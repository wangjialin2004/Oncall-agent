"""健康检查接口"""

import socket
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager

router = APIRouter()


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
    configured = bool(config.dashscope_api_key and config.dashscope_api_key != "your-api-key-here")
    return {
        "status": "configured" if configured else "missing",
        "model": config.dashscope_model,
        "embedding_model": config.dashscope_embedding_model,
        "message": "LLM 配置已存在" if configured else "DASHSCOPE_API_KEY 未配置",
    }


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
        "collection_status": "available" if health_data["milvus"]["status"] == "connected" else "unavailable",
        "retrieval_mode": "dense",
        "top_k": config.rag_top_k,
    }

    if health_data["milvus"]["status"] != "connected":
        health_data["status"] = "unhealthy"
        health_data["error"] = "数据库不可用"

    return health_data


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
