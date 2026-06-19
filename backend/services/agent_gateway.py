from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from app.services.rag_agent_service import rag_agent_service
from backend.models import AgentMode
from backend.services.agent_router import AgentRouter


class AgentGatewayService:
    """Stream normalized frontend events from the selected agent service.

    Legacy gateway: only the RAG lane remains. The old OnCall pipeline lane was
    removed; the live operational path is /api/assistant -> RouterService.
    """

    def __init__(
        self,
        router: AgentRouter | None = None,
        rag_service: Any | None = None,
    ):
        self.router = router or AgentRouter()
        self.rag_service = rag_service or rag_agent_service

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

        async for event in self._stream_rag(message=message, session_id=session_id):
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


agent_gateway_service = AgentGatewayService()
