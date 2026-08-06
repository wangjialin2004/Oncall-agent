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
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from app.agent.events import make_agent_event, make_route_event
from app.agent.experts.registry import DEFAULT_ROUTE, EXPERT_ROUTES, get_expert
from app.agent.stream_common import CLARIFY_TEXT, TIMELINE_EVENT_TYPES, build_timeout_report
from app.config import config
from app.core.llm_client import ChatMessage, new_llm_client
from app.services.user_preference_service import user_preference_service

DEFAULT_EXPERT_TIMEOUT_SECONDS = 60.0

SEMANTIC_ROUTER_SYSTEM_PROMPT = (
    "You are a routing classifier for an intelligent operations (OnCall) assistant. "
    "Classify the user request into a primary route, and optionally list parallel secondary "
    "routes when the request clearly spans multiple expert domains:\n"
    "- knowledge: documentation, concepts, generic how-to, runbooks, operating procedures, "
    "and knowledge-base Q&A. Use this for generic questions such as how to solve high CPU "
    "usage when no concrete target or time window is provided.\n"
    "- metric: current/live alerts, monitoring metric lookup, Prometheus queries, CPU/memory/"
    "disk/latency/error-rate checks for a concrete host, service, pod, instance, or time window.\n"
    "- log: log inspection, error logs, exception stacks, log analysis\n"
    "- change: recent deploys/releases, config changes, rollbacks, change tickets\n"
    "- diagnosis: complex or cross-domain incident root-cause analysis / troubleshooting. "
    "When picked as primary, do not also list it in aux_routes.\n"
    "aux_routes (optional) lists other expert routes that the user also clearly needs in "
    "parallel, e.g. a request that simultaneously wants metric evidence and log evidence. "
    "Leave it empty for single-intent requests. Never include the primary route itself, "
    "never include 'diagnosis', and never invent a route outside the list above.\n"
    "Business priority rules:\n"
    "1. If the user asks for a generic solution, steps, explanation, or best practice "
    "without a specific observed target/time range, prefer knowledge even if resource words "
    "such as CPU, memory, disk, latency, or error rate appear.\n"
    "2. Choose metric only when the user wants to inspect current monitoring data, active "
    "alerts, dashboards, Prometheus, or provides a concrete target/time range.\n"
    "3. Choose diagnosis when the request describes an ongoing incident, business impact, "
    "multiple symptoms, or asks for root-cause troubleshooting across domains.\n"
    "When unsure or the request spans multiple domains, prefer diagnosis. "
    "Return only compact JSON with keys route, reason (short Chinese), confidence (0..1), "
    'and optional aux_routes (string array), e.g. '
    '{"route":"metric","reason":"询问告警","confidence":0.9} or '
    '{"route":"diagnosis","reason":"多症状","confidence":0.8,"aux_routes":["metric","log"]}'
)


@dataclass(slots=True)
class RouteDecision:
    route: str
    reason: str
    confidence: float = 1.0
    hints: tuple[str, ...] = ()
    aux_routes: tuple[str, ...] = ()
    intent_relation: str = "primary"


class SemanticRouteResult(BaseModel):
    route: Literal["knowledge", "metric", "log", "change", "diagnosis"] = Field(
        description="The best downstream expert route."
    )
    reason: str = Field(description="Short Chinese reason for the route decision.")
    confidence: float = Field(default=0.5, description="Confidence in [0, 1].")
    aux_routes: list[str] = Field(
        default_factory=list,
        description=(
            "Optional secondary expert routes observed in parallel with the primary. "
            "Empty when the request is single-intent. Must not include the primary route "
            "itself or the fallback 'diagnosis' expert."
        ),
    )


SemanticRouter = Callable[..., RouteDecision | Awaitable[RouteDecision]]


class RouterService:
    """Route user messages to one expert agent and stream its events."""

    _CONCRETE_OPERATIONAL_TARGET = re.compile(
        r"(?:\b[a-z0-9][a-z0-9._-]*(?:api|service|server|worker|pod)\b|[\u4e00-\u9fff]{2,}(?:服务|接口|实例|节点))",
        re.IGNORECASE,
    )
    _INCIDENT_SIGNALS = (
        "告警",
        "不可用",
        "故障",
        "异常",
        "错误率",
        "健康检查失败",
        "超时",
        "宕机",
        "崩溃",
        "cpu",
        "memory",
        "内存",
        "磁盘",
        "延迟",
        "响应时间",
    )
    # Exact-match short continuations that should inherit the previous turn route.
    _CONTINUATION_PHRASES = frozenset(
        {
            "继续",
            "接着",
            "接着说",
            "继续说",
            "然后呢",
            "还有呢",
            "再说详细点",
            "详细点",
            "展开说说",
            "再详细点",
            "继续讲",
            "go on",
            "continue",
            "more details",
            "more detail",
            "tell me more",
        }
    )
    _OPERATIONAL_HINT_ROUTES = frozenset({"metric", "log", "change", "diagnosis"})

    # Strong keywords keep the low-latency fast path. Weak keywords only hint
    # semantic routing so generic how-to questions are not hijacked by one word.
    STRONG_KEYWORDS: dict[str, tuple[str, ...]] = {
        "metric": (
            "告警", "报警", "指标", "监控", "错误率", "qps", "prometheus", "水位",
        ),
        "log": (
            "日志", "log", "堆栈", "异常栈", "traceback", "stacktrace",
            "错误日志", "栈信息",
        ),
        "change": (
            "变更", "发布", "上线", "部署", "deploy", "release", "回滚", "rollback",
            "配置变更", "灰度",
        ),
        "knowledge": (
            "文档", "知识库", "含义", "定义",
        ),
        "diagnosis": (
            "故障", "诊断", "根因", "不可用", "宕机", "全面分析", "全链路",
        ),
    }
    WEAK_KEYWORDS: dict[str, tuple[str, ...]] = {
        "metric": (
            "cpu", "内存", "memory", "磁盘", "disk", "延迟", "耗时", "资源", "负载",
            "状态", "健康", "检测", "检查", "端口", "服务状态", "可达", "存活",
        ),
        "log": (
            "报错",
        ),
        "change": (
            "工单",
        ),
        "knowledge": (
            "说明", "步骤", "是什么", "解释", "介绍",
        ),
        "diagnosis": (
            "排查", "综合", "为什么", "挂了",
        ),
    }
    KEYWORDS: dict[str, tuple[str, ...]] = {
        "metric": STRONG_KEYWORDS["metric"] + WEAK_KEYWORDS["metric"],
        "log": STRONG_KEYWORDS["log"] + WEAK_KEYWORDS["log"],
        "change": STRONG_KEYWORDS["change"] + WEAK_KEYWORDS["change"],
        "knowledge": STRONG_KEYWORDS["knowledge"] + WEAK_KEYWORDS["knowledge"],
        "diagnosis": STRONG_KEYWORDS["diagnosis"] + WEAK_KEYWORDS["diagnosis"],
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

    @staticmethod
    def _matched_categories_for(
        normalized: str, keywords_by_route: dict[str, tuple[str, ...]]
    ) -> list[str]:
        return [
            route
            for route, keywords in keywords_by_route.items()
            if any(keyword in normalized for keyword in keywords)
        ]

    def route_message(self, message: str) -> RouteDecision:
        """Keyword fast path (synchronous). Returns clarify / a single route / default."""
        normalized = message.strip().lower()
        if not normalized:
            return RouteDecision(route="clarify", reason="empty_message", confidence=1.0)
        if not any(char.isalnum() for char in normalized):
            return RouteDecision(route="clarify", reason="no_meaningful_text", confidence=1.0)

        if not getattr(config, "router_keyword_tiering_enabled", True):
            matched = self._matched_categories(normalized)
            if len(matched) == 1:
                route = matched[0]
                return RouteDecision(route=route, reason=f"matched_{route}_keyword", confidence=0.9)
            if not matched:
                return RouteDecision(route=DEFAULT_ROUTE, reason="default_no_keyword", confidence=0.3)
            return RouteDecision(
                route=DEFAULT_ROUTE, reason="ambiguous_keywords", confidence=0.3, hints=tuple(matched)
            )

        strong_matched = self._matched_categories_for(normalized, self.STRONG_KEYWORDS)
        weak_matched = self._matched_categories_for(normalized, self.WEAK_KEYWORDS)
        hints = tuple(dict.fromkeys([*strong_matched, *weak_matched]))

        if len(strong_matched) == 1 and len(hints) == 1:
            route = strong_matched[0]
            return RouteDecision(
                route=route, reason=f"matched_strong_{route}_keyword", confidence=0.9, hints=hints
            )
        if hints:
            return RouteDecision(
                route=DEFAULT_ROUTE, reason="keyword_hints_semantic", confidence=0.3, hints=hints
            )
        return RouteDecision(route=DEFAULT_ROUTE, reason="default_no_keyword", confidence=0.3)

    async def _semantic_route_message(
        self, message: str, hints: tuple[str, ...] = ()
    ) -> RouteDecision:
        multilabel_enabled = getattr(config, "router_multilabel_enabled", True)
        if self.semantic_router:
            if self._semantic_router_accepts_hints():
                result = self.semantic_router(message, hints=hints)
            else:
                result = self.semantic_router(message)
            if inspect.isawaitable(result):
                result = await result
            aux_routes, intent_relation = self._normalize_aux_routes(
                getattr(result, "aux_routes", None),
                primary=result.route,
                enabled=multilabel_enabled,
            )
            return RouteDecision(
                route=result.route,
                reason=result.reason,
                confidence=getattr(result, "confidence", 1.0),
                hints=getattr(result, "hints", hints),
                aux_routes=aux_routes,
                intent_relation=intent_relation,
            )

        if self.llm_client is None:
            self.llm_client = new_llm_client()
        user_content = message
        if hints:
            user_content = (
                f"{message}\n\nKeyword route hints: {', '.join(hints)}. "
                "These are weak lexical signals only. Do not choose metric solely because "
                "CPU/memory/disk/latency/error-rate words appear. For generic how-to or "
                "runbook questions without a concrete target or time window, prefer knowledge."
            )
        # 模型分层：路由是分类任务，用 planner(轻模型) 而不是默认 reasoner(深度模型)，
        # 路由单次从 10~30s 降到 2~5s。空字符串表示退回 LLMClient 默认模型，兼容老配置。
        planner_model = str(getattr(config, "llm_planner_model", "") or "") or None
        response = await self.llm_client.complete(
            [
                ChatMessage(role="system", content=SEMANTIC_ROUTER_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_content),
            ],
            temperature=0,
            model=planner_model,
        )
        result = self._parse_semantic_route_response(response.content)
        aux_routes, intent_relation = self._normalize_aux_routes(
            result.aux_routes,
            primary=result.route,
            enabled=multilabel_enabled,
        )
        return RouteDecision(
            route=result.route,
            reason=f"llm_semantic_{result.route}",
            confidence=result.confidence,
            hints=hints,
            aux_routes=aux_routes,
            intent_relation=intent_relation,
        )

    @staticmethod
    def _normalize_aux_routes(
        aux_routes: Any,
        primary: str,
        enabled: bool,
    ) -> tuple[tuple[str, ...], str]:
        if not enabled:
            return ((), "primary")
        if not aux_routes:
            return ((), "primary")
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in aux_routes:
            if not isinstance(item, str):
                continue
            route = item.strip()
            if not route or route == primary or route == "diagnosis":
                continue
            if route not in EXPERT_ROUTES:
                continue
            if route in seen:
                continue
            seen.add(route)
            cleaned.append(route)
        if not cleaned:
            return ((), "primary")
        return (tuple(cleaned), "parallel")

    def _semantic_router_accepts_hints(self) -> bool:
        if self.semantic_router is None:
            return False
        try:
            parameters = inspect.signature(self.semantic_router).parameters
        except (TypeError, ValueError):
            return False
        return any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD or name == "hints"
            for name, parameter in parameters.items()
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
        if not isinstance(payload, dict):
            return SemanticRouteResult.model_validate(payload)
        aux = payload.get("aux_routes", [])
        if aux is None:
            aux = []
        if isinstance(aux, str):
            aux = [aux]
        if not isinstance(aux, list):
            aux = []
        payload = {**payload, "aux_routes": aux}
        return SemanticRouteResult.model_validate(payload)

    async def _resolve_route(
        self,
        message: str,
        *,
        previous_route: str | None = None,
    ) -> RouteDecision:
        # Short continuations ("继续") inherit the prior turn's route so low-
        # confidence semantic classification cannot hijack chat follow-ups
        # into diagnosis.
        inherited = self._maybe_inherit_continuation_route(message, previous_route)
        if inherited is not None:
            return inherited

        decision = self.route_message(message)
        if decision.route == "clarify":
            return decision
        # High-confidence strong keyword hit: trust it.
        if decision.confidence >= 0.9 and decision.reason.startswith("matched_strong_"):
            return decision

        # Ambiguous or signal-less: ask the semantic classifier.
        try:
            semantic = await self._semantic_route_message(message, hints=decision.hints)
        except Exception as exc:
            logger.warning(f"LLM 语义路由失败，回退到综合诊断: {exc}")
            return RouteDecision(
                route=DEFAULT_ROUTE, reason="semantic_route_failed_default_diagnosis", confidence=0.0
            )

        if semantic.route not in EXPERT_ROUTES:
            return RouteDecision(route=DEFAULT_ROUTE, reason="semantic_unknown_route", confidence=0.0)
        if semantic.confidence < self.min_confidence:
            return self._low_confidence_decision(message, semantic)
        if self._should_override_knowledge_for_incident(message, semantic):
            return RouteDecision(
                route=DEFAULT_ROUTE,
                reason="concrete_incident_override_knowledge",
                confidence=semantic.confidence,
                hints=semantic.hints,
                aux_routes=semantic.aux_routes,
                intent_relation=semantic.intent_relation,
            )
        return semantic

    def _maybe_inherit_continuation_route(
        self,
        message: str,
        previous_route: str | None,
    ) -> RouteDecision | None:
        if not bool(getattr(config, "router_continuation_inherit_enabled", True)):
            return None
        prev = str(previous_route or "").strip()
        if prev not in EXPERT_ROUTES:
            return None
        if not self._is_continuation_message(message):
            return None
        return RouteDecision(
            route=prev,
            reason=f"continuation_inherit_{prev}",
            confidence=0.85,
            hints=(prev,),
        )

    @classmethod
    def _is_continuation_message(cls, message: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(message or "").strip().lower())
        normalized = normalized.strip("？?。.!！…~～")
        if not normalized:
            return False
        return normalized in cls._CONTINUATION_PHRASES

    def _low_confidence_decision(
        self,
        message: str,
        semantic: RouteDecision,
    ) -> RouteDecision:
        """Choose a low-confidence fallback without always forcing diagnosis."""
        keep_semantic = bool(
            getattr(config, "router_low_confidence_keep_semantic_enabled", True)
        )
        if not keep_semantic:
            return RouteDecision(
                route=DEFAULT_ROUTE,
                reason=f"low_confidence_{semantic.route}_default_diagnosis",
                confidence=semantic.confidence,
                hints=semantic.hints,
                aux_routes=semantic.aux_routes,
                intent_relation=semantic.intent_relation,
            )

        normalized = str(message or "").strip().lower()
        has_incident = any(signal in normalized for signal in self._INCIDENT_SIGNALS)
        has_concrete = bool(self._CONCRETE_OPERATIONAL_TARGET.search(normalized))
        operational_hints = tuple(
            hint
            for hint in (semantic.hints or ())
            if hint in self._OPERATIONAL_HINT_ROUTES
        )

        if semantic.route == "knowledge" and not (has_incident and has_concrete):
            return RouteDecision(
                route="knowledge",
                reason="low_confidence_keep_knowledge",
                confidence=semantic.confidence,
                hints=semantic.hints,
                aux_routes=(),
                intent_relation="primary",
            )

        if has_incident or operational_hints or has_concrete:
            return RouteDecision(
                route=DEFAULT_ROUTE,
                reason=f"low_confidence_{semantic.route}_operational_default_diagnosis",
                confidence=semantic.confidence,
                hints=semantic.hints,
                aux_routes=semantic.aux_routes,
                intent_relation=semantic.intent_relation,
            )

        kept = semantic.route if semantic.route in EXPERT_ROUTES else DEFAULT_ROUTE
        return RouteDecision(
            route=kept,
            reason=f"low_confidence_keep_{kept}",
            confidence=semantic.confidence,
            hints=semantic.hints,
            aux_routes=(),
            intent_relation="primary",
        )

    @classmethod
    def _should_override_knowledge_for_incident(
        cls, message: str, decision: RouteDecision
    ) -> bool:
        """Protect incident triage from a semantic knowledge-route false positive."""
        if not bool(
            getattr(config, "router_concrete_incident_override_enabled", True)
        ):
            return False
        if decision.route != "knowledge":
            return False
        normalized = str(message or "").strip().lower()
        return bool(cls._CONCRETE_OPERATIONAL_TARGET.search(normalized)) and any(
            signal in normalized for signal in cls._INCIDENT_SIGNALS
        )

    @classmethod
    def is_knowledge_light_message(cls, message: str) -> bool:
        """True for greetings / identity / pure-explanation knowledge questions."""
        normalized = str(message or "").strip().lower()
        if not normalized:
            return True
        if any(signal in normalized for signal in cls._INCIDENT_SIGNALS):
            # Still light if there is no concrete operational target.
            if cls._CONCRETE_OPERATIONAL_TARGET.search(normalized):
                return False
        if cls._CONCRETE_OPERATIONAL_TARGET.search(normalized):
            return False
        light_markers = (
            "你好",
            "您好",
            "hello",
            "hi",
            "你是谁",
            "你是什么",
            "什么模型",
            "哪款模型",
            "model",
            "介绍一下你",
            "谢谢",
            "thanks",
        )
        if any(marker in normalized for marker in light_markers):
            return True
        # Short non-operational chat without concrete target.
        return len(normalized) <= 24 and not any(
            token in normalized
            for token in ("告警", "日志", "变更", "发布", "排查", "故障", "prometheus")
        )

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
            clarify_text = CLARIFY_TEXT
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
        preference_mode = str(
            getattr(config, "harness_preference_inject_mode", "router_and_harness")
        ).strip().lower()
        preference_context = (
            user_preference_service.format_for_prompt(owner_key)
            if (
                preference_mode != "harness_only"
                and owner_key
                and config.user_preferences_enabled
            )
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
            fallback = build_timeout_report(
                subject=f"{decision.route} 专家",
                message=message,
                timeout_seconds=self.expert_timeout_seconds,
            )
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
