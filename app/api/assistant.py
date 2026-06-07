"""Unified assistant API."""

from fastapi import APIRouter
from loguru import logger

from app.models.request import ChatRequest
from app.services.router_service import router_service

router = APIRouter()


@router.post("/assistant")
async def assistant(request: ChatRequest):
    try:
        logger.info(f"[会话 {request.id}] 收到统一助手请求: {request.question}")
        data = await router_service.answer(request.question, session_id=request.id)
        return {
            "code": 200,
            "message": "success",
            "data": data,
        }
    except Exception as exc:
        logger.error(f"统一助手接口错误: {exc}")
        return {
            "code": 500,
            "message": "error",
            "data": {
                "success": False,
                "route": "error",
                "answer": None,
                "errorMessage": str(exc),
            },
        }
