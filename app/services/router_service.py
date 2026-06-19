"""Router + Expert Agents service.

A flat multi-agent router: classify each request into exactly one of five expert
routes (knowledge / metric / log / change / diagnosis) plus ``clarify``, then
stream that expert's normalized events to the caller. Routing is a two-stage
process — a fast per-category keyword path, then an LLM semantic classifier for
ambiguous or signal-less inputs.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from app.agent.events import make_agent_event, make_route_event
from app.agent.experts.registry import DEFAULT_ROUTE, EXPERT_ROUTES, get_expert
from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, LLMClientConfig
from app.services.user_preference_service import user_preference_service

# Events persisted into the final response timeline (everything except content/complete).
TIMELINE_EVENT_TYPES = {"route_event", "agent_event", "tool_event", "decision_event"}
DEFAULT_EXPERT_TIMEOUT_SECONDS = 60.0

SEMANTIC_ROUTER_SYSTEM_PROMPT = (
    "You are a routing classifier for an intelligent operations (OnCall) assistant. "
    "Classify the user request into exactly one route:\n"
    "- knowledge: documentation / concept / how-to / knowledge-base Q&A\n"
    "- metric: alerts, monitoring metrics, CPU/memory/disk/latency/error-rate/resource usage\n"
    "- log: log inspection, error logs, exception stacks, log analysis\n"
    "- change: recent deploys/releases, config changes, rollbacks, change tickets\n"
    "- diagnosis: complex or cross-domain incident root-cause analysis / troubleshooting\n"
    "When unsure or the request spans multiple domains, prefer diagnosis. "
    "Return only compact JSON with keys route, reason (short Chinese), and confidence "
    '(0..1), e.g. {"route":"metric","reason":"询问告警","confidence":0.9}'
)


@dataclass(slots=True)
class RouteDecision:
    route: str
    reason: str
    confidence: float = 1.0


class SemanticRouteResult(BaseModel):
    route: Literal["knowledge", "metric", "log", "change", "diagnosis"] = Field(
        description="The best downstream expert route."
    )
    reason: str = Field(description="Short Chinese reason for the route decision.")
    confidence: float = Field(default=0.5, description="Confidence in [0, 1].")


SemanticRouter = Callable[[str], RouteDecision | Awaitable[RouteDecision]]


class RouterService:
    """Route user messages to one expert agent and stream its events."""

    # Per-category keyword fast path. Single-category hit → decide immediately;
    # multi-category or no hit → fall through to the semantic classifier.
    KEYWORDS: dict[str, tuple[str, ...]] = {
        "metric": (
            "告警", "报警", "指标", "监控", "cpu", "内存", "memory", "磁盘", "disk",
            "延迟", "耗时", "错误率", "资源", "负载", "qps", "prometheus", "水位",
        ),
        "log": (
            "日志", "log", "报错", "堆栈", "异常栈", "traceback", "stacktrace",
            "错误日志", "栈信息",
        ),
        "change": (
            "变更", "发布", "上线", "部署", "deploy", "release", "回滚", "rollback",
            "配置变更", "工单", "灰度",
        ),
        "knowledge": (
            "文档", "知识库", "说明", "怎么", "如何", "步骤", "是什么", "解释",
            "介绍", "含义", "定义",
        ),
        "diagnosis": (
            "故障", "诊断", "根因", "排查", "不可用", "宕机", "全面分析", "综合",
            "为什么", "挂了", "全链路",
        ),
    }

    def __init__(
        self,
        semantic_router: SemanticRouter | None = None,
        llm_client: Any | None = None,
        expert_timeout_seconds: float | None = None,
        min_confidence: float | None = None,
    ):
        self.semantic_router = semantic_router
        self.llm_client = llm_client
        self.expert_timeout_seconds = (
            expert_timeout_seconds
            if expert_timeout_seconds is not None
            else float(getattr(config, "expert_timeout_seconds", DEFAULT_EXPERT_TIMEOUT_SECONDS))
        )
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else float(getattr(config, "router_min_confidence", 0.55))
        )

    # ------------------------------------------------------------------ routing

    def _matched_categories(self, normalized: str) -> list[str]:
        return [
            route
            for route, keywords in self.KEYWORDS.items()
            if any(keyword in normalized for keyword in keywords)
        ]

    def route_message(self, message: str) -> RouteDecision:
        """Keyword fast path (synchronous). Returns clarify / a single route / default."""
        normalized = message.strip().lower()
        if not normalized:
            return RouteDecision(route="clarify", reason="empty_message", confidence=1.0)
        if not any(char.isalnum() for char in normalized):
            return RouteDecision(route="clarify", reason="no_meaningful_text", confidence=1.0)

        matched = self._matched_categories(normalized)
        if len(matched) == 1:
            route = matched[0]
            return RouteDecision(route=route, reason=f"matched_{route}_keyword", confidence=0.9)
        if not matched:
            return RouteDecision(route=DEFAULT_ROUTE, reason="default_no_keyword", confidence=0.3)
        # Multiple categories matched → ambiguous, defer to semantic routing.
        return RouteDecision(route=DEFAULT_ROUTE, reason="ambiguous_keywords", confidence=0.3)

    async def _semantic_route_message(self, message: str) -> RouteDecision:
        if self.semantic_router:
            result = self.semantic_router(message)
            if inspect.isawaitable(result):
                result = await result
            return RouteDecision(
                route=result.route,
                reason=result.reason,
                confidence=getattr(result, "confidence", 1.0),
            )

        if self.llm_client is None:
            self.llm_client = LLMClient(LLMClientConfig.from_settings(config))
        response = await self.llm_client.complete(
            [
                ChatMessage(role="system", content=SEMANTIC_ROUTER_SYSTEM_PROMPT),
                ChatMessage(role="user", content=message),
            ],
            temperature=0,
        )
        result = self._parse_semantic_route_response(response.content)
        return RouteDecision(
            route=result.route,
            reason=f"llm_semantic_{result.route}",
            confidence=result.confidence,
        )

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
        # High-confidence single-keyword hit: trust it.
        if decision.confidence >= 0.9 and decision.reason.startswith("matched_"):
            return decision

        # Ambiguous or signal-less: ask the semantic classifier.
        try:
            semantic = await self._semantic_route_message(message)
        except Exception as exc:
            logger.warning(f"LLM 语义路由失败，回退到综合诊断: {exc}")
            return RouteDecision(
                route=DEFAULT_ROUTE, reason="semantic_route_failed_default_diagnosis", confidence=0.0
            )

        if semantic.route not in EXPERT_ROUTES:
            return RouteDecision(route=DEFAULT_ROUTE, reason="semantic_unknown_route", confidence=0.0)
        if semantic.confidence < self.min_confidence:
            return RouteDecision(
                route=DEFAULT_ROUTE,
                reason=f"low_confidence_{semantic.route}_default_diagnosis",
                confidence=semantic.confidence,
            )
        return semantic

    # ------------------------------------------------------------------ streaming

    async def stream(self, message: str, session_id: str, owner_key: str = ""):
        """Stream route + expert events, ending with a ``complete`` event."""
        decision = await self._resolve_route(message)
        route_event = make_route_event(
            route=decision.route,
            reason=decision.reason,
            confidence=decision.confidence,
            candidates=list(EXPERT_ROUTES),
            trace_id=session_id,
        )
        yield route_event

        if decision.route == "clarify":
            clarify_text = "请补充你想咨询的问题，或说明需要诊断的服务、告警、日志现象、近期变更。"
            yield {"type": "content", "data": clarify_text}
            yield {
                "type": "complete",
                "route": "clarify",
                "route_reason": decision.reason,
                "answer": clarify_text,
                "case_id": "",
                "events": [route_event],
            }
            return

        expert = get_expert(decision.route)
        preference_context = (
            user_preference_service.format_for_prompt(owner_key)
            if owner_key and config.user_preferences_enabled
            else ""
        )
        events: list[dict[str, Any]] = [route_event]
        answer_parts: list[str] = []
        case_id = ""

        async def _emit(event: dict[str, Any]) -> None:
            event_type = event.get("type")
            if event_type in TIMELINE_EVENT_TYPES:
                events.append(event)

        try:
            async for event in self._iter_expert(
                expert, message, session_id, context=preference_context
            ):
                event_type = event.get("type")
                if event_type == "content":
                    answer_parts.append(str(event.get("data") or ""))
                else:
                    await _emit(event)
                    found = event.get("case_id") or (
                        event.get("payload", {}).get("case_id") if isinstance(event.get("payload"), dict) else None
                    )
                    if found:
                        case_id = str(found)
                yield event
        except TimeoutError:
            logger.warning(f"专家 {decision.route} 执行超时 {self.expert_timeout_seconds}s，返回降级答案")
            timeout_event = make_agent_event(
                agent="router",
                stage="timeout_fallback",
                status="degraded",
                summary=f"{decision.route} 专家执行超时，已返回降级结果。",
                payload={"timeout_seconds": self.expert_timeout_seconds},
                trace_id=session_id,
            )
            events.append(timeout_event)
            yield timeout_event
            fallback = self._build_timeout_report(message, decision.route)
            answer_parts.append(fallback)
            yield {"type": "content", "data": fallback}

        answer = "".join(answer_parts)
        yield {
            "type": "complete",
            "route": decision.route,
            "route_reason": decision.reason,
            "answer": answer,
            "case_id": case_id,
            "events": events,
        }

    async def _iter_expert(self, expert: Any, message: str, session_id: str, context: str = ""):
        kwargs = {"message": message, "session_id": session_id, "trace_id": session_id}
        try:
            if "context" in inspect.signature(expert.run).parameters:
                kwargs["context"] = context
        except (TypeError, ValueError):
            pass
        generator = expert.run(**kwargs)
        try:
            async with asyncio.timeout(self.expert_timeout_seconds):
                async for event in generator:
                    yield event
        finally:
            aclose = getattr(generator, "aclose", None)
            if aclose:
                await aclose()

    def _build_timeout_report(self, message: str, route: str) -> str:
        return (
            "# 降级响应\n\n"
            f"- {route} 专家执行超过 {self.expert_timeout_seconds:g} 秒，已先返回可追踪的降级结果。\n"
            "- 前后端链路与时间线可继续使用，请检查数据源（LLM、MCP、监控/日志）后重试。\n\n"
            f"原始请求：{message}"
        )

    # --------------------------------------------------------------- non-stream

    async def answer(
        self, message: str, session_id: str, owner_key: str = ""
    ) -> dict[str, object]:
        """Aggregate the stream into a single response (backward-compatible shape)."""
        route = DEFAULT_ROUTE
        route_reason = ""
        answer_text = ""
        case_id = ""
        events: list[dict[str, object]] = []

        async for event in self.stream(message, session_id=session_id, owner_key=owner_key):
            if event.get("type") == "complete":
                route = str(event.get("route") or route)
                route_reason = str(event.get("route_reason") or route_reason)
                answer_text = str(event.get("answer") or "")
                case_id = str(event.get("case_id") or "")
                events = list(event.get("events") or [])

        return {
            "success": True,
            "route": route,
            "route_reason": route_reason,
            "case_id": case_id,
            "answer": answer_text,
            "events": events,
            "errorMessage": None,
        }


router_service = RouterService()
