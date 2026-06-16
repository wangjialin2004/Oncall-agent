"""Unified assistant API."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from loguru import logger

from app.models.request import ChatRequest
from app.services.router_service import router_service
from app.services.session_scope_service import require_session_owner, scope_session_id

router = APIRouter()


@router.post("/assistant")
async def assistant(
    request: ChatRequest,
    owner_key: str = Depends(require_session_owner),
):
    try:
        scoped_session_id = scope_session_id(request.id, owner_key)
        logger.info(f"[会话 {request.id}] 收到统一助手请求: {request.question}")
        data = await router_service.answer(request.question, session_id=scoped_session_id)
        return {
            "code": 200,
            "message": "success",
            "data": data,
        }
    except Exception as exc:
        logger.error(f"统一助手接口错误: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "message": "error",
                "data": {
                    "success": False,
                    "route": "error",
                    "answer": None,
                    "errorMessage": str(exc),
                },
            },
        )
