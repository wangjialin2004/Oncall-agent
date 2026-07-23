from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from app.agent.agent_loop import tool_call_payload
from app.agent.context.integration import persist_stateful_context as _persist_stateful_context
from app.agent.context.operations import framework_patch
from app.agent.context.state import SECTION_OUTPUT
from app.agent.events import make_agent_event
from app.agent.harness.otel_export import maybe_export_otel_span
from app.agent.harness.output_safety import (
    contains_internal_tool_protocol,
    sanitize_user_visible_answer,
)
from app.agent.harness.state import HarnessState
from app.agent.harness.trace_export import maybe_export_harness_trace
from app.agent.harness.verifier import VerificationResult
from app.agent.stream_common import TIMELINE_EVENT_TYPES
from app.config import config
from app.core.llm_client import ChatMessage
from app.core.metrics import (
    observe_agent_run,
    observe_agent_tokens,
    observe_tool_calls_from_timeline,
)
from app.services.context_repository import unified_context_repository_enabled


async def persist_stateful_context(*args, **kwargs):
    """Route unified stage persistence to the inflight recovery key."""
    if unified_context_repository_enabled():
        from app.agent.context.unified import persist_runtime_state

        state = args[0] if args else kwargs.get("state")
        warnings, _ = await persist_runtime_state(state, store=kwargs.get("store"))
        return warnings
    return await _persist_stateful_context(*args, **kwargs)


class HarnessClosePathMixin:
    """Timeout soft-close, HITL suggestions, distill and anti-pattern hooks."""

    @staticmethod
    def _tool_protocol_degraded_event(
        *, state: HarnessState, session_id: str, phase: str
    ) -> dict[str, Any]:
        return make_agent_event(
            agent="harness",
            stage="tool_protocol_degraded",
            status="degraded",
            summary=(
                "Model returned textual tool protocol instead of structured tool "
                "calls; discarded it and closed safely."
            ),
            payload={"step": state.step, "phase": phase},
            trace_id=session_id,
            span_id=f"harness:{session_id}:tool_protocol_degraded:{phase}",
        )

    def _soft_close_timeout_event(
        self,
        *,
        state: HarnessState,
        session_id: str,
        message: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Build soft-close complete when outer timeout hits after evidence."""
        yield_events: list[dict[str, Any]] = []
        notice = (
            f"\n\n> ⚠️ 排查在约 {timeout_seconds:g}s 时限前收口："
            "已基于已收集的只读证据给出当前结论；若仍有缺口请继续追问或扩大时间窗。"
        )
        answer = (state.answer or "").strip()
        if not answer:
            # Synthesize a minimal evidence-based close without degraded markers.
            tools_ok = [
                str(ev.get("tool") or "")
                for ev in state.timeline_events
                if ev.get("type") == "tool_event"
                and str(ev.get("status") or "").lower() in {"completed", "success", "ok"}
            ]
            unique_tools = list(dict.fromkeys(t for t in tools_ok if t))[:8]
            answer = (
                f"针对问题「{(message or '')[:120]}」已完成部分只读取证。"
                f"成功工具：{', '.join(unique_tools) or '（见时间线）'}。"
                "当前未完成全部计划步骤，请结合上述证据继续排查；"
                "缺少变更数据源时不得编造版本/操作人。"
            )
        if notice.strip() not in answer:
            answer = answer + notice
        state.append_answer(answer)
        soft_evt = make_agent_event(
            agent="harness",
            stage="timeout_soft_close",
            status="degraded",
            summary="Outer timeout with evidence; soft-closing without degraded fallback text.",
            payload={"timeout_seconds": timeout_seconds, "answer_chars": len(answer)},
            trace_id=session_id,
            span_id=f"harness:{session_id}:timeout_soft_close",
        )
        state.timeline_events.append(soft_evt)
        yield_events.append(soft_evt)
        yield_events.append({"type": "content", "data": answer, "agent": "harness"})
        complete = {
            "type": "complete",
            "route": state.route or "diagnosis",
            "route_reason": "timeout_soft_close",
            "answer": answer,
            "case_id": state.case_id,
            "events": [
                event
                for event in state.timeline_events
                if event.get("type") in TIMELINE_EVENT_TYPES
            ],
            "suggested_actions": self._build_suggested_actions(state, answer),
        }
        return {"_yield_events": yield_events, "complete": complete}

    def _build_suggested_actions(self, state: HarnessState, answer: str) -> list[dict[str, Any]]:
        if not bool(getattr(config, "hitl_suggested_actions_enabled", True)):
            return []
        route = str(state.route or "diagnosis")
        actions = [
            {
                "id": "review_metrics",
                "title": "建议：复核相关指标/告警时间窗（只读）",
                "risk": "low",
                "requires_confirm": True,
            },
            {
                "id": "review_logs",
                "title": "建议：抽样核对错误日志与变更窗口（只读）",
                "risk": "low",
                "requires_confirm": True,
            },
        ]
        if route == "change" or "变更" in (answer or ""):
            actions.append(
                {
                    "id": "verify_change_system",
                    "title": "建议：到发布/工单系统核实变更（系统未接入变更源）",
                    "risk": "medium",
                    "requires_confirm": True,
                }
            )
        return actions

    # ------------------------------------------------------------------ M3 W10 learn
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
    _CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

    def _maybe_distill_experience(
        self,
        *,
        state: HarnessState,
        message: str,
        owner_key: str,
        verification: VerificationResult | None,
    ) -> dict[str, Any] | None:
        """Best-effort success-run draft creation; never raises into complete path."""
        try:
            if not bool(getattr(config, "long_term_memory_distill_enabled", True)):
                return None
            if not bool(getattr(config, "long_term_memory_enabled", True)):
                return None
            route = str(state.route or "")
            if route in {"clarify", "error", "unknown"}:
                return None
            answer = state.answer or ""
            if "harness_degraded_fallback" in answer:
                return None
            conf = str(verification.confidence if verification is not None else "medium").lower()
            min_conf = str(
                getattr(config, "long_term_memory_auto_distill_min_confidence", "medium")
                or "medium"
            ).lower()
            if self._CONFIDENCE_RANK.get(conf, 0) < self._CONFIDENCE_RANK.get(min_conf, 1):
                return None
            if not self._has_successful_investigation_evidence(state.timeline_events):
                return None
            # Pure knowledge regurgitation without investigation tools → skip.
            if route == "knowledge" and not self._has_successful_investigation_evidence(
                state.timeline_events
            ):
                return None
            auto = bool(getattr(config, "long_term_memory_auto_distill", False))
            require_confirm = bool(
                getattr(config, "long_term_memory_distill_require_confirm", True)
            )
            auto_activate = auto and not require_confirm
            from app.services.experience_memory_service import experience_memory_service

            conf_score = {
                "high": float(config.experience_memory_high_confidence),
                "medium": (
                    float(config.experience_memory_high_confidence)
                    + float(config.experience_memory_weak_confidence)
                )
                / 2.0,
                "low": float(config.experience_memory_weak_confidence),
            }.get(conf, float(config.experience_memory_high_confidence))
            experience_id = experience_memory_service.create_draft_from_run(
                project_id=config.project_id,
                session_id=state.session_id or state.trace_id or "",
                user_message=message or "",
                assistant_answer=answer,
                events=list(state.timeline_events or []),
                confidence=conf_score,
                owner_key=owner_key or "",
                auto_activate=auto_activate,
            )
            if not experience_id:
                return None
            status = "active" if auto_activate else "pending"
            logger.info(
                "distill draft created experience_id={} status={} route={}",
                experience_id,
                status,
                route,
            )
            return {
                "experience_id": experience_id,
                "status": status,
                "enabled": auto_activate,
                "requires_confirm": not auto_activate,
            }
        except Exception as exc:  # noqa: BLE001 — never block complete
            logger.warning(f"distill hook skipped: {exc}")
            return None

    def _maybe_capture_anti_pattern(
        self,
        *,
        state: HarnessState,
        message: str,
        owner_key: str,
        verification: VerificationResult | None,
    ) -> dict[str, Any] | None:
        """Capture failed tool sequences as weak anti-pattern memory."""
        try:
            if not bool(getattr(config, "harness_anti_pattern_capture_enabled", True)):
                return None
            if not bool(getattr(config, "long_term_memory_enabled", True)):
                return None
            failed = self._failed_investigation_tools(state.timeline_events)
            has_success = self._has_successful_investigation_evidence(state.timeline_events)
            zero_evidence_gap = "无法" in (state.answer or "") and not has_success
            verify_failed = bool(
                verification is not None
                and verification.status in {"degraded", "failed"}
                and verification.gaps
                and not has_success
            )
            if not failed and not zero_evidence_gap and not verify_failed:
                return None
            if has_success and not failed:
                return None
            from app.services.experience_memory_service import experience_memory_service

            experience_id = experience_memory_service.create_anti_pattern(
                project_id=config.project_id,
                session_id=state.session_id or state.trace_id or "",
                user_message=message or "",
                failed_tools=failed,
                assistant_answer=state.answer or "",
                owner_key=owner_key or "",
            )
            if not experience_id:
                return None
            logger.info(
                "anti_pattern captured experience_id={} tools={}",
                experience_id,
                failed,
            )
            return {
                "experience_id": experience_id,
                "failed_tools": failed,
                "source_type": "anti_pattern",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"anti_pattern hook skipped: {exc}")
            return None

    async def _finish_after_steps(
        self,
        *,
        message: str,
        session_id: str,
        owner_key: str,
        state,
        client,
        messages,
        tools,
        tool_defs,
        plan,
        answer: str,
        answer_streamed: bool,
        re_evidence_rounds_used: int,
        replan_times_used: int,
        resume_close_only: bool,
        stateful_ctx,
        runtime,
        started: float,
        aux_routes,
    ):
        """Verify / re-evidence / replan-once / HITL footer / complete."""

        replacement_content_suppressed = False

        # When investigation tools all failed (e.g. RE2 simulate) and the model
        # returned no prose, still emit a gap notice so complete.answer is usable
        # and evaluators can score require_replan + gap wording.
        if not (answer or "").strip():
            gap_answer = self._synthesize_zero_evidence_gap_answer(
                message=message,
                state=state,
                replan_times_used=replan_times_used,
            )
            if gap_answer:
                answer = gap_answer

        answer = sanitize_user_visible_answer(answer)

        if answer:
            final_answer = answer
            verification: VerificationResult | None = None
            max_re_rounds = max(0, int(getattr(config, "harness_re_evidence_max_rounds", 1) or 0))
            re_enabled = bool(getattr(config, "harness_re_evidence_enabled", True))

            while True:
                verify_progress = self._progress_event(
                    state=state,
                    stage="verify",
                    summary="Verifying final answer against evidence.",
                    payload={
                        "answer_chars": len(final_answer),
                        "re_evidence_rounds_used": re_evidence_rounds_used,
                    },
                )
                yield verify_progress
                verification = await self.verifier.averify(
                    answer=final_answer,
                    timeline_events=state.timeline_events,
                    plan=plan,
                    llm_client=client,
                )
                verify_event = self._verify_event(verification, state=state)
                state.timeline_events.append(verify_event)
                yield verify_event

                can_re_evidence = (
                    re_enabled
                    and re_evidence_rounds_used < max_re_rounds
                    and not resume_close_only
                    and not self._is_knowledge_light_plan(plan)
                    and verification.status in {"degraded", "failed"}
                    and bool(verification.gaps)
                    and bool(tool_defs)
                    and self._has_investigation_tools(tools)
                    and not state.over_budget(self.limits)
                )
                if not can_re_evidence:
                    break

                re_evidence_rounds_used += 1
                re_event = make_agent_event(
                    agent="harness",
                    stage="re_evidence",
                    status="in_progress",
                    summary=(
                        f"Evidence gaps remain; running re-evidence pass "
                        f"{re_evidence_rounds_used}/{max_re_rounds}."
                    ),
                    payload={
                        "round": re_evidence_rounds_used,
                        "max_rounds": max_re_rounds,
                        "gaps": list(verification.gaps),
                        "confidence": verification.confidence,
                        "status": verification.status,
                    },
                    trace_id=session_id,
                    span_id=f"harness:{session_id}:re_evidence:{re_evidence_rounds_used}",
                )
                state.timeline_events.append(re_event)
                yield re_event

                gap_text = "；".join(str(g) for g in verification.gaps if g)
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            f"证据自检未通过（补取证第 {re_evidence_rounds_used} 轮）。"
                            f"缺口：{gap_text or '证据不足'}。"
                            "请调用最相关的只读工具补取证，再给出最终结论；"
                            "若仍无法取证，必须明确声明证据缺口，禁止编造数值/版本/操作人。"
                        ),
                    )
                )
                state.add_text_budget(messages[-1].content)

                re_answer = ""
                re_streamed = False
                emit_replacement_content = not (
                    answer_streamed
                    and bool(
                        getattr(config, "harness_final_answer_replacement_enabled", True)
                    )
                )
                re_response = None
                re_decision_text = ""
                re_decision_parts: tuple[str, ...] = ()
                re_step_timed_out = False
                try:
                    async with asyncio.timeout(self.limits.step_timeout_seconds):
                        (
                            re_response,
                            re_decision_text,
                            re_decision_parts,
                        ) = await self._collect_chat_turn(
                            client=client,
                            messages=messages,
                            tools=tool_defs or None,
                            tool_choice="auto" if tool_defs else None,
                            temperature=0.3,
                            model=self._planner_model(),
                        )
                except TimeoutError:
                    re_step_timed_out = True
                    step_timeout_event = make_agent_event(
                        agent="harness",
                        stage="step_timeout",
                        status="degraded",
                        summary=(
                            f"Re-evidence LLM decision exceeded "
                            f"{self.limits.step_timeout_seconds}s; closing with gaps."
                        ),
                        payload={
                            "step": state.step,
                            "re_evidence_round": re_evidence_rounds_used,
                            "step_timeout_seconds": self.limits.step_timeout_seconds,
                        },
                        trace_id=session_id,
                        span_id=f"harness:{session_id}:re_evidence_timeout",
                    )
                    state.timeline_events.append(step_timeout_event)
                    yield step_timeout_event

                if re_step_timed_out or re_response is None:
                    break

                state.add_usage(re_response.usage)
                if re_response.tool_calls:
                    messages.append(
                        ChatMessage(
                            role="assistant",
                            content=re_response.content,
                            tool_calls=[tool_call_payload(tc) for tc in re_response.tool_calls],
                        )
                    )
                    async for event in self._execute_tools(
                        re_response.tool_calls,
                        tools,
                        messages,
                        client=client,
                        state=state,
                        context_state=(stateful_ctx.state if stateful_ctx is not None else None),
                    ):
                        yield event
                    if stateful_ctx is not None:
                        await persist_stateful_context(
                            stateful_ctx.state,
                            store=self.context_store,
                            persist_snapshot=True,
                        )
                    self._schedule_checkpoint_save(
                        state=state,
                        messages=messages,
                        step_index=state.step,
                        tool_calls=[tool_call_payload(tc) for tc in re_response.tool_calls],
                        context_state=(stateful_ctx.state if stateful_ctx is not None else None),
                    )
                    # After tools, force a no-tool closing answer for re-verify.
                    re_answer = ""
                    re_streamed = False
                    async for chunk in self._stream_final_answer(
                        client=client,
                        messages=messages,
                        temperature=0.3,
                        model=self._reasoner_model(),
                    ):
                        re_answer += chunk
                        if emit_replacement_content:
                            re_streamed = True
                            yield {
                                "type": "content",
                                "data": chunk,
                                "agent": "harness",
                            }
                else:
                    candidate = str(re_response.content or re_decision_text or "")
                    if contains_internal_tool_protocol(candidate):
                        protocol_event = self._tool_protocol_degraded_event(
                            state=state,
                            session_id=session_id,
                            phase="re_evidence",
                        )
                        state.timeline_events.append(protocol_event)
                        yield protocol_event
                        async for chunk in self._stream_final_answer(
                            client=client,
                            messages=messages,
                            temperature=0.3,
                            model=self._reasoner_model(),
                        ):
                            re_answer += chunk
                            if emit_replacement_content:
                                re_streamed = True
                                yield {
                                    "type": "content",
                                    "data": chunk,
                                    "agent": "harness",
                                }
                    else:
                        re_answer = sanitize_user_visible_answer(candidate or final_answer)
                        if re_answer and emit_replacement_content:
                            re_streamed = True
                            if re_decision_parts and re_answer == candidate.strip():
                                for part in re_decision_parts:
                                    yield {
                                        "type": "content",
                                        "data": part,
                                        "agent": "harness",
                                    }
                            else:
                                yield {
                                    "type": "content",
                                    "data": re_answer,
                                    "agent": "harness",
                                }

                if re_answer.strip():
                    replacement_content_suppressed = (
                        replacement_content_suppressed or not emit_replacement_content
                    )
                    final_answer = re_answer
                    answer = re_answer
                    answer_streamed = answer_streamed or re_streamed
                # loop to re-verify

            assert verification is not None

            # After re-evidence was actually used and gaps remain, optionally replan
            # once and take one more light tool turn before corrective final.
            # (Do not replan merely because re-evidence is disabled — that would
            # change the W1 "re_evidence off = single pass" contract.)
            if (
                re_evidence_rounds_used > 0
                and verification.status in {"degraded", "failed"}
                and bool(verification.gaps)
                and self._should_replan(
                    replan_times_used=replan_times_used,
                    resume_close_only=resume_close_only,
                    state=state,
                    verification_gaps=list(verification.gaps),
                    force_after_re_evidence=True,
                    aux_routes=aux_routes,
                    plan=plan,
                )
                and bool(tool_defs)
                and self._has_investigation_tools(tools)
                and not state.over_budget(self.limits)
            ):
                plan, replan_event = self._apply_replan(
                    plan=plan,
                    state=state,
                    messages=messages,
                    trigger="post_re_evidence_gaps",
                    gaps=list(verification.gaps),
                    failed_tools=self._failed_investigation_tools(state.timeline_events),
                    aux_routes=aux_routes,
                )
                replan_times_used += 1
                yield replan_event

                replan_answer = ""
                replan_streamed = False
                emit_replacement_content = not (
                    answer_streamed
                    and bool(
                        getattr(config, "harness_final_answer_replacement_enabled", True)
                    )
                )
                replan_response = None
                replan_decision_text = ""
                replan_decision_parts: tuple[str, ...] = ()
                replan_step_timed_out = False
                try:
                    async with asyncio.timeout(self.limits.step_timeout_seconds):
                        (
                            replan_response,
                            replan_decision_text,
                            replan_decision_parts,
                        ) = await self._collect_chat_turn(
                            client=client,
                            messages=messages,
                            tools=tool_defs or None,
                            tool_choice="auto" if tool_defs else None,
                            temperature=0.3,
                            model=self._planner_model(),
                        )
                except TimeoutError:
                    replan_step_timed_out = True
                    step_timeout_event = make_agent_event(
                        agent="harness",
                        stage="step_timeout",
                        status="degraded",
                        summary=(
                            f"Post-replan LLM decision exceeded "
                            f"{self.limits.step_timeout_seconds}s; closing with gaps."
                        ),
                        payload={
                            "step": state.step,
                            "replan_times_used": replan_times_used,
                            "step_timeout_seconds": self.limits.step_timeout_seconds,
                        },
                        trace_id=session_id,
                        span_id=f"harness:{session_id}:replan_timeout",
                    )
                    state.timeline_events.append(step_timeout_event)
                    yield step_timeout_event

                if not replan_step_timed_out and replan_response is not None:
                    state.add_usage(replan_response.usage)
                    if replan_response.tool_calls:
                        messages.append(
                            ChatMessage(
                                role="assistant",
                                content=replan_response.content,
                                tool_calls=[
                                    tool_call_payload(tc) for tc in replan_response.tool_calls
                                ],
                            )
                        )
                        async for event in self._execute_tools(
                            replan_response.tool_calls,
                            tools,
                            messages,
                            client=client,
                            state=state,
                            context_state=(
                                stateful_ctx.state if stateful_ctx is not None else None
                            ),
                        ):
                            yield event
                        if stateful_ctx is not None:
                            await persist_stateful_context(
                                stateful_ctx.state,
                                store=self.context_store,
                                persist_snapshot=True,
                            )
                        replan_answer = ""
                        replan_streamed = False
                        async for chunk in self._stream_final_answer(
                            client=client,
                            messages=messages,
                            temperature=0.3,
                            model=self._reasoner_model(),
                        ):
                            replan_answer += chunk
                            if emit_replacement_content:
                                replan_streamed = True
                                yield {
                                    "type": "content",
                                    "data": chunk,
                                    "agent": "harness",
                                }
                    else:
                        candidate = str(replan_response.content or replan_decision_text or "")
                        if contains_internal_tool_protocol(candidate):
                            protocol_event = self._tool_protocol_degraded_event(
                                state=state,
                                session_id=session_id,
                                phase="replan",
                            )
                            state.timeline_events.append(protocol_event)
                            yield protocol_event
                            async for chunk in self._stream_final_answer(
                                client=client,
                                messages=messages,
                                temperature=0.3,
                                model=self._reasoner_model(),
                            ):
                                replan_answer += chunk
                                if emit_replacement_content:
                                    replan_streamed = True
                                    yield {
                                        "type": "content",
                                        "data": chunk,
                                        "agent": "harness",
                                    }
                        else:
                            replan_answer = sanitize_user_visible_answer(candidate or final_answer)
                            if replan_answer and emit_replacement_content:
                                replan_streamed = True
                                if replan_decision_parts and replan_answer == candidate.strip():
                                    for part in replan_decision_parts:
                                        yield {
                                            "type": "content",
                                            "data": part,
                                            "agent": "harness",
                                        }
                                else:
                                    yield {
                                        "type": "content",
                                        "data": replan_answer,
                                        "agent": "harness",
                                    }

                    if replan_answer.strip():
                        replacement_content_suppressed = (
                            replacement_content_suppressed or not emit_replacement_content
                        )
                        final_answer = replan_answer
                        answer = replan_answer
                        answer_streamed = answer_streamed or replan_streamed

                    verification = await self.verifier.averify(
                        answer=final_answer,
                        timeline_events=state.timeline_events,
                        plan=plan,
                        llm_client=client,
                    )
                    verify_event = self._verify_event(verification, state=state)
                    state.timeline_events.append(verify_event)
                    yield verify_event

            if (
                getattr(config, "harness_corrective_verify_enabled", True)
                and verification.status in {"degraded", "failed"}
                and verification.gaps
            ):
                final_answer = self._apply_corrective_notice(final_answer, verification)
            final_answer = sanitize_user_visible_answer(final_answer)
            state.append_answer(final_answer)
            if stateful_ctx is not None:
                framework_patch(
                    stateful_ctx.state,
                    SECTION_OUTPUT,
                    "last_answer_summary",
                    op="set",
                    value=final_answer[:600],
                    history_limit=int(getattr(config, "harness_context_patch_history_limit", 200)),
                )
                self._stamp_current_turn(
                    stateful_ctx.state,
                    user_message=message,
                    assistant_answer=final_answer,
                )
                await persist_stateful_context(
                    stateful_ctx.state,
                    store=self.context_store,
                    persist_snapshot=True,
                )
                if runtime is not None:
                    runtime["context_state"] = stateful_ctx.state
            report_progress = self._progress_event(
                state=state,
                stage="report",
                summary="Evidence verification complete; emitting final report.",
                payload={
                    "answer_chars": len(final_answer),
                    "re_evidence_rounds_used": re_evidence_rounds_used,
                    "replan_times_used": replan_times_used,
                    **(
                        {"context_chars": dict(runtime.get("context_metrics") or {})}
                        if runtime is not None
                        and bool(getattr(config, "harness_context_size_metrics_enabled", False))
                        else {}
                    ),
                },
            )
            yield report_progress
            if not answer_streamed:
                yield {"type": "content", "data": final_answer, "agent": "harness"}

        suggested_actions = self._build_suggested_actions(state, state.answer or "")
        parallel_count = sum(
            1
            for ev in state.timeline_events
            if (
                (
                    ev.get("type") == "agent_event"
                    and ev.get("stage")
                    in {
                        "delegate_parallel_start",
                        "delegate_parallel_done",
                    }
                )
                or (ev.get("type") == "tool_event" and ev.get("tool") == "delegate_parallel")
            )
        )
        distill_draft = self._maybe_distill_experience(
            state=state,
            message=message,
            owner_key=owner_key,
            verification=verification if "verification" in locals() else None,
        )
        anti_pattern = self._maybe_capture_anti_pattern(
            state=state,
            message=message,
            owner_key=owner_key,
            verification=verification if "verification" in locals() else None,
        )
        complete_event = make_agent_event(
            agent="harness",
            stage="complete",
            status="completed",
            summary="Unified Harness main loop completed.",
            payload={
                "answer_chars": len(state.answer),
                "steps": state.step,
                "token_estimate": state.token_estimate,
                "re_evidence_rounds_used": re_evidence_rounds_used,
                "replan_times_used": replan_times_used,
                "suggested_actions": suggested_actions,
                "distill_draft": distill_draft,
                "anti_pattern": anti_pattern,
                **(
                    {"context_chars": dict(runtime.get("context_metrics") or {})}
                    if runtime is not None
                    and bool(getattr(config, "harness_context_size_metrics_enabled", False))
                    else {}
                ),
            },
            trace_id=session_id,
            span_id=f"harness:{session_id}",
            duration_ms=(time.perf_counter() - started) * 1000,
            usage=state.usage_total or None,
        )
        state.timeline_events.append(complete_event)
        yield complete_event
        latency_s = time.perf_counter() - started
        maybe_export_harness_trace(
            session_id=session_id,
            state=state,
            plan=plan if "plan" in locals() else None,
            verification=verification if "verification" in locals() else None,
            re_evidence_rounds=re_evidence_rounds_used,
            replan_times=replan_times_used,
            distill_draft=distill_draft if isinstance(distill_draft, dict) else None,
            anti_pattern=anti_pattern if isinstance(anti_pattern, dict) else None,
        )
        self._schedule_checkpoint_completed(state=state)
        observe_agent_run(
            status="completed",
            latency_seconds=latency_s,
            re_evidence_rounds=int(re_evidence_rounds_used or 0),
            replan_times=int(replan_times_used or 0),
            parallel_delegations=1 if parallel_count else 0,
        )
        # W11: cost / tool metrics (timeline scan once; usage from state).
        observe_agent_tokens(state.usage_total)
        observe_tool_calls_from_timeline(state.timeline_events)
        maybe_export_otel_span(
            session_id=session_id,
            route=getattr(state, "route", None),
            status="completed",
            latency_seconds=latency_s,
            re_evidence_rounds=int(re_evidence_rounds_used or 0),
            replan_times=int(replan_times_used or 0),
            usage=state.usage_total,
        )
        complete_payload = self._complete_event(state)
        # A re-evidence/replan pass can replace prose that was already visible
        # through ``content`` events. Keep the SSE type stable and mark only
        # an answer whose replacement content was actually suppressed. This
        # leaves ordinary streaming and the rollback flag on their old path.
        if replacement_content_suppressed:
            complete_payload["replace_streamed_answer"] = True
        complete_payload["suggested_actions"] = suggested_actions
        complete_payload["distill_draft"] = distill_draft
        complete_payload["anti_pattern"] = anti_pattern
        yield complete_payload
