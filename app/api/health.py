"""健康检查接口"""

import socket
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.memory_cache import get_default_cache

router = APIRouter()
_DEFAULT_AUTH_SECRET = "dev-auth-token-secret"


def _port_reachable(url: str, timeout: float = 0.2) -> bool:
    """检查 URL 对应主机端口是否可达。"""

    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _llm_config_status() -> dict[str, str]:
    llm_key = str(config.llm_api_key or "").strip()
    dash_key = str(config.dashscope_api_key or "").strip()
    llm_configured = bool(
        (llm_key and llm_key not in {"your-api-key-here", "get.env('LLM_API_KEY')"})
        or (dash_key and dash_key not in {"your-api-key-here", "your-dashscope-api-key-here"})
    )
    provider = str(config.embedding_provider or "local_bge_m3").strip().lower()
    if provider in {"local_bge_m3", "bge", "bge3", "local", "bge_m3"}:
        embedding_status = "local_bge_m3"
        embedding_model = str(config.embedding_model or "BAAI/bge-m3")
        embedding_message = "本地 BGE-M3 embedding（无需 DASHSCOPE_API_KEY）"
    elif provider == "dashscope":
        dash_ok = bool(
            dash_key and dash_key not in {"your-api-key-here", "your-dashscope-api-key-here"}
        )
        embedding_status = "configured" if dash_ok else "missing"
        embedding_model = str(
            config.dashscope_embedding_model or config.embedding_model or "text-embedding-v4"
        )
        embedding_message = (
            "DashScope embedding 已配置"
            if dash_ok
            else "EMBEDDING_PROVIDER=dashscope 但 DASHSCOPE_API_KEY 未配置"
        )
    else:
        embedding_status = "unknown"
        embedding_model = str(config.embedding_model or "")
        embedding_message = f"未知 EMBEDDING_PROVIDER={provider}"

    return {
        "status": "configured" if llm_configured else "missing",
        "model": str(config.llm_model or config.dashscope_model or ""),
        "embedding_provider": provider or "local_bge_m3",
        "embedding_model": embedding_model,
        "embedding_dim": str(int(config.embedding_dim or 1024)),
        "embedding_status": embedding_status,
        "message": (
            "LLM 配置已存在" if llm_configured else "LLM_API_KEY / DASHSCOPE_API_KEY 未配置"
        )
        + f"；{embedding_message}",
    }


def _memory_cache_status() -> dict[str, Any]:
    """Surface the long-term memory L1 cache stats on ``/health``.

    The cache is process-local so stats reflect the *current* API process.
    Failures here must never break ``/health`` — the call sites already
    treat the cache as best-effort.
    """

    base = {
        "enabled": bool(config.memory_cache_enabled),
        "max_entries": int(config.memory_cache_max_entries),
        "ttls": {
            "user_preference_seconds": float(config.memory_cache_ttl_user_preference_seconds),
            "experience_seconds": float(config.memory_cache_ttl_experience_seconds),
            "service_knowledge_seconds": float(config.memory_cache_ttl_service_knowledge_seconds),
        },
    }
    try:
        stats = get_default_cache().stats_snapshot()
        base.update(stats)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(f"memory_cache stats snapshot failed: {exc}")
        base["error"] = str(exc)
    return base


def build_health_data() -> dict[str, Any]:
    """构建完整健康检查数据。"""

    health_data: dict[str, Any] = {
        "service": config.app_name,
        "version": config.app_version,
        "status": "healthy",
    }

    try:
        milvus_healthy = milvus_manager.health_check()
        health_data["milvus"] = {
            "status": "connected" if milvus_healthy else "disconnected",
            "message": "Milvus 连接正常" if milvus_healthy else "Milvus 连接异常",
        }
    except Exception as e:
        logger.warning(f"Milvus 健康检查失败: {e}")
        health_data["milvus"] = {
            "status": "error",
            "message": f"Milvus 检查失败: {str(e)}",
        }

    health_data["mcp"] = {
        "cls": {
            "url": config.mcp_cls_url,
            "status": "reachable" if _port_reachable(config.mcp_cls_url) else "unreachable",
            "transport": config.mcp_cls_transport,
        },
        "monitor": {
            "url": config.mcp_monitor_url,
            "status": "reachable" if _port_reachable(config.mcp_monitor_url) else "unreachable",
            "transport": config.mcp_monitor_transport,
        },
    }

    health_data["llm"] = _llm_config_status()
    health_data["rag"] = {
        "collection_name": milvus_manager.COLLECTION_NAME,
        "collection_status": "available"
        if health_data["milvus"]["status"] == "connected"
        else "unavailable",
        "retrieval_mode": config.rag_retrieval_mode,
        "top_k": config.rag_top_k,
        "dense_weight": config.rag_dense_weight,
        "bm25_weight": config.rag_bm25_weight,
    }
    health_data["monitor"] = {
        "target_mode": config.monitor_target_mode,
    }
    health_data["logs"] = {
        "provider": config.log_provider,
    }
    health_data["memory_cache"] = _memory_cache_status()

    if health_data["milvus"]["status"] != "connected":
        health_data["status"] = "unhealthy"
        health_data["error"] = "数据库不可用"

    return health_data


def readiness_issues(health_data: dict[str, Any] | None = None) -> list[str]:
    data = health_data or build_health_data()
    issues: list[str] = []
    if data.get("milvus", {}).get("status") != "connected":
        issues.append("milvus_unavailable")
    if data.get("llm", {}).get("status") != "configured":
        issues.append("llm_not_configured")
    if not config.debug:
        if str(config.auth_token_secret or "").strip() in {"", _DEFAULT_AUTH_SECRET}:
            issues.append("default_auth_secret")
        users = config.auth_user_map
        if users.get("admin") == "admin":
            issues.append("default_admin_credentials")
    return issues


@router.get("/health")
async def health_check():
    """健康检查接口。"""

    health_data = build_health_data()
    status_code = 200 if health_data["status"] == "healthy" else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": "服务运行正常" if status_code == 200 else "服务不可用",
            "data": health_data,
        },
    )


@router.get("/health/live")
async def liveness_check():
    """Process liveness; external dependency failures do not fail this probe."""
    return {"code": 200, "message": "alive", "data": {"status": "alive"}}


@router.get("/health/readiness")
async def readiness_check():
    """Traffic readiness including dependencies and non-debug security defaults."""
    data = build_health_data()
    issues = readiness_issues(data)
    status_code = 200 if not issues else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": "ready" if not issues else "not ready",
            "data": {"status": "ready" if not issues else "not_ready", "issues": issues},
        },
    )
