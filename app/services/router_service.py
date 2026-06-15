"""Unified assistant routing service."""

from __future__ import annotations

import json
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config
from app.services.aiops_service import aiops_service
from app.services.rag_agent_service import rag_agent_service


TIMELINE_EVENT_TYPES = {"agent_event", "tool_event", "decision_event"}


@dataclass(slots=True)
class RouteDecision:
    route: str
    reason: str


class SemanticRouteResult(BaseModel):
    route: Literal["rag", "aiops"] = Field(description="The best downstream route.")
    reason: str = Field(description="Short Chinese reason for the route decision.")


SemanticRouter = Callable[[str], RouteDecision | Awaitable[RouteDecision]]


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

    def __init__(
        self,
        semantic_router: SemanticRouter | None = None,
        semantic_model: ChatQwen | None = None,
    ):
        self.semantic_router = semantic_router
        self.semantic_model = semantic_model

    @staticmethod
    def _normalize_timeline_event(event: object) -> dict[str, object] | None:
        if not isinstance(event, dict):
            return None
        event_type = event.get("type")
        if event_type not in TIMELINE_EVENT_TYPES:
            return None
        return dict(event)

    def route_message(self, message: str) -> RouteDecision:
        normalized = message.strip().lower()
        if not normalized:
            return RouteDecision(route="clarify", reason="empty_message")
        if not any(char.isalnum() for char in normalized):
            return RouteDecision(route="clarify", reason="no_meaningful_text")

        if any(keyword in normalized for keyword in self.AIOPS_KEYWORDS):
            return RouteDecision(route="aiops", reason="matched_aiops_keyword")

        if any(keyword in normalized for keyword in self.RAG_KEYWORDS):
            return RouteDecision(route="rag", reason="matched_rag_keyword")

        return RouteDecision(route="rag", reason="default_rag")

    async def _semantic_route_message(self, message: str) -> RouteDecision:
        if self.semantic_router:
            result = self.semantic_router(message)
            if inspect.isawaitable(result):
                result = await result
            return RouteDecision(route=result.route, reason=result.reason)

        model = self.semantic_model or ChatQwen(
            model=config.rag_model,
            api_key=config.dashscope_api_key,
            temperature=0,
            streaming=False,
        )
        classifier = model.with_structured_output(SemanticRouteResult)
        result = await classifier.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是智能运维助手的路由器，只能把用户请求分类为 rag 或 aiops。\n"
                        "aiops: 故障、告警、服务挂了、下单转圈、响应变慢、错误率上升、"
                        "日志异常、资源飙高、需要诊断或排障。\n"
                        "rag: 查询知识库、文档说明、概念解释、步骤咨询、普通问答。\n"
                        "示例: '服务挂了' -> aiops; '下单一直转圈' -> aiops; "
                        "'怎么处理慢响应' -> rag; '解释一下 CPU 高的原因' -> rag。"
                    )
                ),
                HumanMessage(content=message),
            ]
        )
        return RouteDecision(route=result.route, reason=f"llm_semantic_{result.route}")

    async def _resolve_route(self, message: str) -> RouteDecision:
        decision = self.route_message(message)
        if decision.reason != "default_rag":
            return decision

        try:
            return await self._semantic_route_message(message)
        except Exception as exc:
            logger.warning(f"LLM 语义路由失败，回退到 RAG: {exc}")
            return RouteDecision(route="rag", reason="semantic_route_failed_default_rag")

    async def answer(self, message: str, session_id: str) -> dict[str, object]:
        decision = await self._resolve_route(message)
        if decision.route == "clarify":
            return {
                "success": True,
                "route": "clarify",
                "route_reason": decision.reason,
                "answer": "请补充你想咨询的问题，或说明需要诊断的服务、告警、日志现象。",
                "errorMessage": None,
            }

        if decision.route == "aiops":
            final_answer = ""
            case_id = ""
            events: list[dict[str, object]] = []
            seen_event_keys: set[tuple[tuple[str, object], ...]] = set()

            def append_event(candidate: object) -> None:
                normalized_event = self._normalize_timeline_event(candidate)
                if normalized_event is None:
                    return
                event_key = json.dumps(normalized_event, sort_keys=True, default=str)
                if event_key in seen_event_keys:
                    return
                seen_event_keys.add(event_key)
                events.append(normalized_event)

            async for event in aiops_service.execute(message, session_id=session_id):
                event_type = event.get("type")
                if event_type in TIMELINE_EVENT_TYPES:
                    append_event(event)
                elif event_type == "complete":
                    case_id = str(event.get("case_id") or "")
                    final_answer = str(event.get("response") or event.get("message") or "")
                    for final_event in event.get("events") or []:
                        append_event(final_event)
                elif event_type == "error":
                    return {
                        "success": False,
                        "route": "aiops",
                        "route_reason": decision.reason,
                        "case_id": str(event.get("case_id") or ""),
                        "answer": None,
                        "events": events,
                        "errorMessage": str(event.get("message") or event.get("response") or ""),
                    }
            return {
                "success": True,
                "route": "aiops",
                "route_reason": decision.reason,
                "case_id": case_id,
                "answer": final_answer,
                "events": events,
                "errorMessage": None,
            }

        answer = await rag_agent_service.query(message, session_id=session_id)
        return {
            "success": True,
            "route": "rag",
            "route_reason": decision.reason,
            "answer": answer,
            "errorMessage": None,
        }


router_service = RouterService()
