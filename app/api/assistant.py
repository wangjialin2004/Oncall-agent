"""Unified assistant API (SSE streaming over the Router + Expert Agents)."""

import inspect
import json

from fastapi import APIRouter, Depends
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.models.request import ChatRequest
from app.services.conversation_service import conversation_service
from app.services.router_service import router_service
from app.services.session_scope_service import require_session_owner, scope_session_id

router = APIRouter()


def _persist_turn(owner_key: str, request: ChatRequest, event: dict) -> None:
    """Best-effort: store the completed turn for multi-turn history. Never raises."""
    try:
        conversation_service.append_turn(
            owner_key=owner_key,
            session_id=request.id,
            user_message=request.question,
            assistant_answer=str(event.get("answer") or ""),
            route=str(event.get("route") or ""),
            case_id=str(event.get("case_id") or ""),
            events=list(event.get("events") or []),
        )
    except Exception as exc:  # pragma: no cover - persistence must not break the stream
        logger.warning(f"[会话 {request.id}] 会话持久化失败（已忽略）: {exc}")


@router.post("/assistant")
async def assistant(
    request: ChatRequest,
    owner_key: str = Depends(require_session_owner),
):
    scoped_session_id = scope_session_id(request.id, owner_key)
    logger.info(f"[会话 {request.id}] 收到统一助手请求: {request.question}")

    async def event_generator():
        try:
            stream_kwargs = {"session_id": scoped_session_id}
            if "owner_key" in inspect.signature(router_service.stream).parameters:
                stream_kwargs["owner_key"] = owner_key
            async for event in router_service.stream(request.question, **stream_kwargs):
                yield {"event": "message", "data": json.dumps(event, ensure_ascii=False, default=str)}
                event_type = event.get("type")
                if event_type == "complete":
                    _persist_turn(owner_key, request, event)
                if event_type in {"complete", "error"}:
                    break
        except Exception as exc:
            logger.error(f"统一助手接口错误: {exc}", exc_info=True)
            yield {
                "event": "message",
                "data": json.dumps(
                    {"type": "error", "route": "error", "message": str(exc)},
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())
