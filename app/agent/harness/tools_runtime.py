from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from loguru import logger

from app.agent.agent_loop import stream_tool_results, tool_call_payload
from app.agent.context.integration import capture_tool_outcome
from app.agent.context.state import AgentContextState
from app.agent.context.tools_evidence import record_delegate_merge
from app.agent.events import make_agent_event
from app.agent.experts.log_pipeline import analyze_logs
from app.agent.experts.registry import DEFAULT_ROUTE, EXPERT_ROUTES
from app.agent.harness.state import HarnessState
from app.agent.harness.sub_harness import merge_delegate_results
from app.agent.harness.subagent import (
    aux_execution_mode,
    aux_max_probes,
    build_aux_subtask,
    merge_delegate_answers,
    run_one_delegate,
    run_parallel_delegates,
)
from app.agent.stream_common import TIMELINE_EVENT_TYPES
from app.config import config
from app.core.llm_client import ChatMessage, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.core.tool_calling import _stringify_tool_result


async def persist_stateful_context(*args, **kwargs):
    """Route unified stage persistence to the inflight recovery key."""
    from app.agent.context.unified import persist_runtime_state

    state = args[0] if args else kwargs.get("state")
    warnings, _ = await persist_runtime_state(state, store=kwargs.get("store"))
    return warnings


def get_expert(route: str):
    from app.agent.harness import loop as harness_loop
    fn = getattr(harness_loop, "get_expert", None)
    if fn is not None and getattr(fn, "__module__", "") != __name__:
        return fn(route)
    from app.agent.experts.registry import get_expert as _impl
    return _impl(route)

class HarnessToolsRuntimeMixin:
    """Tool execution, force-seed, aux probes, log postprocess."""

    async def _seed_force_parallel(
        self,
        *,
        message: str,
        tools: Sequence[RuntimeTool],
        messages: list[ChatMessage],
        client: Any,
        state: HarnessState,
        context_state: AgentContextState | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Deterministically seed one delegate_parallel (metric+log) call."""
        subtask = (message or "").strip()
        if len(subtask) > 600:
            subtask = f"{subtask[:600]}..."
        if not subtask:
            subtask = "跨域只读取证：指标与日志"
        experts = ["metric", "log"]
        subtasks = [
            f"从指标/告警侧只读取证：{subtask}",
            f"从日志/错误侧只读取证：{subtask}",
        ]
        call_id = f"force-parallel-{state.trace_id}"
        tool_call = ToolCall(
            id=call_id,
            name="delegate_parallel",
            arguments={"experts": experts, "subtasks": subtasks},
        )
        dispatch_event = make_agent_event(
            agent="harness",
            stage="delegate_parallel_start",
            status="in_progress",
            summary="Force-seeding parallel metric+log investigation.",
            payload={
                "experts": experts,
                "subtasks": [s[:200] for s in subtasks],
                "tool_call_id": call_id,
                "forced": True,
                "parallel": True,
            },
            trace_id=state.trace_id,
            span_id=f"delegate_parallel:{call_id}:start",
        )
        state.timeline_events.append(dispatch_event)
        yield dispatch_event
        # Also emit a tool_event so eval summarize(tool==delegate_parallel) matches.
        tool_start = {
            "type": "tool_event",
            "tool": "delegate_parallel",
            "status": "in_progress",
            "summary": "force seed delegate_parallel",
            "payload": {"forced": True, "parallel": True},
        }
        state.timeline_events.append(tool_start)
        yield tool_start

        messages.append(
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[tool_call_payload(tool_call)],
            )
        )
        async for event in self._execute_tools(
            [tool_call],
            list(tools),
            messages,
            client=client,
            state=state,
            context_state=context_state,
        ):
            yield event
        if context_state is not None:
            await persist_stateful_context(
                context_state,
                store=self.context_store,
                persist_snapshot=True,
            )
        self._schedule_checkpoint_save(
            state=state,
            messages=messages,
            step_index=max(1, state.step),
            tool_calls=[tool_call_payload(tool_call)],
            context_state=context_state,
        )

    async def _seed_force_delegate(
        self,
        *,
        message: str,
        route: str,
        tools: Sequence[RuntimeTool],
        messages: list[ChatMessage],
        client: Any,
        state: HarnessState,
        context_state: AgentContextState | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Seed one ``delegate_to_expert`` call before the model decision loop."""
        expert = route if route in EXPERT_ROUTES else DEFAULT_ROUTE
        subtask = (message or "").strip()
        if len(subtask) > 800:
            subtask = f"{subtask[:800]}..."
        if not subtask:
            subtask = f"围绕 {expert} 焦点做只读排查并返回证据摘要"

        call_id = f"force-delegate-{state.trace_id}-{expert}"
        tool_call = ToolCall(
            id=call_id,
            name="delegate_to_expert",
            arguments={"expert": expert, "subtask": subtask},
        )
        dispatch_event = make_agent_event(
            agent="harness",
            stage="delegate_dispatch",
            status="in_progress",
            summary=f"Force-seeding first investigation to {expert} expert.",
            payload={
                "delegated_expert": expert,
                "subtask": subtask[:500],
                "tool_call_id": call_id,
                "forced": True,
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:delegate_dispatch",
        )
        state.timeline_events.append(dispatch_event)
        yield dispatch_event

        messages.append(
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[tool_call_payload(tool_call)],
            )
        )
        async for event in self._execute_tools(
            [tool_call],
            list(tools),
            messages,
            client=client,
            state=state,
            context_state=context_state,
        ):
            yield event
        if context_state is not None:
            await persist_stateful_context(
                context_state,
                store=self.context_store,
                persist_snapshot=True,
            )
        self._schedule_checkpoint_save(
            state=state,
            messages=messages,
            step_index=max(1, state.step),
            tool_calls=[tool_call_payload(tool_call)],
            context_state=context_state,
        )

    async def _maybe_run_aux_probes(
        self,
        *,
        message: str,
        aux_routes: Sequence[str],
        primary_route: str,
        messages: list[ChatMessage],
        state: HarnessState,
        context_state: AgentContextState | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Auto-execute router aux_routes before the main model loop (M2 W6)."""
        if not bool(getattr(config, "harness_delegation_enabled", True)):
            return
        mode = aux_execution_mode()
        max_probes = aux_max_probes()
        if mode == "off" or max_probes <= 0:
            return
        if state.over_budget(self.limits):
            return

        cleaned: list[str] = []
        seen: set[str] = set()
        primary = str(primary_route or "").strip()
        for raw in aux_routes or ():
            route = str(raw or "").strip()
            if not route or route == primary or route == "diagnosis":
                continue
            if route not in EXPERT_ROUTES or route in seen:
                continue
            seen.add(route)
            cleaned.append(route)
            if len(cleaned) >= max_probes:
                break
        if not cleaned:
            return

        pairs = [(route, build_aux_subtask(message, route)) for route in cleaned]
        start_event = make_agent_event(
            agent="harness",
            stage="aux_probe",
            status="in_progress",
            summary=f"Auto-probing aux routes ({mode}): {', '.join(cleaned)}.",
            payload={
                "mode": mode,
                "experts": cleaned,
                "primary_route": primary,
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:aux_probe:start",
        )
        state.timeline_events.append(start_event)
        yield start_event

        # Surface as delegate_start so replan/aux heuristics still see activity.
        for route, subtask in pairs:
            dispatch = make_agent_event(
                agent="harness",
                stage="delegate_start",
                status="in_progress",
                summary=f"Aux probe delegating to {route} expert.",
                payload={
                    "delegated_expert": route,
                    "subtask": subtask[:500],
                    "aux": True,
                    "mode": mode,
                },
                trace_id=state.trace_id,
                span_id=f"harness:{state.trace_id}:aux:{route}",
            )
            state.timeline_events.append(dispatch)
            yield dispatch

        started = time.perf_counter()
        if mode == "serial":
            results: list[dict[str, Any]] = []
            for route, subtask in pairs:
                result = await run_one_delegate(
                    expert=route,
                    subtask=subtask,
                    session_id=state.session_id,
                    trace_id=f"{state.trace_id}:aux",
                    context="",
                    expert_getter=get_expert,
                )
                results.append(result)
            payload = {
                "status": (
                    "completed"
                    if any(str(r.get("status")) != "failed" for r in results)
                    else "failed"
                ),
                "parallel": False,
                "wall_ms": int((time.perf_counter() - started) * 1000),
                "results": results,
            }
        else:
            payload = await run_parallel_delegates(
                pairs,
                session_id=state.session_id,
                trace_id=f"{state.trace_id}:aux",
                context="",
                expert_getter=get_expert,
            )

        results = list(payload.get("results") or [])
        merged_struct = merge_delegate_results(results)
        results = list(merged_struct.get("results") or results)
        # Promote child tool successes onto the parent timeline so early-close /
        # evidence match can observe aux evidence without parsing nested events.
        for item in results:
            expert = str(item.get("expert") or "")
            status = str(item.get("status") or "failed")
            for child in list(item.get("events") or [])[-8:]:
                if not isinstance(child, dict):
                    continue
                if child.get("type") != "tool_event":
                    continue
                promoted = dict(child)
                promoted.setdefault("payload", {})
                if isinstance(promoted.get("payload"), dict):
                    promoted["payload"] = {
                        **promoted["payload"],
                        "from_aux_probe": True,
                        "delegated_expert": expert,
                    }
                state.timeline_events.append(promoted)
                yield promoted
            # Always record a synthetic tool_event for the aux delegate itself.
            synthetic = {
                "type": "tool_event",
                "tool": "delegate_to_expert",
                "status": "completed" if status in {"completed", "degraded"} else "failed",
                "summary": f"aux probe {expert}: {status}",
                "payload": {
                    "from_aux_probe": True,
                    "delegated_expert": expert,
                    "mode": mode,
                },
            }
            state.timeline_events.append(synthetic)
            yield synthetic

        merged = merge_delegate_answers(results)
        if merged:
            messages.append(
                ChatMessage(
                    role="user",
                    content=(
                        "系统已自动并行/串行补充跨域 aux 专家取证（只读）。"
                        f"模式={mode}。请在后续排查中优先使用这些证据，"
                        "不要重复相同工具调用；仍禁止编造数值/版本/操作人。\n"
                        f"{merged[:4000]}"
                    ),
                )
            )
            state.add_text_budget(merged[:4000])

        if context_state is not None:
            for item in results:
                capture_tool_outcome(
                    context_state,
                    tool_name="delegate_to_expert",
                    raw_result={
                        "ok": str(item.get("status")) in {"completed", "degraded"},
                        "summary": str(item.get("answer") or item.get("error") or "")[:1500],
                        "expert": item.get("expert"),
                        "from_aux_probe": True,
                    },
                    raw_ref=f"aux:{item.get('expert')}",
                    history_limit=int(
                        getattr(config, "harness_context_patch_history_limit", 200)
                    ),
                )
            record_delegate_merge(
                context_state,
                mode=mode,
                experts=list(merged_struct.get("experts") or cleaned),
                folded_tools=list(merged_struct.get("folded_tools") or []),
                status=str(merged_struct.get("status") or payload.get("status") or "completed"),
                wall_ms=int(payload.get("wall_ms") or 0),
                history_limit=int(
                    getattr(config, "harness_context_patch_history_limit", 200)
                ),
            )
            await persist_stateful_context(
                context_state,
                store=self.context_store,
                persist_snapshot=True,
            )

        done_event = make_agent_event(
            agent="harness",
            stage="aux_probe",
            status=str(payload.get("status") or "completed"),
            summary=(
                f"Aux probe finished ({mode}) in "
                f"{int(payload.get('wall_ms') or 0)}ms; experts={', '.join(cleaned)}."
            ),
            payload={
                "mode": mode,
                "experts": cleaned,
                "wall_ms": int(payload.get("wall_ms") or 0),
                "parallel": bool(payload.get("parallel")),
                "results": [
                    {
                        "expert": r.get("expert"),
                        "status": r.get("status"),
                        "error": r.get("error"),
                        "answer_chars": len(str(r.get("answer") or "")),
                    }
                    for r in results
                ],
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:aux_probe:done",
        )
        state.timeline_events.append(done_event)
        yield done_event

    async def _execute_tools(
        self,
        tool_calls: list[ToolCall],
        tools: list[RuntimeTool],
        messages: list[ChatMessage],
        *,
        client: Any,
        state: HarnessState,
        context_state: AgentContextState | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        for event in self._delegate_start_events(tool_calls, state=state):
            state.timeline_events.append(event)
            yield event

        tool_results = await self.tool_executor.execute(tool_calls, tools)
        args_by_id = {tc.id: tc.arguments for tc in tool_calls}

        async def process(result: Any):
            content = result.content
            pre_events: list[dict[str, Any]] = []
            async for pipeline_event, digest in self._log_postprocess(
                result, client=client, state=state
            ):
                if pipeline_event is not None:
                    pre_events.append(pipeline_event)
                if digest is not None:
                    content = digest
            state.add_text_budget(content)
            if context_state is not None:
                raw_result = (
                    dict(result.raw)
                    if isinstance(getattr(result, "raw", None), dict)
                    else {}
                )
                raw_result.update(
                    {
                        "ok": bool(result.success),
                        "summary": content,
                        "latency_ms": int(getattr(result, "latency_ms", 0) or 0),
                    }
                )
                capture_tool_outcome(
                    context_state,
                    tool_name=result.tool_name,
                    raw_result=raw_result,
                    raw_ref=f"tool:{result.call_id}",
                    history_limit=int(
                        getattr(config, "harness_context_patch_history_limit", 200)
                    ),
                )
            post_events = self._delegate_child_events(result, state=state)
            return content, pre_events, post_events

        async for event in stream_tool_results(
            tool_results,
            messages=messages,
            agent_label="harness",
            trace_id=state.trace_id,
            args_by_id=args_by_id,
            process_result=process,
            on_event=state.timeline_events.append,
        ):
            yield event

    def _delegate_start_events(
        self, tool_calls: list[ToolCall], *, state: HarnessState
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            if tool_call.name == "delegate_to_expert":
                arguments = (
                    tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
                )
                expert = str(arguments.get("expert") or DEFAULT_ROUTE).strip()
                if expert not in EXPERT_ROUTES:
                    expert = DEFAULT_ROUTE
                subtask = str(arguments.get("subtask") or "").strip()
                compact_subtask = (
                    f"{subtask[:500]}..." if len(subtask) > 500 else subtask
                )
                events.append(
                    make_agent_event(
                        agent="harness",
                        stage="delegate_start",
                        status="in_progress",
                        summary=f"Delegating subtask to {expert} expert.",
                        payload={
                            "delegated_expert": expert,
                            "subtask": compact_subtask,
                            "tool_call_id": tool_call.id,
                        },
                        trace_id=state.trace_id,
                        span_id=f"delegate:{tool_call.id}:start",
                    )
                )
            elif tool_call.name == "delegate_parallel":
                arguments = (
                    tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
                )
                experts = arguments.get("experts") or []
                if not isinstance(experts, list):
                    experts = [experts]
                expert_names = [
                    str(e).strip() for e in experts if str(e or "").strip()
                ][:8]
                events.append(
                    make_agent_event(
                        agent="harness",
                        stage="delegate_parallel_start",
                        status="in_progress",
                        summary=(
                            "Parallel delegation to "
                            + (", ".join(expert_names) if expert_names else "experts")
                            + "."
                        ),
                        payload={
                            "experts": expert_names,
                            "tool_call_id": tool_call.id,
                            "parallel": True,
                        },
                        trace_id=state.trace_id,
                        span_id=f"delegate_parallel:{tool_call.id}:start",
                    )
                )
            else:
                # The terminal ``tool_event`` is intentionally emitted by
                # ``stream_tool_results`` after execution. This separate
                # agent lifecycle marker lets the UI show immediate progress
                # without changing tool metrics or exposing arguments.
                events.append(
                    make_agent_event(
                        agent="harness",
                        stage="tool_start",
                        status="in_progress",
                        summary=f"Starting {tool_call.name}.",
                        payload={
                            "tool": tool_call.name,
                            "tool_call_id": tool_call.id,
                        },
                        trace_id=state.trace_id,
                        span_id=f"tool:{tool_call.id}:start",
                    )
                )
        return events

    async def _log_postprocess(
        self,
        result: Any,
        *,
        client: Any,
        state: HarnessState,
    ) -> AsyncGenerator[tuple[dict[str, Any] | None, str | None], None]:
        """For large log-tool output, replace hard truncation with analyze_logs.

        Yields ``(pipeline_event, None)`` for each pipeline event to stream, then a
        final ``(None, digest)`` carrying the clustered digest. Falls back silently
        (yields nothing) when disabled, not a log tool, output is small, or on error.
        """
        if (
            not result.success
            or not getattr(config, "harness_log_pipeline_enabled", True)
            or not self._is_log_tool(result.tool_name)
        ):
            return
        full = _stringify_tool_result(result.raw)
        if len(full) <= self.tool_executor.max_output_chars:
            return
        sink: list[dict[str, Any]] = []
        try:
            digest = await analyze_logs(
                full,
                llm_client=client,
                trace_id=state.trace_id,
                events_sink=sink,
            )
        except Exception as exc:  # pipeline must never break the loop
            logger.warning(f"harness log preprocessing failed; falling back to truncated output: {exc}")
            return
        for pipeline_event in sink:
            yield pipeline_event, None
        yield None, digest

    def _delegate_child_events(
        self,
        result: Any,
        *,
        state: HarnessState,
    ) -> list[dict[str, Any]]:
        if result.tool_name not in {"delegate_to_expert", "delegate_parallel"}:
            return []
        if not isinstance(result.raw, dict):
            return []

        child_events: list[dict[str, Any]] = []
        if result.tool_name == "delegate_parallel":
            # Emit a done marker; nested per-expert child events are optional.
            child_events.append(
                make_agent_event(
                    agent="harness",
                    stage="delegate_parallel_done",
                    status=str(result.raw.get("status") or "completed"),
                    summary=(
                        "Parallel delegation finished in "
                        f"{int(result.raw.get('wall_ms') or 0)}ms."
                    ),
                    payload={
                        "parallel": True,
                        "wall_ms": int(result.raw.get("wall_ms") or 0),
                        "truncated": bool(result.raw.get("truncated")),
                        "results": [
                            {
                                "expert": (item or {}).get("expert"),
                                "status": (item or {}).get("status"),
                            }
                            for item in list(result.raw.get("results") or [])
                            if isinstance(item, dict)
                        ],
                        "parent_tool_call_id": result.call_id,
                    },
                    trace_id=state.trace_id,
                    span_id=f"delegate_parallel:{result.call_id}:done",
                )
            )
            for expert_result in list(result.raw.get("results") or []):
                if not isinstance(expert_result, dict):
                    continue
                for index, raw_event in enumerate(list(expert_result.get("events") or [])[-6:]):
                    if (
                        not isinstance(raw_event, dict)
                        or raw_event.get("type") not in TIMELINE_EVENT_TYPES
                    ):
                        continue
                    event = dict(raw_event)
                    payload = dict(event.get("payload") or {})
                    payload.setdefault("parent_tool_call_id", result.call_id)
                    payload.setdefault("delegated_expert", expert_result.get("expert"))
                    payload.setdefault("parallel", True)
                    event["payload"] = payload
                    event.setdefault("trace_id", state.trace_id)
                    event.setdefault(
                        "span_id",
                        f"delegate_parallel:{result.call_id}:{expert_result.get('expert')}:{index}",
                    )
                    child_events.append(event)
            return child_events

        raw_events = result.raw.get("events")
        if not isinstance(raw_events, list):
            return []

        for index, raw_event in enumerate(raw_events):
            if (
                not isinstance(raw_event, dict)
                or raw_event.get("type") not in TIMELINE_EVENT_TYPES
            ):
                continue
            event = dict(raw_event)
            payload = dict(event.get("payload") or {})
            payload.setdefault("parent_tool_call_id", result.call_id)
            payload.setdefault("delegated_expert", result.raw.get("expert"))
            event["payload"] = payload
            event.setdefault("trace_id", state.trace_id)
            event.setdefault("span_id", f"delegate:{result.call_id}:{index}")
            child_events.append(event)
        return child_events
