from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from app.services.aiops_service import aiops_service
from app.services.rag_agent_service import rag_agent_service
from backend.models import AgentMode
from backend.services.agent_router import AgentRouter

TIMELINE_EVENT_TYPES = {"agent_event", "tool_event", "decision_event"}


class AgentGatewayService:
    """Stream normalized frontend events from the selected agent service."""

    def __init__(
        self,
        router: AgentRouter | None = None,
        rag_service: Any | None = None,
        oncall_service: Any | None = None,
    ):
        self.router = router or AgentRouter()
        self.rag_service = rag_service or rag_agent_service
        self.oncall_service = oncall_service or aiops_service

    async def stream(
        self,
        *,
        message: str,
        session_id: str,
        mode: AgentMode,
    ) -> AsyncGenerator[dict[str, Any], None]:
        route = self.router.resolve_route(message=message, mode=mode)
        yield {
            "type": "route_selected",
            "route": route.route,
            "reason": route.reason,
            "mode": mode,
        }

        if route.route == "rag":
            async for event in self._stream_rag(message=message, session_id=session_id):
                yield event
            return

        async for event in self._stream_oncall(message=message, session_id=session_id):
            yield event

    async def _stream_rag(
        self,
        *,
        message: str,
        session_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        final_answer = ""
        async for chunk in self.rag_service.query_stream(message, session_id=session_id):
            chunk_type = chunk.get("type")
            if chunk_type == "content":
                data = str(chunk.get("data") or "")
                final_answer += data
                yield {"type": "content", "data": data}
            elif chunk_type == "complete":
                data = chunk.get("data")
                if isinstance(data, dict):
                    final_answer = str(data.get("answer") or final_answer)
                yield {
                    "type": "complete",
                    "route": "rag",
                    "answer": final_answer,
                    "case_id": "",
                    "events": [],
                }
            elif chunk_type == "error":
                yield {"type": "error", "route": "rag", "message": str(chunk.get("data") or "")}

    async def _stream_oncall(
        self,
        *,
        message: str,
        session_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self.oncall_service.execute(message, session_id=session_id):
            event_type = event.get("type")
            if event_type in TIMELINE_EVENT_TYPES:
                yield dict(event)
            elif event_type == "report":
                yield {
                    "type": "report",
                    "route": "oncall",
                    "case_id": str(event.get("case_id") or ""),
                    "report": str(event.get("report") or ""),
                }
            elif event_type == "complete":
                case_id = str(event.get("case_id") or "")
                answer = str(event.get("response") or event.get("message") or "")
                events = event.get("events") if isinstance(event.get("events"), list) else []
                yield {
                    "type": "report",
                    "route": "oncall",
                    "case_id": case_id,
                    "report": answer,
                }
                yield {
                    "type": "complete",
                    "route": "oncall",
                    "answer": answer,
                    "case_id": case_id,
                    "events": events,
                }
            elif event_type == "error":
                yield {
                    "type": "error",
                    "route": "oncall",
                    "case_id": str(event.get("case_id") or ""),
                    "message": str(event.get("message") or "OnCall execution failed"),
                }


agent_gateway_service = AgentGatewayService()
