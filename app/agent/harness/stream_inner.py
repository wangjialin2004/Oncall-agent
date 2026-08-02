from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

from loguru import logger

from app.agent.agent_loop import tool_call_payload
from app.agent.events import make_agent_event, make_route_event
from app.agent.experts.registry import DEFAULT_ROUTE, EXPERT_ROUTES
from app.agent.harness.context import HarnessContext
from app.agent.harness.output_safety import (
    contains_internal_tool_protocol,
    sanitize_user_visible_answer,
)
from app.agent.harness.state import HarnessState
from app.agent.stream_common import CLARIFY_TEXT
from app.config import config
from app.core.llm_client import ChatMessage
from app.core.tool_calling import tool_to_definition
from app.services.attachment_reference_service import strip_attachment_wrapper
from app.services.harness_checkpoint import messages_from_dict


def stateful_context_enabled(*args, **kwargs):
    from app.agent.harness import loop as harness_loop

    fn = getattr(harness_loop, "stateful_context_enabled", None)
    if fn is not None and getattr(fn, "__module__", "") != __name__:
        return fn(*args, **kwargs)
    from app.agent.context.integration import stateful_context_enabled as _impl

    return _impl(*args, **kwargs)


async def build_stateful_context(*args, **kwargs):
    """Compatibility adapter that always loads the unified context envelope."""
    from app.agent.context.unified import load_unified_stateful_context

    allowed = {
        "owner_key",
        "session_id",
        "current_question",
        "current_goal",
        "repository",
        "active_attachment_refs",
        "token_budget",
        "history_limit",
    }
    filtered = {key: value for key, value in kwargs.items() if key in allowed}
    return await load_unified_stateful_context(*args, **filtered)


async def persist_stateful_context(*args, **kwargs):
    """Persist only recoverable in-flight unified context state."""
    from app.agent.context.unified import persist_runtime_state

    state = args[0] if args else kwargs.get("state")
    warnings, _ = await persist_runtime_state(state, store=kwargs.get("store"))
    return warnings

class HarnessStreamInnerMixin:
    """Owns ``_stream_inner`` — full harness turn orchestration."""

    async def _stream_inner(
        self,
        message: str,
        session_id: str,
        owner_key: str,
        checkpoint_replay: bool | None = None,
        attachment_refs: list[dict[str, Any]] | None = None,
        runtime: dict[str, Any] | None = None,
        simulate: str | None = None,
        prefer_parallel: bool | None = None,
        raw_question: str | None = None,
        context_run_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        state = HarnessState(trace_id=session_id, session_id=session_id, owner_key=owner_key)
        intent_question = strip_attachment_wrapper(raw_question or message)
        started = time.perf_counter()
        if runtime is not None:
            runtime["state"] = state
            runtime["messages"] = []
            runtime["context_state"] = None
            runtime["started"] = started
            runtime["raw_question"] = intent_question
            runtime["simulate"] = (simulate or "").strip()
            runtime["prefer_parallel"] = prefer_parallel
            runtime["context_run_id"] = context_run_id or session_id
        if self.llm_client is not None:
            client = self.llm_client
            owns_client = False
        else:
            # 复用进程级共享 LLMClient（连接池复用，省去重复的 TCP+TLS 握手）
            client = await self._new_llm_client()
            # 单例模式下不需要本地 aclose；lifespan 关闭时统一释放
            owns_client = False
        # Resume from a previous interrupted run, if a valid checkpoint exists.
        # When this branch fires, ``messages`` and the state snapshot are
        # restored; we also surface a ``checkpoint_resume`` event so the SSE
        # client knows to discard earlier timeline events from the prior run.
        resume_from_step = 0
        resume_messages: list[ChatMessage] | None = None
        resume_close_only = False
        stateful_ctx = None
        resume_context_version: int | None = None
        resume_context_ref: str | None = None
        if (
            self.checkpoint_store is not None
            and self.checkpoint_store.is_enabled()
            and owner_key
        ):
            resume = await self.checkpoint_store.try_resume(owner_key, session_id)
            if resume is not None and not resume.completed:
                restored_messages = messages_from_dict(resume.messages)
                if restored_messages:
                    resume_messages = restored_messages
                # Stateful checkpoints intentionally persist no messages. The
                # state snapshot must still be restored before context rehydrate.
                state = self._restore_state_from_resume(resume, state)
                if runtime is not None:
                    runtime["state"] = state
                resume_context_version = getattr(resume, "context_version", None)
                resume_context_ref = getattr(resume, "context_snapshot_ref", None)
                if self._should_replay_resume(resume, replay_override=checkpoint_replay):
                    resume_from_step = max(0, int(resume.next_step) - 1)
                else:
                    # Explicit close-only (request checkpoint_replay=False).
                    resume_close_only = True
                yield make_agent_event(
                    agent="harness",
                    stage="checkpoint_resume",
                    status="completed",
                    summary=(
                        f"Resumed from checkpoint step {resume.next_step - 1} ({len(resume.steps)} persisted steps)."
                    ),
                    payload={
                        "resumed_from_step": int(resume.next_step - 1),
                        "replayed_steps": len(resume.steps),
                        "started_at": resume.started_at,
                        # True only when this resume will skip the tool loop.
                        "conservative": bool(resume_close_only),
                        "continue_from_next_step": not bool(resume_close_only),
                        "replay_override": (
                            True
                            if checkpoint_replay is True
                            else (False if checkpoint_replay is False else None)
                        ),
                        "context_version": resume_context_version,
                        "context_snapshot_ref": resume_context_ref,
                    },
                    trace_id=session_id,
                    span_id=f"harness:{session_id}:checkpoint_resume",
                )
        try:
            route_progress = self._progress_event(
                state=state,
                stage="route",
                summary="Identifying route and selecting expert.",
                payload={"message_chars": len(message)},
            )
            yield route_progress
            # Bound to a fresh list first so the except branch's checkpoint save
            # has something to serialize even if the run explodes before the
            # main ``messages = [...]`` assignment further below.
            messages = []
            previous_route = None
            if owner_key and session_id:
                try:
                    from app.services.conversation_service import conversation_service

                    previous_route = conversation_service.get_last_route(owner_key, session_id)
                except Exception:
                    previous_route = None
            route_decision = await self.router._resolve_route(
                message,
                previous_route=previous_route,
            )
            state.route = (
                route_decision.route if route_decision.route != "clarify" else DEFAULT_ROUTE
            )
            state.route_reason = f"harness_focus:{route_decision.reason}"
            route_event = make_route_event(
                route=state.route,
                reason=state.route_reason,
                confidence=route_decision.confidence,
                candidates=list(EXPERT_ROUTES),
                payload={
                    "mode": "harness",
                    "focus_route": route_decision.route,
                    "previous_route": previous_route,
                    "continuation_inherited": str(route_decision.reason or "").startswith(
                        "continuation_inherit_"
                    ),
                },
                trace_id=session_id,
            )
            state.timeline_events.append(route_event)
            yield route_event

            if route_decision.route == "clarify":
                clarify_text = CLARIFY_TEXT
                state.append_answer(clarify_text)
                yield {"type": "content", "data": clarify_text, "agent": "harness"}
                yield self._complete_event(state)
                return

            context_progress = self._progress_event(
                state=state,
                stage="context",
                summary="Loading context, preferences, and tools.",
                payload={"route": state.route},
            )
            yield context_progress

            context_ref = {"value": ""}
            catalog = await self.tool_registry.collect(
                route=state.route,
                session_id=session_id,
                trace_id=session_id,
                context_getter=lambda: context_ref["value"],
            )
            tools = catalog.tools
            focus_hint = (
                f"candidate route: {route_decision.route}; reason: {route_decision.reason}; "
                f"置信度：{route_decision.confidence:.2f}"
            )
            from app.agent.context.integration import StatefulContext
            from app.agent.context.unified import (
                prepare_unified_context,
                render_unified_envelope,
            )

            use_stateful_renderer = stateful_context_enabled()
            base_prompt = self.context_builder._build_system_prompt(
                owner_key=owner_key,
                tools=tools,
                focus_hint=focus_hint,
            )
            loaded = await prepare_unified_context(
                owner_key=owner_key,
                session_id=session_id,
                current_question=intent_question,
                current_goal=intent_question,
                repository=self.context_repository,
                active_attachment_refs=attachment_refs,
                token_budget=int(
                    getattr(config, "harness_context_view_token_budget", 4000)
                ),
                history_limit=int(
                    getattr(config, "harness_context_patch_history_limit", 200)
                ),
                stateful=use_stateful_renderer,
                base_prompt=base_prompt,
                run_id=context_run_id or session_id,
                resume_context_ref=resume_context_ref,
                resume_context_version=resume_context_version,
            )
            if use_stateful_renderer:
                context_tools = self.tool_registry.context_tools(
                    loaded.state,
                    whiteboard_injected=True,
                )
                if context_tools:
                    tools = [*tools, *context_tools]
                    base_prompt = self.context_builder._build_system_prompt(
                        owner_key=owner_key,
                        tools=tools,
                        focus_hint=focus_hint,
                    )
                    loaded.rendered = render_unified_envelope(
                        loaded.envelope,
                        message=intent_question,
                        stateful=True,
                        base_prompt=base_prompt,
                    )
            stateful_ctx = StatefulContext(
                state=loaded.state,
                view=loaded.rendered.view,
                recent_messages=[item.to_dict() for item in loaded.rendered.history_messages],
                source=str(loaded.envelope.source),
                warnings=list(loaded.envelope.warnings),
            )
            context = HarnessContext(
                system_prompt=loaded.rendered.system_prompt,
                history_messages=list(loaded.rendered.history_messages),
            )
            await persist_stateful_context(
                stateful_ctx.state,
                store=self.context_store,
                persist_snapshot=False,
            )
            if runtime is not None:
                runtime["context_state"] = stateful_ctx.state
            if resume_context_ref:
                async for event in self._emit_context_rehydrate_status(
                    state=state,
                    stateful_ctx=stateful_ctx,
                    resume_context_ref=resume_context_ref,
                    owner_key=owner_key,
                    session_id=session_id,
                ):
                    yield event
            context_ref["value"] = context.system_prompt
            state.add_text_budget(context.system_prompt)
            for history_message in context.history_messages:
                state.add_text_budget(history_message.content)
            state.add_text_budget(message)

            start_event = make_agent_event(
                agent="harness",
                stage="start",
                status="in_progress",
                summary="Unified Harness main loop started.",
                payload={
                    "history_turns": len(context.history_messages) // 2,
                    "tool_count": len(tools),
                    "max_steps": self.limits.max_steps,
                },
                trace_id=session_id,
                span_id=f"harness:{session_id}",
            )
            state.timeline_events.append(start_event)
            yield start_event

            planning_progress = self._progress_event(
                state=state,
                stage="planning",
                summary="Creating investigation plan and evidence requirements.",
                payload={"route": state.route, "tool_count": len(tools)},
            )
            yield planning_progress

            plan = await self.planner.acreate(
                message=message,
                route_decision=route_decision,
                tools=tools,
                history_turns=len(context.history_messages) // 2,
                llm_client=client,
            )
            plan_event = self._make_plan_event(plan, state=state)
            state.timeline_events.append(plan_event)
            yield plan_event

            pending_clarification = self.clarifier.check(
                message=message,
                plan=plan,
                tools=tools,
                history_messages=context.history_messages,
            )
            if pending_clarification is not None and not self._has_investigation_tools(tools):
                async for event in self._emit_clarification(
                    pending_clarification, state=state, started=started
                ):
                    yield event
                if stateful_ctx is not None:
                    await persist_stateful_context(
                        stateful_ctx.state,
                        store=self.context_store,
                        persist_snapshot=True,
                    )
                return

            messages = (
                list(resume_messages)
                if resume_messages is not None
                else [
                    ChatMessage(role="system", content=context.system_prompt),
                    *context.history_messages,
                    ChatMessage(role="user", content=message),
                ]
            )
            if runtime is not None:
                if bool(getattr(config, "harness_context_size_metrics_enabled", False)):
                    runtime["context_metrics"] = {
                        "system_chars": len(context.system_prompt or ""),
                        "history_chars": sum(
                            len(item.content or "") for item in context.history_messages
                        ),
                        "user_chars": len(message or ""),
                        "view_chars": len(getattr(stateful_ctx, "view", "") or ""),
                    }
                runtime["messages"] = messages
                runtime["state"] = state
                if stateful_ctx is not None:
                    runtime["context_state"] = stateful_ctx.state
            # Keep context whiteboard helpers available to the model. They are
            # filtered out of plan.available_tools / early-clarify gates via
            # ``_has_investigation_tools`` so zero-evidence verify still works.
            tool_defs = [tool_to_definition(tool) for tool in tools]
            answer = ""
            answer_streamed = False
            seen_signatures: set[str] = set()
            no_progress_streak = 0
            re_evidence_rounds_used = 0
            replan_times_used = 0
            aux_routes = tuple(
                str(r).strip()
                for r in (getattr(route_decision, "aux_routes", ()) or ())
                if str(r).strip()
            )
            effective_max_steps = self._effective_max_steps(state.route)

            # M3 W9 eval fault inject first so seeds / loop see failing tools.
            simulate_tag = str(
                (simulate if simulate is not None else (runtime or {}).get("simulate")) or ""
            ).strip()
            if simulate_tag:
                tools = self._apply_simulate_faults(tools, simulate=simulate_tag, state=state)
                tool_defs = [tool_to_definition(tool) for tool in tools]

            if resume_close_only:
                yield self._progress_event(
                    state=state,
                    stage="checkpoint_conservative_close",
                    summary=(
                        "Resumed from checkpoint in close-only mode; "
                        "finalizing from saved evidence without further tools."
                    ),
                    payload={"step": state.step, "checkpoint_resume": True},
                )
            elif (
                resume_messages is None
                and "delegation_off" not in simulate_tag.lower()
                and self._should_force_seed_parallel(
                    message=message,
                    route=state.route,
                    tools=tools,
                    aux_routes=aux_routes,
                    prefer_parallel=prefer_parallel
                    if prefer_parallel is not None
                    else (runtime or {}).get("prefer_parallel"),
                )
            ):
                async for event in self._seed_force_parallel(
                    message=message,
                    tools=tools,
                    messages=messages,
                    client=client,
                    state=state,
                    context_state=(stateful_ctx.state if stateful_ctx is not None else None),
                ):
                    yield event
            elif (
                resume_messages is None
                and "delegation_off" not in simulate_tag.lower()
                and self._should_force_seed_delegate(route=state.route, tools=tools)
            ):
                async for event in self._seed_force_delegate(
                    message=message,
                    route=state.route,
                    tools=tools,
                    messages=messages,
                    client=client,
                    state=state,
                    context_state=(stateful_ctx.state if stateful_ctx is not None else None),
                ):
                    yield event

            if resume_messages is None and not resume_close_only and aux_routes:
                async for event in self._maybe_run_aux_probes(
                    message=message,
                    aux_routes=aux_routes,
                    primary_route=state.route,
                    messages=messages,
                    state=state,
                    context_state=(stateful_ctx.state if stateful_ctx is not None else None),
                ):
                    yield event

            loop_start = self.limits.max_steps if resume_close_only else resume_from_step
            for step_index in range(loop_start, effective_max_steps):
                state.step = step_index + 1
                if state.over_budget(self.limits):
                    budget_event = make_agent_event(
                        agent="harness",
                        stage="budget",
                        status="degraded",
                        summary="Harness token budget is near the limit; closing without tools.",
                        payload={"token_budget": self.limits.token_budget},
                        trace_id=session_id,
                    )
                    state.timeline_events.append(budget_event)
                    yield budget_event
                    closing_progress = self._progress_event(
                        state=state,
                        stage="model_closing",
                        summary="Token budget is near the limit; asking model to close without tools.",
                        payload={"step": state.step},
                    )
                    yield closing_progress
                    async for chunk in self._stream_final_answer(
                        client=client,
                        messages=messages,
                        temperature=0.3,
                        model=self._reasoner_model(),
                    ):
                        answer += chunk
                        yield {"type": "content", "data": chunk, "agent": "harness"}
                    answer_streamed = True
                    break

                # Mid-loop replan: primary investigation tools failed and no success yet.
                if self._should_replan(
                    replan_times_used=replan_times_used,
                    resume_close_only=resume_close_only,
                    state=state,
                    verification_gaps=None,
                    force_after_re_evidence=False,
                    aux_routes=aux_routes,
                    plan=plan,
                ):
                    plan, replan_event = self._apply_replan(
                        plan=plan,
                        state=state,
                        messages=messages,
                        trigger="primary_tool_failure",
                        gaps=self._collect_recent_tool_failure_summaries(state.timeline_events),
                        failed_tools=self._failed_investigation_tools(state.timeline_events),
                        aux_routes=aux_routes,
                    )
                    replan_times_used += 1
                    yield replan_event

                model_progress = self._progress_event(
                    state=state,
                    stage="model_decision",
                    summary="Calling model to decide the next action.",
                    payload={
                        "step": state.step,
                        "tool_count": len(tool_defs),
                        "history_messages": len(messages),
                        "effective_max_steps": effective_max_steps,
                        "replan_times_used": replan_times_used,
                    },
                )
                yield model_progress

                step_tools = self._filter_tools_by_cap(tools, state)
                step_tool_defs = [tool_to_definition(tool) for tool in step_tools]
                if not step_tool_defs:
                    async for chunk in self._stream_final_answer(
                        client=client,
                        messages=messages,
                        temperature=0.3,
                        model=self._reasoner_model(),
                    ):
                        answer += chunk
                        yield {"type": "content", "data": chunk, "agent": "harness"}
                    answer_streamed = True
                    break

                response = None
                decision_text = ""
                decision_parts: tuple[str, ...] = ()
                step_timed_out = False
                try:
                    async with asyncio.timeout(self.limits.step_timeout_seconds):
                        response, decision_text, decision_parts = await self._collect_chat_turn(
                            client=client,
                            messages=messages,
                            tools=step_tool_defs or None,
                            tool_choice="auto" if step_tool_defs else None,
                            temperature=0.3,
                            model=self._planner_model(),
                        )
                except TimeoutError:
                    step_timed_out = True
                    step_timeout_event = make_agent_event(
                        agent="harness",
                        stage="step_timeout",
                        status="degraded",
                        summary=f"Single-step LLM decision exceeded {self.limits.step_timeout_seconds}s; closing early.",
                        payload={
                            "step": state.step,
                            "step_timeout_seconds": self.limits.step_timeout_seconds,
                        },
                        trace_id=session_id,
                        span_id=f"harness:{session_id}:step_timeout",
                    )
                    state.timeline_events.append(step_timeout_event)
                    yield step_timeout_event
                if step_timed_out:
                    break
                if response is None:
                    break
                state.add_usage(response.usage)

                if not response.tool_calls:
                    candidate = str(response.content or decision_text or "")
                    if contains_internal_tool_protocol(candidate):
                        protocol_event = make_agent_event(
                            agent="harness",
                            stage="tool_protocol_degraded",
                            status="degraded",
                            summary=(
                                "Model returned textual tool protocol instead of "
                                "structured tool calls; discarded it and closed safely."
                            ),
                            payload={"step": state.step},
                            trace_id=session_id,
                            span_id=f"harness:{session_id}:tool_protocol_degraded:{state.step}",
                        )
                        state.timeline_events.append(protocol_event)
                        yield protocol_event
                        async for chunk in self._stream_final_answer(
                            client=client,
                            messages=messages,
                            temperature=0.3,
                            model=self._reasoner_model(),
                        ):
                            answer += chunk
                            answer_streamed = True
                            yield {"type": "content", "data": chunk, "agent": "harness"}
                    else:
                        clean_candidate = sanitize_user_visible_answer(candidate)
                        if clean_candidate:
                            answer = clean_candidate
                            answer_streamed = True
                            if decision_parts and clean_candidate == candidate.strip():
                                for part in decision_parts:
                                    yield {
                                        "type": "content",
                                        "data": part,
                                        "agent": "harness",
                                    }
                            else:
                                yield {
                                    "type": "content",
                                    "data": clean_candidate,
                                    "agent": "harness",
                                }
                    break

                step_signatures = {self._tool_signature(tc) for tc in response.tool_calls}
                if step_signatures and step_signatures.issubset(seen_signatures):
                    no_progress_streak += 1
                else:
                    no_progress_streak = 0
                seen_signatures |= step_signatures

                if no_progress_streak >= self.limits.no_progress_limit:
                    no_progress_event = make_agent_event(
                        agent="harness",
                        stage="no_progress",
                        status="degraded",
                        summary="Repeated tool calls produced no new evidence; closing early.",
                        payload={
                            "repeated_signatures": sorted(step_signatures),
                            "streak": no_progress_streak,
                        },
                        trace_id=session_id,
                        span_id=f"harness:{session_id}:no_progress",
                    )
                    state.timeline_events.append(no_progress_event)
                    yield no_progress_event
                    closing_progress = self._progress_event(
                        state=state,
                        stage="model_closing",
                        summary="Repeated tool calls detected; asking model to close from existing evidence.",
                        payload={"step": state.step},
                    )
                    yield closing_progress
                    async for chunk in self._stream_final_answer(
                        client=client,
                        messages=messages,
                        temperature=0.3,
                        model=self._reasoner_model(),
                    ):
                        answer += chunk
                        yield {"type": "content", "data": chunk, "agent": "harness"}
                    answer_streamed = True
                    break

                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=[tool_call_payload(tc) for tc in response.tool_calls],
                    )
                )
                async for event in self._execute_tools(
                    response.tool_calls,
                    step_tools,
                    messages,
                    client=client,
                    state=state,
                    context_state=stateful_ctx.state if stateful_ctx is not None else None,
                ):
                    yield event
                if stateful_ctx is not None:
                    await persist_stateful_context(
                        stateful_ctx.state,
                        store=self.context_store,
                        persist_snapshot=True,
                    )
                # Persist the snapshot for this completed step. Fire-and-forget
                # so Redis IO never blocks the SSE response.
                self._schedule_checkpoint_save(
                    state=state,
                    messages=messages,
                    step_index=state.step,
                    tool_calls=[tool_call_payload(tc) for tc in response.tool_calls],
                    context_state=(stateful_ctx.state if stateful_ctx is not None else None),
                )

                # Knowledge early close: once a knowledge-class tool succeeds, force
                # a no-tool final answer instead of spending remaining steps.
                if self._should_knowledge_early_close(state=state):
                    early_event = make_agent_event(
                        agent="harness",
                        stage="knowledge_early_close",
                        status="completed",
                        summary=(
                            "Knowledge route already has successful retrieval evidence; "
                            "closing without further tools."
                        ),
                        payload={"step": state.step, "route": state.route},
                        trace_id=session_id,
                        span_id=f"harness:{session_id}:knowledge_early_close",
                    )
                    state.timeline_events.append(early_event)
                    yield early_event
                    closing_progress = self._progress_event(
                        state=state,
                        stage="model_closing",
                        summary="Knowledge evidence present; asking model to close without tools.",
                        payload={"step": state.step, "early_close": True},
                    )
                    yield closing_progress
                    async for chunk in self._stream_final_answer(
                        client=client,
                        messages=messages,
                        temperature=0.3,
                        model=self._reasoner_model(),
                    ):
                        answer += chunk
                        yield {"type": "content", "data": chunk, "agent": "harness"}
                    answer_streamed = True
                    break

                # A delegated expert or direct tool turn can already produce a
                # complete read-only evidence bundle. Do not spend additional
                # planner turns merely to rediscover it: close to the usual
                # answer/verify path, where gaps may still trigger re-evidence
                # and replan.
                if self._should_investigation_evidence_early_close(state=state):
                    early_event = make_agent_event(
                        agent="harness",
                        stage="investigation_evidence_early_close",
                        status="completed",
                        summary=(
                            "Route has successful investigation evidence; "
                            "closing without further planner turns."
                        ),
                        payload={"step": state.step, "route": state.route},
                        trace_id=session_id,
                        span_id=f"harness:{session_id}:investigation_evidence_early_close",
                    )
                    state.timeline_events.append(early_event)
                    yield early_event
                    closing_progress = self._progress_event(
                        state=state,
                        stage="model_closing",
                        summary="Investigation evidence present; asking model to close without tools.",
                        payload={"step": state.step, "early_close": True},
                    )
                    yield closing_progress
                    async for chunk in self._stream_final_answer(
                        client=client,
                        messages=messages,
                        temperature=0.3,
                        model=self._reasoner_model(),
                    ):
                        answer += chunk
                        yield {"type": "content", "data": chunk, "agent": "harness"}
                    answer_streamed = True
                    break
            else:
                closing_progress = self._progress_event(
                    state=state,
                    stage="model_closing",
                    summary="Reached max steps; asking model for final answer without tools.",
                    payload={
                        "step": state.step,
                        "effective_max_steps": effective_max_steps,
                    },
                )
                yield closing_progress
                async for chunk in self._stream_final_answer(
                    client=client,
                    messages=messages,
                    temperature=0.3,
                    model=self._reasoner_model(),
                ):
                    answer += chunk
                    yield {"type": "content", "data": chunk, "agent": "harness"}
                answer_streamed = True

            if pending_clarification is not None and not self._has_successful_tool_evidence(
                state.timeline_events
            ):
                async for event in self._emit_clarification(
                    pending_clarification, state=state, started=started
                ):
                    yield event
                if stateful_ctx is not None:
                    await persist_stateful_context(
                        stateful_ctx.state,
                        store=self.context_store,
                        persist_snapshot=True,
                    )
                return
            async for event in self._finish_after_steps(
                message=message,
                session_id=session_id,
                owner_key=owner_key,
                state=state,
                client=client,
                messages=messages,
                tools=tools,
                tool_defs=tool_defs,
                plan=plan,
                answer=answer,
                answer_streamed=answer_streamed,
                re_evidence_rounds_used=re_evidence_rounds_used,
                replan_times_used=replan_times_used,
                resume_close_only=resume_close_only,
                stateful_ctx=stateful_ctx,
                runtime=runtime,
                started=started,
                aux_routes=aux_routes,
            ):
                yield event
        except Exception as exc:
            logger.error(f"harness 执行失败: {exc}", exc_info=True)
            # Best-effort: keep the partial snapshot so the user can resume.
            self._schedule_checkpoint_save(
                state=state,
                messages=messages,
                step_index=state.step,
                tool_calls=[],
                context_state=stateful_ctx.state if stateful_ctx is not None else None,
            )
            error_event = make_agent_event(
                agent="harness",
                stage="error",
                status="degraded",
                summary=f"Harness 主循环执行出错：{exc}",
                payload={"error": str(exc)},
                trace_id=session_id,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=state.usage_total or None,
            )
            state.timeline_events.append(error_event)
            try:
                async with asyncio.timeout(self.limits.fallback_timeout_seconds):
                    async for event in self._fallback_stream(
                        message=message,
                        session_id=session_id,
                        owner_key=owner_key,
                        reason="harness_error",
                        seed_events=state.timeline_events,
                    ):
                        yield event
            except TimeoutError:
                logger.warning(
                    f"harness fallback path also timed out after {self.limits.fallback_timeout_seconds}s; returning final fallback"
                )
                yield self._final_fallback_complete_event(
                    message=message,
                    session_id=session_id,
                    reason="harness_error_fallback_timeout",
                    timeout_seconds=self.limits.fallback_timeout_seconds,
                    seed_events=state.timeline_events,
                )
        finally:
            if owns_client:
                await client.aclose()
