from __future__ import annotations

import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from backend.models import AgentStreamRequest
from backend.services.agent_gateway import agent_gateway_service

router = APIRouter()


@router.post("/agent/stream")
async def stream_agent(request: AgentStreamRequest):
    async def event_generator():
        try:
            async for event in agent_gateway_service.stream(
                message=request.message,
                session_id=request.session_id,
                mode=request.mode,
            ):
                yield {
                    "event": "message",
                    "data": json.dumps(event, ensure_ascii=False),
                }
                if event.get("type") in {"complete", "error"}:
                    break
        except Exception as exc:
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "route": "unknown",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())
