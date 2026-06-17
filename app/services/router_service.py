"""Unified assistant routing service."""

from __future__ import annotations

import asyncio
import json
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, LLMClientConfig
from app.services.aiops_service import aiops_service
from app.services.rag_agent_service import rag_agent_service


TIMELINE_EVENT_TYPES = {"agent_event", "tool_event", "decision_event"}
DEFAULT_AIOPS_TIMEOUT_SECONDS = 20.0
SEMANTIC_ROUTER_SYSTEM_PROMPT = (
    "You are a routing classifier for an intelligent operations assistant. "
    "Classify the user request into exactly one route: rag or aiops. "
    "aiops means incidents, alarms, service outage, stuck transaction, slow response, "
    "error-rate increase, abnormal logs, high resource usage, diagnosis, or troubleshooting. "
    "rag means knowledge-base lookup, documentation explanation, concepts, steps, or general Q&A. "
    "Return only compact JSON with keys route and reason, for example: "
    '{"route":"aiops","reason":"service incident"}'
)


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
        llm_client: Any | None = None,
        aiops_timeout_seconds: float = DEFAULT_AIOPS_TIMEOUT_SECONDS,
    ):
        self.semantic_router = semantic_router
        self.llm_client = llm_client
        self.aiops_timeout_seconds = aiops_timeout_seconds

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

        if self.llm_client is None:
            self.llm_client = LLMClient(LLMClientConfig.from_settings(config))
        llm_client = self.llm_client
        response = await llm_client.complete(
            [
                ChatMessage(role="system", content=SEMANTIC_ROUTER_SYSTEM_PROMPT),
                ChatMessage(role="user", content=message),
            ],
            temperature=0,
        )
        result = self._parse_semantic_route_response(response.content)
        return RouteDecision(route=result.route, reason=f"llm_semantic_{result.route}")

    @staticmethod
    def _parse_semantic_route_response(content: str) -> SemanticRouteResult:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or start > end:
                raise
            payload = json.loads(text[start : end + 1])

        return SemanticRouteResult.model_validate(payload)

    async def _resolve_route(self, message: str) -> RouteDecision:
        decision = self.route_message(message)
        if decision.route == "clarify":
            return decision

        # 关键词路由可能误判：含 aiops 关键词的知识类问题（如“解释一下日志格式”）会被
        # 硬路由到 aiops。当两类关键词同时命中（意图歧义）或无明确信号时，交给语义路由判别。
        normalized = message.strip().lower()
        has_aiops = any(keyword in normalized for keyword in self.AIOPS_KEYWORDS)
        has_rag = any(keyword in normalized for keyword in self.RAG_KEYWORDS)
        ambiguous = has_aiops and has_rag

        if decision.reason != "default_rag" and not ambiguous:
            return decision

        try:
            return await self._semantic_route_message(message)
        except Exception as exc:
            logger.warning(f"LLM 语义路由失败，回退到 RAG: {exc}")
            return RouteDecision(route="rag", reason="semantic_route_failed_default_rag")

    async def _iter_aiops_events(self, message: str, session_id: str):
        generator = aiops_service.execute(message, session_id=session_id)
        try:
            async with asyncio.timeout(self.aiops_timeout_seconds):
                async for event in generator:
                    yield event
        finally:
            aclose = getattr(generator, "aclose", None)
            if aclose:
                await aclose()

    def _create_timeout_case(self, message: str, session_id: str, report: str) -> str:
        fallback_case_id = f"case-timeout-{abs(hash((session_id, message))) % 10_000_000}"
        memory_service = getattr(aiops_service, "memory_service", None)
        create_case = getattr(memory_service, "create_case", None)
        complete_case = getattr(memory_service, "complete_case", None)
        if not create_case:
            return fallback_case_id

        try:
            case_id = create_case(session_id=session_id, user_input=message)
            if complete_case:
                complete_case(case_id, executed_steps=[], final_report=report)
            return str(case_id)
        except Exception as exc:
            logger.warning(f"AIOps 超时降级 case 持久化失败: {exc}")
            return fallback_case_id

    def _build_timeout_report(self, message: str) -> str:
        return (
            "# OnCall 降级诊断报告\n\n"
            f"- AIOps 智能体执行超过 {self.aiops_timeout_seconds:g} 秒，已先返回可追踪的降级结果。\n"
            "- 当前前后端链路已连通，诊断时间线与反馈入口可继续使用。\n"
            "- 建议检查 DashScope、MCP、监控/日志数据源状态后重试完整诊断。\n\n"
            f"原始请求：{message}"
        )

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
            seen_event_keys: set[str] = set()

            def append_event(candidate: object) -> None:
                normalized_event = self._normalize_timeline_event(candidate)
                if normalized_event is None:
                    return
                event_key = json.dumps(normalized_event, sort_keys=True, default=str)
                if event_key in seen_event_keys:
                    return
                seen_event_keys.add(event_key)
                events.append(normalized_event)

            try:
                async for event in self._iter_aiops_events(message, session_id=session_id):
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
            except TimeoutError:
                timeout_event = {
                    "type": "agent_event",
                    "agent": "router",
                    "stage": "timeout_fallback",
                    "status": "degraded",
                    "summary": "AIOps 智能体执行超时，已返回降级诊断报告。",
                    "payload": {"timeout_seconds": self.aiops_timeout_seconds},
                }
                append_event(timeout_event)
                final_answer = self._build_timeout_report(message)
                case_id = case_id or self._create_timeout_case(message, session_id, final_answer)
                return {
                    "success": True,
                    "route": "aiops",
                    "route_reason": decision.reason,
                    "case_id": case_id,
                    "answer": final_answer,
                    "events": events,
                    "errorMessage": None,
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
