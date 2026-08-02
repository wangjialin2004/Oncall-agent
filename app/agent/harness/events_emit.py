from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any

from app.agent.events import make_agent_event
from app.agent.harness.clarifier import ClarificationRequest
from app.agent.harness.output_safety import sanitize_user_visible_answer
from app.agent.harness.planner import HarnessPlan
from app.agent.harness.state import HarnessState
from app.agent.harness.verifier import VerificationResult
from app.agent.stream_common import TIMELINE_EVENT_TYPES


class HarnessEventsMixin:
    """SSE / agent event builders and clarification emission."""

    def _progress_event(
        self,
        *,
        state: HarnessState,
        stage: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = make_agent_event(
            agent="harness",
            stage=stage,
            status="in_progress",
            summary=summary,
            payload=payload or {},
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:{stage}:{len(state.timeline_events) + 1}",
        )
        state.timeline_events.append(event)
        return event

    def _make_plan_event(
        self,
        plan: HarnessPlan,
        *,
        state: HarnessState,
    ) -> dict[str, Any]:
        return make_agent_event(
            agent="harness",
            stage="plan",
            status="completed",
            summary="Created lightweight investigation plan.",
            payload={
                "todos": plan.todos,
                "required_evidence": plan.required_evidence,
                "required_params": [
                    {
                        "name": item.name,
                        "prompt": item.prompt,
                        "aliases": item.aliases,
                        "default": item.default,
                        "reason": item.reason,
                    }
                    for item in plan.required_params
                ],
                "focus_route": plan.focus_route,
                "available_tools": plan.available_tools,
                "history_turns": plan.history_turns,
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:plan",
        )

    def _verify_event(
        self,
        result: VerificationResult,
        *,
        state: HarnessState,
    ) -> dict[str, Any]:
        return make_agent_event(
            agent="harness",
            stage="verify",
            status=result.status,
            summary=result.summary,
            payload={
                "confidence": result.confidence,
                "evidence_count": result.evidence_count,
                "failed_evidence_count": result.failed_evidence_count,
                "gaps": result.gaps,
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:verify",
        )

    def _clarify_missing_params_event(
        self,
        request: ClarificationRequest,
        *,
        state: HarnessState,
    ) -> dict[str, Any]:
        return make_agent_event(
            agent="harness",
            stage="clarify_missing_params",
            status="degraded",
            summary="Missing required parameters; asking user for clarification.",
            payload={
                "missing_params": request.missing_params,
                "reason": request.reason,
                "evidence_gap": request.evidence_gap,
                "defaults": request.defaults,
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:clarify_missing_params",
        )

    async def _emit_clarification(
        self,
        request: ClarificationRequest,
        *,
        state: HarnessState,
        started: float,
    ) -> AsyncGenerator[dict[str, Any], None]:
        clarify_event = self._clarify_missing_params_event(request, state=state)
        state.timeline_events.append(clarify_event)
        state.append_answer(request.question)
        yield clarify_event
        # Explicit decision_event for frontend quick-fill chips (W10).
        yield {
            "type": "decision_event",
            "stage": "clarify",
            "status": "degraded",
            "summary": "Missing required parameters; waiting for user input.",
            "payload": {
                "missing_params": list(request.missing_params or []),
                "defaults": dict(request.defaults or {}),
                "reason": request.reason,
                "evidence_gap": request.evidence_gap,
                "question": request.question,
            },
            "trace_id": state.trace_id,
        }
        yield {
            "type": "content",
            "data": request.question,
            "agent": "harness",
        }
        complete_event = make_agent_event(
            agent="harness",
            stage="complete",
            status="completed",
            summary="Unified Harness is waiting for required user parameters.",
            payload={
                "answer_chars": len(state.answer),
                "steps": state.step,
                "token_estimate": state.token_estimate,
                "missing_params": list(request.missing_params or []),
                "clarification": {
                    "missing_params": list(request.missing_params or []),
                    "defaults": dict(request.defaults or {}),
                    "question": request.question,
                },
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}",
            duration_ms=(time.perf_counter() - started) * 1000,
            usage=state.usage_total or None,
        )
        state.timeline_events.append(complete_event)
        yield complete_event
        complete_payload = self._complete_event(state)
        complete_payload["missing_params"] = list(request.missing_params or [])
        complete_payload["clarification"] = {
            "missing_params": list(request.missing_params or []),
            "defaults": dict(request.defaults or {}),
            "question": request.question,
        }
        yield complete_payload

    @staticmethod
    def _apply_corrective_notice(answer: str, result: VerificationResult) -> str:
        """Surface verification gaps to the user instead of leaving them in a side panel."""
        gap_lines = "\n".join(f"> - {gap}" for gap in result.gaps)
        notice = (
            f"> ⚠️ 证据自检：置信度 {result.confidence}，"
            "本次回答存在以下证据缺口，请谨慎采用：\n"
            f"{gap_lines}"
        )
        return f"{notice}\n\n{answer}"

    def _complete_event(self, state: HarnessState) -> dict[str, Any]:
        events = [
            event for event in state.timeline_events if event.get("type") in TIMELINE_EVENT_TYPES
        ]
        return {
            "type": "complete",
            "route": state.route,
            "route_reason": state.route_reason,
            "answer": sanitize_user_visible_answer(state.answer),
            "case_id": state.case_id,
            "events": events,
        }

    async def _emit_context_rehydrate_status(
        self,
        *,
        state: HarnessState,
        stateful_ctx: Any | None,
        resume_context_ref: str | None,
        owner_key: str,
        session_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Emit rehydrate outcome when resuming with a context_snapshot_ref."""
        if not resume_context_ref:
            return
        source = ""
        warnings: list[str] = []
        if stateful_ctx is not None:
            source = str(getattr(stateful_ctx, "source", "") or "")
            warnings = list(getattr(stateful_ctx, "warnings", None) or [])
        if source == "redis_inflight":
            event = make_agent_event(
                agent="harness",
                stage="context_rehydrate",
                status="completed",
                summary="Context rehydrated from unified inflight recovery.",
                payload={
                    "source": source,
                    "context_snapshot_ref": resume_context_ref,
                    "warnings": warnings,
                },
                trace_id=session_id,
                span_id=f"harness:{session_id}:context_rehydrate",
            )
            state.timeline_events.append(event)
            yield event
            return
        fail_event = make_agent_event(
            agent="harness",
            stage="context_rehydrate_failed",
            status="degraded",
            summary="Unified inflight context was unavailable; using committed context.",
            payload={
                "context_snapshot_ref": resume_context_ref,
                "source": source or "fresh",
                "warnings": warnings,
            },
            trace_id=session_id,
            span_id=f"harness:{session_id}:context_rehydrate_failed",
        )
        state.timeline_events.append(fail_event)
        yield fail_event
