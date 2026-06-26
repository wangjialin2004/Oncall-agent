"""Unified feature-flagged harness loop."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Sequence
from dataclasses import replace
from typing import Any

from loguru import logger

from app.agent.agent_loop import (
    GuardedToolExecutor,
    estimate_tokens,
    stream_tool_results,
    tool_call_payload,
)
from app.agent.events import make_agent_event, make_route_event
from app.agent.experts.log_pipeline import analyze_logs
from app.agent.experts.registry import DEFAULT_ROUTE, EXPERT_ROUTES, get_expert
from app.agent.harness.clarifier import ClarificationRequest, MissingParameterClarifier
from app.agent.harness.context import ContextBuilder
from app.agent.harness.planner import HarnessPlan, LightweightPlanner
from app.agent.harness.registry import HarnessToolRegistry
from app.agent.harness.state import HarnessLimits, HarnessState
from app.agent.harness.verifier import EvidenceVerifier, VerificationResult
from app.agent.stream_common import CLARIFY_TEXT, TIMELINE_EVENT_TYPES, build_timeout_report
from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, ToolCall, new_llm_client
from app.core.runtime_tools import RuntimeTool
from app.core.tool_calling import _stringify_tool_result, tool_to_definition
from app.services.router_service import RouterService


class HarnessService:
    """A single orchestrating loop behind ``harness_enabled``.

    The first implementation deliberately keeps the old RouterService untouched:
    this service owns the new path, emits the same event shapes, and can be
    switched off immediately through configuration.
    """

    def __init__(
        self,
        *,
        context_builder: ContextBuilder | None = None,
        router: RouterService | None = None,
        llm_client: Any | None = None,
        tools: Sequence[RuntimeTool] | None = None,
        limits: HarnessLimits | None = None,
        fallback_expert: Any | None = None,
        vector_searcher: Any | None = None,
    ) -> None:
        self.context_builder = context_builder or ContextBuilder()
        self.router = router or RouterService()
        self.llm_client = llm_client
        self.tool_registry = HarnessToolRegistry(list(tools) if tools is not None else None)
        self.tool_executor = GuardedToolExecutor()
        self.planner = LightweightPlanner()
        self.clarifier = MissingParameterClarifier()
        self.verifier = EvidenceVerifier()
        self.fallback_expert = fallback_expert
        self.vector_searcher = vector_searcher
        self.limits = limits or HarnessLimits(
            max_steps=int(getattr(config, "harness_max_steps", 6)),
            token_budget=int(getattr(config, "harness_token_budget", 16000)),
            timeout_seconds=float(getattr(config, "harness_timeout_seconds", 90.0)),
            no_progress_limit=int(getattr(config, "harness_no_progress_limit", 2)),
        )
        # 发往模型的 messages 体量安全网（与累计预算 token_budget 不同：这是“单次请求”护栏）
        self.message_token_budget = int(getattr(config, "harness_message_token_budget", 60000))

    async def stream(
        self, message: str, session_id: str, owner_key: str = ""
    ) -> AsyncGenerator[dict[str, Any], None]:
        try:
            async with asyncio.timeout(self.limits.timeout_seconds):
                async for event in self._stream_inner(message, session_id, owner_key):
                    yield event
        except TimeoutError:
            logger.warning(f"harness 执行超时 {self.limits.timeout_seconds}s，返回降级答案")
            timeout_event = make_agent_event(
                agent="harness",
                stage="timeout_fallback",
                status="degraded",
                summary="Harness 主循环执行超时，已返回降级结果。",
                payload={"timeout_seconds": self.limits.timeout_seconds},
                trace_id=session_id,
            )
            async for event in self._fallback_stream(
                message=message,
                session_id=session_id,
                owner_key=owner_key,
                reason="harness_timeout",
                seed_events=[timeout_event],
            ):
                yield event

    async def _stream_inner(
        self, message: str, session_id: str, owner_key: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        state = HarnessState(trace_id=session_id, session_id=session_id, owner_key=owner_key)
        started = time.perf_counter()
        client = self.llm_client or self._new_llm_client()
        owns_client = self.llm_client is None
        try:
            route_progress = self._progress_event(
                state=state,
                stage="route",
                summary="正在识别问题类型并选择处理专家。",
                payload={"message_chars": len(message)},
            )
            yield route_progress

            route_decision = await self.router._resolve_route(message)
            state.route = route_decision.route if route_decision.route != "clarify" else DEFAULT_ROUTE
            state.route_reason = f"harness_focus:{route_decision.reason}"
            route_event = make_route_event(
                route=state.route,
                reason=state.route_reason,
                confidence=route_decision.confidence,
                candidates=list(EXPERT_ROUTES),
                payload={"mode": "harness", "focus_route": route_decision.route},
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
                summary="正在加载会话记忆、用户偏好和可用工具。",
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
            context = await self.context_builder.abuild(
                message=message,
                owner_key=owner_key,
                session_id=session_id,
                tools=tools,
                focus_hint=(
                    f"候选领域：{route_decision.route}；原因：{route_decision.reason}；"
                    f"置信度：{route_decision.confidence:.2f}"
                ),
                llm_client=client,
            )
            context_ref["value"] = context.system_prompt
            state.add_text_budget(context.system_prompt)
            for history_message in context.history_messages:
                state.add_text_budget(history_message.content)
            state.add_text_budget(message)

            start_event = make_agent_event(
                agent="harness",
                stage="start",
                status="in_progress",
                summary="统一 Harness 主循环开始处理",
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
                summary="正在生成排查计划和证据需求。",
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
            if pending_clarification is not None and not tools:
                async for event in self._emit_clarification(
                    pending_clarification, state=state, started=started
                ):
                    yield event
                return

            messages = [
                ChatMessage(role="system", content=context.system_prompt),
                *context.history_messages,
                ChatMessage(role="user", content=message),
            ]
            tool_defs = [tool_to_definition(tool) for tool in tools]
            answer = ""
            answer_streamed = False
            seen_signatures: set[str] = set()
            no_progress_streak = 0

            # 路由选中的专项专家先行执行核心调查（确定性委派），harness 随后只做核对/补充/收尾。
            if self._should_seed_delegation(tools, state.route):
                async for event in self._seed_expert_delegation(
                    route=state.route,
                    subtask=message,
                    tools=tools,
                    messages=messages,
                    client=client,
                    state=state,
                ):
                    yield event

            for step_index in range(self.limits.max_steps):
                state.step = step_index + 1
                if state.over_budget(self.limits):
                    budget_event = make_agent_event(
                        agent="harness",
                        stage="budget",
                        status="degraded",
                        summary="接近 Harness 预算上限，切换为无工具收尾。",
                        payload={"token_budget": self.limits.token_budget},
                        trace_id=session_id,
                    )
                    state.timeline_events.append(budget_event)
                    yield budget_event
                    closing_progress = self._progress_event(
                        state=state,
                        stage="model_closing",
                        summary="预算接近上限，正在让模型无工具收尾。",
                        payload={"step": state.step},
                    )
                    yield closing_progress
                    async for chunk in self._stream_final_answer(
                        client=client, messages=messages, temperature=0.3
                    ):
                        answer += chunk
                        yield {"type": "content", "data": chunk, "agent": "harness"}
                    answer_streamed = True
                    break

                model_progress = self._progress_event(
                    state=state,
                    stage="model_decision",
                    summary="正在调用大模型判断下一步行动。",
                    payload={
                        "step": state.step,
                        "tool_count": len(tool_defs),
                        "history_messages": len(messages),
                    },
                )
                yield model_progress

                if not tool_defs:
                    async for chunk in self._stream_final_answer(
                        client=client, messages=messages, temperature=0.3
                    ):
                        answer += chunk
                        yield {"type": "content", "data": chunk, "agent": "harness"}
                    answer_streamed = True
                    break

                response = None
                async for event in self._stream_chat_turn(
                    client=client,
                    messages=messages,
                    tools=tool_defs or None,
                    tool_choice="auto" if tool_defs else None,
                    temperature=0.3,
                ):
                    chunk = event.get("content")
                    if chunk:
                        answer += str(chunk)
                        answer_streamed = True
                        yield {"type": "content", "data": str(chunk), "agent": "harness"}
                    if event.get("response") is not None:
                        response = event["response"]
                if response is None:
                    break
                state.add_usage(response.usage)

                if not response.tool_calls:
                    if not answer:
                        answer = response.content
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
                        summary="检测到重复工具调用且无新增证据，提前结束取证并收尾。",
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
                        summary="检测到重复工具调用，正在让模型基于已有证据收尾。",
                        payload={"step": state.step},
                    )
                    yield closing_progress
                    async for chunk in self._stream_final_answer(
                        client=client, messages=messages, temperature=0.3
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
                    response.tool_calls, tools, messages, client=client, state=state
                ):
                    yield event
            else:
                closing_progress = self._progress_event(
                    state=state,
                    stage="model_closing",
                    summary="已达到最大步骤数，正在让模型无工具生成最终回答。",
                    payload={"step": state.step},
                )
                yield closing_progress
                async for chunk in self._stream_final_answer(
                    client=client, messages=messages, temperature=0.3
                ):
                    answer += chunk
                    yield {"type": "content", "data": chunk, "agent": "harness"}
                answer_streamed = True

            if (
                pending_clarification is not None
                and not self._has_successful_tool_evidence(state.timeline_events)
            ):
                async for event in self._emit_clarification(
                    pending_clarification, state=state, started=started
                ):
                    yield event
                return

            if answer:
                verify_progress = self._progress_event(
                    state=state,
                    stage="verify",
                    summary="正在对最终回答做证据自检。",
                    payload={"answer_chars": len(answer)},
                )
                yield verify_progress
                verification = await self.verifier.averify(
                    answer=answer,
                    timeline_events=state.timeline_events,
                    plan=plan,
                    llm_client=client,
                )
                final_answer = answer
                if (
                    getattr(config, "harness_corrective_verify_enabled", True)
                    and verification.status in {"degraded", "failed"}
                    and verification.gaps
                ):
                    final_answer = self._apply_corrective_notice(answer, verification)
                state.append_answer(final_answer)
                verify_event = self._verify_event(verification, state=state)
                state.timeline_events.append(verify_event)
                yield verify_event
                report_progress = self._progress_event(
                    state=state,
                    stage="report",
                    summary="证据自检完成，正在输出最终报告。",
                    payload={"answer_chars": len(final_answer)},
                )
                yield report_progress
                if not answer_streamed:
                    yield {"type": "content", "data": final_answer, "agent": "harness"}

            complete_event = make_agent_event(
                agent="harness",
                stage="complete",
                status="completed",
                summary="统一 Harness 主循环处理完成",
                payload={
                    "answer_chars": len(state.answer),
                    "steps": state.step,
                    "token_estimate": state.token_estimate,
                },
                trace_id=session_id,
                span_id=f"harness:{session_id}",
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=state.usage_total or None,
            )
            state.timeline_events.append(complete_event)
            yield complete_event
            yield self._complete_event(state)
        except Exception as exc:
            logger.error(f"harness 执行失败: {exc}", exc_info=True)
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
            async for event in self._fallback_stream(
                message=message,
                session_id=session_id,
                owner_key=owner_key,
                reason="harness_error",
                seed_events=state.timeline_events,
            ):
                yield event
        finally:
            if owns_client:
                await client.aclose()

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
            usage=state.usage_total or None,
        )
        state.timeline_events.append(event)
        return event

    def _truncate_messages_for_model(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Shrink an oversized prompt before sending it to the model.

        The loop appends every tool result to ``messages`` and never drops them,
        so a long multi-step run (or the no-tool closing call that re-sends the
        whole history) can exceed the model context window and fail the request.
        This is a per-request safety net: it compacts the *content* of the oldest
        history / tool messages — never removing a message — so assistant
        ``tool_calls`` stay paired with their ``tool`` results. The system prompt,
        the most recent user question, and the latest assistant/tool tail (newest
        evidence) are always preserved verbatim.
        """
        budget = self.message_token_budget
        if budget <= 0 or not messages:
            return messages
        total = sum(estimate_tokens(item.content or "") for item in messages)
        if total <= budget:
            return messages

        protected: set[int] = set()
        if messages[0].role == "system":
            protected.add(0)
        last_user = max(
            (index for index, item in enumerate(messages) if item.role == "user"),
            default=-1,
        )
        if last_user >= 0:
            protected.add(last_user)
        # 只保护“最近一次” assistant 轮及其后续 tool 结果（最新证据），更早的取证轮可压缩。
        # 注意只替换 content、保留 assistant.tool_calls 与 tool.tool_call_id，配对始终成立。
        last_assistant = max(
            (index for index, item in enumerate(messages) if item.role == "assistant"),
            default=-1,
        )
        if last_assistant >= 0:
            protected.update(range(last_assistant, len(messages)))

        stub = "[早期上下文已压缩以控制 token 预算]"
        stub_tokens = estimate_tokens(stub)
        trimmed = list(messages)
        for index, item in enumerate(trimmed):
            if total <= budget:
                break
            if index in protected:
                continue
            original = item.content or ""
            if estimate_tokens(original) <= stub_tokens:
                continue
            total -= estimate_tokens(original) - stub_tokens
            trimmed[index] = replace(item, content=stub)
        return trimmed

    async def _stream_final_answer(
        self,
        *,
        client: Any,
        messages: list[ChatMessage],
        temperature: float,
    ) -> AsyncGenerator[str, None]:
        messages = self._truncate_messages_for_model(messages)
        stream_complete = getattr(client, "stream_complete", None)
        if stream_complete is None:
            response = await client.complete(messages, temperature=temperature)
            yield response.content
            return
        async for chunk in stream_complete(messages, temperature=temperature):
            yield str(chunk)

    async def _stream_chat_turn(
        self,
        *,
        client: Any,
        messages: list[ChatMessage],
        temperature: float,
        tools: list[Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        messages = self._truncate_messages_for_model(messages)
        stream_chat = getattr(client, "stream_chat", None)
        if stream_chat is None:
            response = await client.complete(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
            )
            yield {"response": response}
            return
        async for event in stream_chat(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
        ):
            content = str(getattr(event, "content", "") or "")
            if content:
                yield {"content": content}
            response = getattr(event, "response", None)
            if response is not None:
                yield {"response": response}

    def _should_seed_delegation(self, tools: Sequence[RuntimeTool], route: str) -> bool:
        """Whether to deterministically hand the first investigation to the routed expert."""
        if not getattr(config, "harness_force_expert_delegation", True):
            return False
        if route not in EXPERT_ROUTES:
            return False
        return any(tool.name == "delegate_to_expert" for tool in tools)

    async def _seed_expert_delegation(
        self,
        *,
        route: str,
        subtask: str,
        tools: list[RuntimeTool],
        messages: list[ChatMessage],
        client: Any,
        state: HarnessState,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Seed a deterministic ``delegate_to_expert`` call to the routed expert.

        The router's choice becomes authoritative: before the harness model gets a
        turn, the selected expert runs the core investigation and its conclusion +
        evidence are appended as a tool result. The subsequent loop then verifies,
        does targeted follow-up, and synthesizes — it no longer re-investigates from
        scratch. Reuses ``_execute_tools`` so delegate_start / tool / child events
        surface into the timeline exactly like a model-initiated delegation.
        """
        yield self._progress_event(
            state=state,
            stage="delegate_dispatch",
            summary=f"按路由焦点将核心调查委派给 {route} 专家执行。",
            payload={"delegated_expert": route, "forced": True},
        )
        seed_call = ToolCall(
            id=f"seed-delegate:{state.trace_id}",
            name="delegate_to_expert",
            arguments={"expert": route, "subtask": subtask},
        )
        messages.append(
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[tool_call_payload(seed_call)],
            )
        )
        async for event in self._execute_tools(
            [seed_call], tools, messages, client=client, state=state
        ):
            yield event

    async def _execute_tools(
        self,
        tool_calls: list[ToolCall],
        tools: list[RuntimeTool],
        messages: list[ChatMessage],
        *,
        client: Any,
        state: HarnessState,
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
            if tool_call.name != "delegate_to_expert":
                continue
            arguments = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
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
                    summary=f"进入 {expert} 专家处理子任务。",
                    payload={
                        "delegated_expert": expert,
                        "subtask": compact_subtask,
                        "tool_call_id": tool_call.id,
                    },
                    trace_id=state.trace_id,
                    span_id=f"delegate:{tool_call.id}:start",
                )
            )
        return events

    async def _fallback_stream(
        self,
        *,
        message: str,
        session_id: str,
        owner_key: str,
        reason: str,
        seed_events: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        events = [
            dict(event)
            for event in (seed_events or [])
            if event.get("type") in TIMELINE_EVENT_TYPES
        ]
        if events:
            yield events[-1]

        start_event = make_agent_event(
            agent="harness",
            stage="fallback_start",
            status="degraded",
            summary="Harness 主流程不可用，开始按 knowledge_expert -> raw_vector 逐级降级。",
            payload={"reason": reason, "levels": ["knowledge_expert", "raw_vector"]},
            trace_id=session_id,
            span_id=f"harness:{session_id}:fallback",
        )
        events.append(start_event)
        yield start_event

        answer_parts: list[str] = []
        try:
            async for event in self._iter_knowledge_fallback(
                message=message,
                session_id=session_id,
                owner_key=owner_key,
            ):
                event_type = event.get("type")
                if event_type == "content":
                    answer_parts.append(str(event.get("data") or ""))
                elif event_type in TIMELINE_EVENT_TYPES:
                    events.append(event)
                yield event
        except Exception as exc:
            fail_event = make_agent_event(
                agent="harness",
                stage="knowledge_fallback_error",
                status="degraded",
                summary=f"knowledge_expert 降级失败：{exc}",
                payload={"error": str(exc)},
                trace_id=session_id,
                span_id=f"harness:{session_id}:fallback:knowledge",
            )
            events.append(fail_event)
            yield fail_event

        answer = "".join(answer_parts).strip()
        if answer:
            complete_event = make_agent_event(
                agent="harness",
                stage="fallback_complete",
                status="completed",
                summary="已使用 knowledge_expert 返回降级答案。",
                payload={"level": "knowledge_expert", "answer_chars": len(answer)},
                trace_id=session_id,
                span_id=f"harness:{session_id}:fallback",
            )
            events.append(complete_event)
            yield complete_event
            yield {
                "type": "complete",
                "route": "knowledge",
                "route_reason": f"{reason}:fallback_knowledge_expert",
                "answer": answer,
                "case_id": "",
                "events": events,
            }
            return

        empty_event = make_agent_event(
            agent="harness",
            stage="knowledge_fallback_empty",
            status="degraded",
            summary="knowledge_expert 未产生可用答案，继续降级到原始向量检索。",
            payload={"reason": reason},
            trace_id=session_id,
            span_id=f"harness:{session_id}:fallback:knowledge",
        )
        events.append(empty_event)
        yield empty_event

        raw_answer = await self._raw_vector_fallback_answer(message)
        raw_event = make_agent_event(
            agent="harness",
            stage="raw_vector_fallback_complete",
            status="completed" if raw_answer else "degraded",
            summary="已使用原始向量检索返回最后兜底结果。" if raw_answer else "原始向量检索也没有返回结果。",
            payload={"answer_chars": len(raw_answer)},
            trace_id=session_id,
            span_id=f"harness:{session_id}:fallback:vector",
        )
        events.append(raw_event)
        yield raw_event

        final_answer = raw_answer or build_timeout_report(
            subject="Harness 主循环和知识库降级",
            message=message,
            timeout_seconds=self.limits.timeout_seconds,
        )
        yield {"type": "content", "data": final_answer, "agent": "raw_vector_fallback"}
        yield {
            "type": "complete",
            "route": "knowledge",
            "route_reason": f"{reason}:fallback_raw_vector",
            "answer": final_answer,
            "case_id": "",
            "events": events,
        }

    async def _iter_knowledge_fallback(
        self, *, message: str, session_id: str, owner_key: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        expert = self.fallback_expert or get_expert("knowledge")
        context = ""
        if owner_key and getattr(config, "user_preferences_enabled", False):
            from app.services.user_preference_service import user_preference_service

            context = user_preference_service.format_for_prompt(owner_key)
        generator = expert.run(
            message=message,
            session_id=session_id,
            trace_id=session_id,
            context=context,
        )
        try:
            async for event in generator:
                yield event
        finally:
            aclose = getattr(generator, "aclose", None)
            if aclose:
                await aclose()

    async def _raw_vector_fallback_answer(self, message: str) -> str:
        try:
            searcher = self.vector_searcher
            if searcher is None:
                from app.services.vector_search_service import vector_search_service

                searcher = vector_search_service
            results = searcher.search(message, top_k=getattr(config, "rag_top_k", 3))
        except Exception as exc:
            logger.warning(f"raw vector 降级检索失败：{exc}")
            return ""

        if not results:
            return ""

        lines = ["# 知识库降级检索结果", "", "Harness 和 knowledge_expert 当前不可用，以下是原始向量检索命中的参考片段："]
        for index, result in enumerate(results, 1):
            content = str(getattr(result, "content", "") or "").strip()
            if not content:
                continue
            source = str(getattr(result, "source", "") or "未知来源")
            score = getattr(result, "score", "")
            rank = getattr(result, "rank", index)
            snippet = content[:800]
            lines.extend(
                [
                    "",
                    f"## 参考 {index}",
                    f"- 来源：{source}",
                    f"- 排名：{rank}",
                    f"- 分数：{score}",
                    "",
                    snippet,
                ]
            )
        return "\n".join(lines).strip()

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
            logger.warning(f"harness 日志预处理失败，回退截断输出：{exc}")
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
        if result.tool_name != "delegate_to_expert" or not isinstance(result.raw, dict):
            return []
        raw_events = result.raw.get("events")
        if not isinstance(raw_events, list):
            return []

        child_events: list[dict[str, Any]] = []
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

    def _new_llm_client(self) -> LLMClient:
        return new_llm_client()

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
            summary="已生成轻量排查计划",
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
            usage=state.usage_total or None,
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
            summary="缺少工具无法自动获取的必要参数，已暂停排查并向用户追问。",
            payload={
                "missing_params": request.missing_params,
                "reason": request.reason,
                "evidence_gap": request.evidence_gap,
                "defaults": request.defaults,
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}:clarify_missing_params",
            usage=state.usage_total or None,
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
        yield {
            "type": "content",
            "data": request.question,
            "agent": "harness",
        }
        complete_event = make_agent_event(
            agent="harness",
            stage="complete",
            status="completed",
            summary="统一 Harness 主循环等待用户补充必要参数。",
            payload={
                "answer_chars": len(state.answer),
                "steps": state.step,
                "token_estimate": state.token_estimate,
            },
            trace_id=state.trace_id,
            span_id=f"harness:{state.trace_id}",
            duration_ms=(time.perf_counter() - started) * 1000,
            usage=state.usage_total or None,
        )
        state.timeline_events.append(complete_event)
        yield complete_event
        yield self._complete_event(state)

    @staticmethod
    def _apply_corrective_notice(answer: str, result: VerificationResult) -> str:
        """Surface verification gaps to the user instead of leaving them in a side panel."""
        gap_lines = "\n".join(f"> - {gap}" for gap in result.gaps)
        notice = (
            f"> ⚠️ 证据自检：置信度 {result.confidence}，本次回答存在以下证据缺口，请谨慎采用：\n"
            f"{gap_lines}"
        )
        return f"{notice}\n\n{answer}"

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
    def _has_successful_tool_evidence(events: Sequence[dict[str, Any]]) -> bool:
        return any(
            event.get("type") == "tool_event" and event.get("status") == "completed"
            for event in events
        )

    def _complete_event(self, state: HarnessState) -> dict[str, Any]:
        events = [event for event in state.timeline_events if event.get("type") in TIMELINE_EVENT_TYPES]
        return {
            "type": "complete",
            "route": state.route,
            "route_reason": state.route_reason,
            "answer": state.answer,
            "case_id": state.case_id,
            "events": events,
        }

harness_service = HarnessService()
