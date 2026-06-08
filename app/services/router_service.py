"""Unified assistant routing service."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.aiops_service import aiops_service
from app.services.rag_agent_service import rag_agent_service


@dataclass(slots=True)
class RouteDecision:
    route: str
    reason: str


class RouterService:
    """Route user messages to RAG chat, AIOps diagnosis, or clarification."""

    AIOPS_KEYWORDS = (
        "告警",
        "故障",
        "异常",
        "诊断",
        "日志",
        "cpu",
        "内存",
        "不可用",
        "失败",
        "报警",
        "error",
        "failed",
    )
    RAG_KEYWORDS = (
        "文档",
        "知识库",
        "说明",
        "怎么",
        "步骤",
        "处理",
        "解释",
        "是什么",
    )

    def route_message(self, message: str) -> RouteDecision:
        normalized = message.strip().lower()
        if not normalized:
            return RouteDecision(route="clarify", reason="empty_message")

        if any(keyword in normalized for keyword in self.AIOPS_KEYWORDS):
            return RouteDecision(route="aiops", reason="matched_aiops_keyword")

        if any(keyword in normalized for keyword in self.RAG_KEYWORDS):
            return RouteDecision(route="rag", reason="matched_rag_keyword")

        return RouteDecision(route="rag", reason="default_rag")

    async def answer(self, message: str, session_id: str) -> dict[str, object]:
        decision = self.route_message(message)
        if decision.route == "clarify":
            return {
                "success": True,
                "route": "clarify",
                "answer": "请补充你想咨询的问题，或说明需要诊断的服务、告警、日志现象。",
                "errorMessage": None,
            }

        if decision.route == "aiops":
            final_answer = ""
            case_id = ""
            async for event in aiops_service.execute(message, session_id=session_id):
                if event.get("type") == "complete":
                    case_id = str(event.get("case_id") or "")
                    final_answer = str(event.get("response") or event.get("message") or "")
                elif event.get("type") == "error":
                    return {
                        "success": False,
                        "route": "aiops",
                        "case_id": str(event.get("case_id") or ""),
                        "answer": None,
                        "errorMessage": str(event.get("message") or event.get("response") or ""),
                    }
            return {
                "success": True,
                "route": "aiops",
                "case_id": case_id,
                "answer": final_answer,
                "errorMessage": None,
            }

        answer = await rag_agent_service.query(message, session_id=session_id)
        return {
            "success": True,
            "route": "rag",
            "answer": answer,
            "errorMessage": None,
        }


router_service = RouterService()
