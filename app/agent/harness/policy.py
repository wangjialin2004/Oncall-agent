from __future__ import annotations

from app.config import config

import json
from collections.abc import Sequence
from typing import Any

from app.agent.events import make_agent_event
from app.agent.experts.registry import EXPERT_ROUTES
from app.agent.harness.planner import HarnessPlan
from app.agent.harness.state import HarnessState
from app.core.llm_client import ChatMessage, ToolCall
from app.core.runtime_tools import RuntimeTool

class HarnessPolicyMixin:
    """Policy / evidence / replan / early-close helpers."""

    _INVESTIGATION_TOOL_HINTS = (
        "prometheus",
        "metric",
        "alert",
        "log",
        "change",
        "redis",
        "delegate",
        "search_app",
        "query_",
        "analyze_log",
        "check_",
    )

    _KNOWLEDGE_ONLY_TOOLS = frozenset(
        {
            "retrieve_knowledge",
            "recall_experience",
            "lookup_service_knowledge",
            "context_read",
            "context_note",
            "read_attachment",
        }
    )

    @staticmethod
    def _is_knowledge_light_plan(plan: Any | None) -> bool:
        """True when planner marked a knowledge light-path (no hard evidence)."""
        if plan is None:
            return False
        if str(getattr(plan, "focus_route", "") or "").strip() != "knowledge":
            return False
        if not bool(getattr(config, "harness_knowledge_light_path_enabled", True)):
            return False
        required = list(getattr(plan, "required_evidence", None) or [])
        return len(required) == 0

    _CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

    def _effective_max_steps(self, route: str) -> int:
        """Optionally shrink the step budget for simple knowledge/clarify routes."""
        base = int(self.limits.max_steps)
        if not bool(getattr(config, "harness_dynamic_max_steps", True)):
            return base
        route_name = str(route or "").strip().lower()
        if route_name in {"knowledge", "clarify"}:
            # Route timeout profile can further clamp knowledge steps.
            cap = 2 if bool(getattr(config, "harness_route_timeout_profile", True)) else 3
            return max(1, min(base, cap))
        if route_name == "diagnosis" and bool(
            getattr(config, "harness_route_timeout_profile", True)
        ):
            cap = max(
                1,
                int(getattr(config, "harness_diagnosis_max_steps", 3) or 3),
            )
            return max(1, min(base, cap))
        return base

    def _should_knowledge_early_close(self, *, state: HarnessState) -> bool:
        if not bool(getattr(config, "harness_knowledge_early_close", True)):
            return False
        if str(state.route or "").strip().lower() != "knowledge":
            return False
        knowledge_tools = {
            "retrieve_knowledge",
            "recall_experience",
            "lookup_service_knowledge",
            "search_knowledge",
        }
        for event in state.timeline_events:
            if event.get("type") != "tool_event":
                continue
            tool = str(event.get("tool") or "")
            status = str(event.get("status") or "").lower()
            if tool in knowledge_tools and status in {"completed", "success", "ok"}:
                return True
        return False

    def _should_investigation_evidence_early_close(
        self, *, state: HarnessState
    ) -> bool:
        """Close after usable evidence, while retaining verification paths."""
        if not bool(getattr(config, "harness_route_timeout_profile", True)):
            return False
        if not bool(
            getattr(config, "harness_investigation_evidence_early_close", True)
        ):
            return False
        if str(state.route or "").strip().lower() == "clarify":
            return False
        return any(
            event.get("type") == "tool_event"
            and str(event.get("status") or "").lower()
            in {"completed", "success", "ok"}
            and not self._is_context_helper_tool(str(event.get("tool") or ""))
            for event in state.timeline_events
        )

    def _should_replan(
        self,
        *,
        replan_times_used: int,
        resume_close_only: bool,
        state: HarnessState,
        verification_gaps: Sequence[str] | None,
        force_after_re_evidence: bool,
        aux_routes: Sequence[str],
        plan: Any | None = None,
    ) -> bool:
        if not bool(getattr(config, "harness_replan_enabled", True)):
            return False
        max_times = max(0, int(getattr(config, "harness_replan_max_times", 1) or 0))
        if replan_times_used >= max_times:
            return False
        if resume_close_only:
            return False
        if self._is_knowledge_light_plan(plan):
            return False
        if state.over_budget(self.limits):
            return False

        if force_after_re_evidence and verification_gaps:
            return True

        # Mid-loop: only after at least one investigation tool outcome exists.
        if not self._failed_investigation_tools(
            state.timeline_events
        ) and not self._has_successful_tool_evidence(state.timeline_events):
            # Still allow aux-route trigger after first model/tool attempt (step>=2).
            if not aux_routes or state.step < 2:
                return False

        failed_tools = self._failed_investigation_tools(state.timeline_events)
        has_success = self._has_successful_tool_evidence(state.timeline_events)
        if failed_tools and not has_success:
            return True

        # M3 W9: primary-route investigation tools failed → replan even if weak
        # successes (e.g. knowledge) already exist.
        if (
            bool(getattr(config, "harness_replan_on_primary_fail", True))
            and failed_tools
            and self._primary_investigation_tools_failed(
                state=state, failed_tools=failed_tools
            )
        ):
            return True

        # Aux cross-domain hint without any successful expert delegation / tools.
        if aux_routes and not has_success:
            delegated = {
                str((ev.get("payload") or {}).get("delegated_expert") or "")
                for ev in state.timeline_events
                if ev.get("type") == "agent_event"
                and ev.get("stage") in {"delegate_start", "delegate_dispatch"}
            }
            if not any(route in delegated for route in aux_routes):
                return True
        return False

    def _apply_replan(
        self,
        *,
        plan: HarnessPlan,
        state: HarnessState,
        messages: list[ChatMessage],
        trigger: str,
        gaps: Sequence[str] | None,
        failed_tools: Sequence[str] | None,
        aux_routes: Sequence[str],
    ) -> tuple[HarnessPlan, dict[str, Any]]:
        new_plan = self.planner.replan(
            plan,
            trigger=trigger,
            gaps=gaps,
            failed_tools=failed_tools,
            aux_routes=aux_routes,
        )
        replan_event = make_agent_event(
            agent="harness",
            stage="replan",
            status="completed",
            summary=f"Revised investigation plan ({trigger}).",
            payload={
                "trigger": trigger,
                "todos": new_plan.todos,
                "required_evidence": new_plan.required_evidence,
                "gaps": list(gaps or []),
                "failed_tools": list(failed_tools or []),
                "aux_routes": list(aux_routes or []),
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:replan",
        )
        state.timeline_events.append(replan_event)
        gap_text = "；".join(str(g) for g in (gaps or []) if g)
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    "排查计划已修订（规则 replan）。"
                    f"触发：{trigger}。"
                    f"新 todos：{'；'.join(new_plan.todos[:5])}。"
                    f"必需证据：{'、'.join(new_plan.required_evidence)}。"
                    f"{('缺口：' + gap_text + '。') if gap_text else ''}"
                    "请按新计划调用只读工具取证；若仍无法取证，必须声明证据缺口，"
                    "禁止编造数值/版本/操作人。"
                ),
            )
        )
        state.add_text_budget(messages[-1].content)
        return new_plan, replan_event

    @staticmethod
    def _collect_recent_tool_failure_summaries(
        events: Sequence[dict[str, Any]],
    ) -> list[str]:
        gaps: list[str] = []
        for event in events:
            if event.get("type") != "tool_event":
                continue
            status = str(event.get("status") or "").lower()
            if status not in {"failed", "error", "timeout", "timed_out"}:
                continue
            tool = str(event.get("tool") or "tool")
            summary = str(event.get("summary") or "").strip()
            gaps.append(f"{tool} 失败" + (f"：{summary[:120]}" if summary else ""))
        return gaps[-4:]

    def _should_force_seed_delegate(
        self, *, route: str, tools: Sequence[RuntimeTool]
    ) -> bool:
        """Whether to deterministically seed a first expert delegation."""
        if not bool(getattr(config, "harness_force_expert_delegation", False)):
            return False
        if not bool(getattr(config, "harness_delegation_enabled", True)):
            return False
        if route not in EXPERT_ROUTES:
            return False
        return any(tool.name == "delegate_to_expert" for tool in tools)

    def _should_force_seed_parallel(
        self,
        *,
        message: str,
        route: str,
        tools: Sequence[RuntimeTool],
        aux_routes: Sequence[str],
        prefer_parallel: bool | None,
    ) -> bool:
        """Seed delegate_parallel for cross-domain diagnosis (M3 W9 / P1)."""
        if not bool(getattr(config, "harness_force_parallel_on_cross_domain", True)):
            return False
        if not bool(getattr(config, "harness_parallel_delegation_enabled", True)):
            return False
        if not bool(getattr(config, "harness_delegation_enabled", True)):
            return False
        if not any(tool.name == "delegate_parallel" for tool in tools):
            return False
        return bool(prefer_parallel) or self._looks_cross_domain(
            message=message, route=route, aux_routes=aux_routes
        )

    @staticmethod
    def _looks_cross_domain(
        *,
        message: str,
        route: str,
        aux_routes: Sequence[str],
    ) -> bool:
        text = (message or "").lower()
        if "并行" in text or "parallel" in text:
            return True
        metric_tokens = ("cpu", "内存", "memory", "p99", "告警", "指标", "prometheus")
        log_tokens = ("日志", "error", "错误", "stack", "log")
        has_metric = any(t in text for t in metric_tokens)
        has_log = any(t in text for t in log_tokens)
        if has_metric and has_log:
            return True
        aux = {str(r).strip().lower() for r in aux_routes if str(r).strip()}
        if route == "diagnosis" and aux & {"metric", "log", "change"}:
            return True
        if "metric" in aux and "log" in aux:
            return True
        return False

    def _primary_investigation_tools_failed(
        self, *, state: HarnessState, failed_tools: Sequence[str]
    ) -> bool:
        focus = str(state.route or "").strip().lower()
        failed = {str(t).lower() for t in failed_tools}
        primary_prefixes = {
            "metric": ("query_prometheus", "query_cpu", "query_memory", "query_disk", "get_local_resource"),
            "log": ("search_app_logs", "get_recent_app_logs", "get_log_files", "analyze_logs"),
            "change": ("query_recent_changes",),
            "diagnosis": (
                "query_prometheus",
                "query_cpu",
                "query_memory",
                "search_app_logs",
                "get_recent_app_logs",
            ),
        }.get(focus, ("query_prometheus", "query_cpu", "search_app_logs"))
        return any(
            any(name.startswith(prefix) or prefix in name for prefix in primary_prefixes)
            for name in failed
        )

    def _apply_simulate_faults(
        self,
        tools: Sequence[RuntimeTool],
        *,
        simulate: str,
        state: HarnessState,
    ) -> list[RuntimeTool]:
        """Eval-only fault injection (RE2)."""
        tag = (simulate or "").strip().lower()
        if not tag:
            return list(tools)
        fail_prom = "prometheus_unavailable" in tag
        if not fail_prom:
            return list(tools)

        async def _fail_handler(arguments: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("simulated prometheus unavailable")

        wrapped: list[RuntimeTool] = []
        for tool in tools:
            name = str(tool.name or "")
            if name in {
                "query_prometheus_alerts",
                "query_cpu_metrics",
                "query_memory_metrics",
                "query_disk_metrics",
            } or name.startswith("query_prometheus"):
                wrapped.append(
                    RuntimeTool(
                        name=tool.name,
                        description=tool.description,
                        parameters=tool.parameters,
                        handler=_fail_handler,
                    )
                )
            else:
                wrapped.append(tool)
        inject_event = make_agent_event(
            agent="harness",
            stage="simulate_fault",
            status="completed",
            summary=f"Eval simulate active: {tag}",
            payload={"simulate": tag, "failed_tools": "prometheus*"},
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:simulate",
        )
        state.timeline_events.append(inject_event)
        return wrapped

    def _synthesize_zero_evidence_gap_answer(
        self,
        *,
        message: str,
        state: HarnessState,
        replan_times_used: int = 0,
    ) -> str:
        """Build a minimal gap notice when tools failed and model returned no prose.

        Used by RE2-style primary-fail paths so complete.answer is not only the
        empty response. Read-only; never claims remediation was executed.
        """
        if self._has_successful_tool_evidence(state.timeline_events):
            return ""
        failed = self._failed_investigation_tools(state.timeline_events)
        fail_summaries = self._collect_recent_tool_failure_summaries(
            state.timeline_events
        )
        if not failed and int(replan_times_used or 0) < 1:
            # No investigation failure signal — leave empty (clarifier/other paths).
            return ""
        failed_list = ", ".join(failed[:8]) if failed else "（见时间线）"
        summary_line = ""
        if fail_summaries:
            summary_line = "失败摘要：" + "；".join(str(s) for s in fail_summaries[:4]) + "。\n"
        replan_line = (
            f"已触发重规划 {int(replan_times_used)} 次，仍无法取得实时指标证据。\n"
            if int(replan_times_used or 0) > 0
            else "主调查工具失败后未能取得成功证据。\n"
        )
        q = (message or "").strip()[:120]
        return (
            f"## 证据缺口\n"
            f"针对问题「{q}」：主路径只读调查工具失败或不可用，"
            f"**无法**基于实时 Prometheus/指标给出可靠结论。\n"
            f"{replan_line}"
            f"失败工具：{failed_list}。\n"
            f"{summary_line}"
            "请勿用知识库结论冒充实时告警/指标；变更数据源未接入时不得编造版本/操作人。"
            "建议：恢复指标源后重试，或走现有 OnCall 升级流程。"
        )

    def _should_timeout_soft_close(self, state: Any) -> bool:
        if not bool(getattr(config, "harness_timeout_soft_close_enabled", True)):
            return False
        if not isinstance(state, HarnessState):
            return False
        if not (state.answer or "").strip() and not self._has_successful_tool_evidence(
            state.timeline_events
        ):
            return False
        return self._has_successful_tool_evidence(state.timeline_events)

    @classmethod
    def _has_successful_investigation_evidence(
        cls, events: Sequence[dict[str, Any]]
    ) -> bool:
        for event in events or []:
            if event.get("type") != "tool_event":
                continue
            if str(event.get("status") or "").lower() not in {"completed", "success", "ok"}:
                continue
            tool = str(event.get("tool") or event.get("tool_name") or "").strip()
            if not tool or cls._is_context_helper_tool(tool):
                continue
            if tool in cls._KNOWLEDGE_ONLY_TOOLS:
                # knowledge-only tools do not count as investigation evidence for distill
                continue
            lower = tool.lower()
            if any(hint in lower for hint in cls._INVESTIGATION_TOOL_HINTS):
                return True
            # delegate / unknown non-knowledge tools count as investigation
            if tool not in cls._KNOWLEDGE_ONLY_TOOLS:
                return True
        return False

    @classmethod
    def _failed_investigation_tools(cls, events: Sequence[dict[str, Any]]) -> list[str]:
        failed: list[str] = []
        seen: set[str] = set()
        for event in events or []:
            if event.get("type") != "tool_event":
                continue
            status = str(event.get("status") or "").lower()
            if status not in {"failed", "error", "timeout", "cancelled"}:
                continue
            tool = str(event.get("tool") or event.get("tool_name") or "").strip()
            if not tool or cls._is_context_helper_tool(tool):
                continue
            if tool in seen:
                continue
            seen.add(tool)
            failed.append(tool)
        return failed

    def _tool_success_counts(self, events: Sequence[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in events:
            if event.get("type") != "tool_event":
                continue
            if str(event.get("status") or "").lower() not in {"completed", "success", "ok"}:
                continue
            tool = str(event.get("tool") or "").strip()
            if not tool or self._is_context_helper_tool(tool):
                continue
            counts[tool] = counts.get(tool, 0) + 1
        return counts

    def _filter_tools_by_cap(
        self, tools: Sequence[RuntimeTool], state: HarnessState
    ) -> list[RuntimeTool]:
        cap = int(getattr(config, "harness_slow_path_tool_cap", 2) or 0)
        if cap <= 0:
            return list(tools)
        counts = self._tool_success_counts(state.timeline_events)
        filtered: list[RuntimeTool] = []
        for tool in tools:
            name = str(tool.name or "")
            # Never cap control-plane tools.
            if name in {"delegate_to_expert", "delegate_parallel"}:
                filtered.append(tool)
                continue
            if counts.get(name, 0) >= cap:
                continue
            filtered.append(tool)
        return filtered

    @staticmethod
    def _tool_signature(tool_call: ToolCall) -> str:
        try:
            args = json.dumps(tool_call.arguments, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            args = str(tool_call.arguments)
        return f"{tool_call.name}:{args}"

    @staticmethod
    def _is_log_tool(tool_name: str) -> bool:
        return "log" in (tool_name or "").lower()

    @staticmethod
    def _is_context_helper_tool(tool_name: str) -> bool:
        name = (tool_name or "").strip().lower()
        return name in {"context_read", "context_note", "read_attachment"}

    @classmethod
    def _has_investigation_tools(cls, tools: Sequence[RuntimeTool]) -> bool:
        """True when at least one non-context helper tool is available.

        Stateful context injects whiteboard helpers even when the harness is
        constructed with ``tools=[]``. Those helpers must not block missing-
        parameter clarification or zero-evidence verify degradation.
        """
        return any(not cls._is_context_helper_tool(tool.name) for tool in tools or [])

    @staticmethod
    def _has_successful_tool_evidence(events: Sequence[dict[str, Any]]) -> bool:
        return any(
            event.get("type") == "tool_event" and event.get("status") == "completed"
            for event in events
        )
