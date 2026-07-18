"""FastAPI 应用入口

主应用程序，配置路由、中间件、静态文件等
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api import assistant, auth, checkpoint, conversations, file, health, hitl, memory
from app.config import config
from app.core.llm_client import close_default_llm_client, get_default_llm_client
from app.core.metrics import setup_metrics
from app.core.milvus_client import milvus_manager
from app.services.redis_client import redis_lifespan

_DEFAULT_AUTH_TOKEN_SECRET = "dev-auth-token-secret"


def _log_auth_startup_checks() -> None:
    """Surface auth misconfiguration early without printing secrets."""

    user_count = len(config.auth_user_map)
    ttl = int(getattr(config, "auth_token_ttl_seconds", 0) or 0)
    if user_count == 0:
        logger.error(
            "AUTH_USERS is empty — every login will be rejected. "
            "Set AUTH_USERS=user:pass[,user2:pass2] before accepting traffic."
        )
    else:
        logger.info("Auth accounts loaded: {} user(s); token TTL={}s", user_count, ttl)

    secret = (config.auth_token_secret or "").strip()
    if not secret:
        logger.error("AUTH_TOKEN_SECRET is empty — access tokens cannot be verified safely.")
    elif secret == _DEFAULT_AUTH_TOKEN_SECRET and not config.debug:
        logger.warning(
            "AUTH_TOKEN_SECRET is still the default dev value. "
            "Override it in any shared/pilot environment; rotating the secret "
            "invalidates every previously issued token and forces re-login."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("=" * 60)
    logger.info(f"🚀 {config.app_name} v{config.app_version} 启动中...")
    logger.info(f"📝 环境: {'开发' if config.debug else '生产'}")
    logger.info(f"🌐 监听地址: http://{config.host}:{config.port}")
    logger.info(f"📚 API 文档: http://{config.host}:{config.port}/docs")

    _log_auth_startup_checks()
    redis_context = redis_lifespan()
    await redis_context.__aenter__()

    # 连接 Milvus
    logger.info("🔌 正在连接 Milvus...")
    try:
        milvus_manager.connect()
        logger.info("✅ Milvus 连接成功")
    except RuntimeError as e:
        logger.warning(
            "Milvus startup connect failed; continuing without vector search until it is available: {}",
            e,
        )

    # 预热共享 LLMClient（连接池复用，省掉首次 expert 启动的 TCP+TLS 握手）
    try:
        await get_default_llm_client()
        logger.info("✅ 共享 LLMClient 预热完成")
    except Exception as e:
        logger.warning("LLMClient 预热失败（按需懒加载）：{}", e)

    # 预热 MCP 客户端（避免首次 expert 启动时再冷启 5~8s）
    try:
        from app.agent.mcp_client import get_mcp_client_with_retry

        await get_mcp_client_with_retry()
        logger.info("✅ MCP 客户端预热完成")
    except Exception as e:
        logger.warning("MCP 客户端预热失败（按需懒加载）：{}", e)

    logger.info("=" * 60)

    yield

    # 关闭时执行
    logger.info("🔌 正在关闭 LLMClient 连接池...")
    try:
        await close_default_llm_client()
    except Exception as e:
        logger.warning("LLMClient shutdown failed: {}", e)
    logger.info("🔌 正在关闭 Milvus 连接...")
    try:
        milvus_manager.close()
    except Exception as e:
        logger.warning("Milvus close failed during shutdown: {}", e)
    await redis_context.__aexit__(None, None, None)
    logger.info(f"👋 {config.app_name} 关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title=config.app_name, version=config.app_version, description="智能运维系统", lifespan=lifespan
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 暴露 Prometheus /metrics（供 Prometheus 抓取，打通真实指标链路）
setup_metrics(app)

# 注册路由
app.include_router(health.router, tags=["健康检查"])
app.include_router(assistant.router, prefix="/api", tags=["统一助手"])
app.include_router(conversations.router, prefix="/api", tags=["会话历史"])
app.include_router(file.router, prefix="/api", tags=["文件管理"])
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(memory.router, prefix="/api", tags=["long-term-memory"])
app.include_router(hitl.router, prefix="/api", tags=["hitl"])
app.include_router(checkpoint.router, prefix="/api", tags=["harness-checkpoint"])

# 挂载静态文件
static_dir = "static"
if config.static_serve_enabled:
    if not os.path.isdir(static_dir):
        raise RuntimeError("STATIC_SERVE_ENABLED=true but static directory is missing")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    """返回首页"""
    index_path = os.path.join(static_dir, "index.html")
    if config.static_serve_enabled and os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "message": f"Welcome to {config.app_name} API",
        "version": config.app_version,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app", host=config.host, port=config.port, reload=config.debug, log_level="info"
    )
