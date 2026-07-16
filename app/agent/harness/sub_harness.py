"""Shared tool-calling sub-loop used by experts and harness delegation (M2 W7).

This is the single implementation of the multi-round model→tools loop that
``ToolCallingExpert`` previously owned inline. Parent harness orchestration
(plan / re-evidence / replan / verify) stays in ``loop.py``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.agent.agent_loop import (
    GuardedToolExecutor,
    merge_usage,
    stream_tool_results,
    tool_call_payload,
)
from app.agent.events import make_agent_event
from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, get_default_llm_client
from app.core.runtime_tools import RuntimeTool
from app.core.tool_calling import tool_to_definition

ExpertEvent = dict[str, Any]
ResultProcessor = Callable[[Any], Awaitable[tuple[str, list[ExpertEvent], list[ExpertEvent]]]]


@dataclass(slots=True)
class SubHarnessConfig:
    """Configuration for one scoped expert / subagent run."""

    agent_label: str
    display_name: str
    system_prompt: str
    tools: list[RuntimeTool]
    max_steps: int = 1
    temperature: float = 0.3
    close_after_tools: bool = False
    step_timeout_seconds: float | None = None
    # Optional result post-processor (e.g. log pipeline). Signature matches
    # stream_tool_results process_result: async (result) -> (content, pre, post).
    process_result: ResultProcessor | None = None


async def run_sub_harness(
    config_obj: SubHarnessConfig,
    *,
    message: str,
    session_id: str,
    trace_id: str,
    context: str = "",
    llm_client: LLMClient | None = None,
) -> AsyncGenerator[ExpertEvent, None]:
    """Run the shared multi-round tool-calling loop and yield timeline events."""
    del session_id  # reserved for future per-session budgets / tracing
    span_id = f"{config_obj.agent_label}:{trace_id}"
    started = time.perf_counter()
    usage_total: dict[str, int] = {}
    kernel_flag = bool(getattr(config, "harness_shared_kernel_delegation", True))

    yield make_agent_event(
        agent=config_obj.agent_label,
        stage="start",
        status="in_progress",
        summary=f"{config_obj.display_name}开始处理",
        payload={"shared_kernel": kernel_flag},
        trace_id=trace_id,
        span_id=span_id,
    )

    client = llm_client or await get_default_llm_client()
    round_limit = max(1, int(config_obj.max_steps or 1))
    # max_output_chars=0: keep raw tool output so transform hooks see full content.
    executor = GuardedToolExecutor(max_output_chars=0)
    step_timeout_seconds = float(
        config_obj.step_timeout_seconds
        if config_obj.step_timeout_seconds is not None
        else getattr(config, "harness_step_timeout_seconds", 25.0) or 25.0
    )

    try:
        tools = list(config_obj.tools or [])
        tool_defs = [tool_to_definition(tool) for tool in tools]
        system_content = config_obj.system_prompt or ""
        if context:
            system_content = f"{system_content}\n\n{context}" if system_content else context
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_content),
            ChatMessage(role="user", content=message),
        ]

        answer = ""
        answer_streamed = False
        closed_after_tools = False
        rounds_used = 0
        for round_index in range(round_limit):
            rounds_used = round_index + 1
            if not tool_defs:
                async for chunk in _stream_final_answer(
                    client=client,
                    messages=messages,
                    temperature=config_obj.temperature,
                ):
                    answer += chunk
                    yield {
                        "type": "content",
                        "data": chunk,
                        "agent": config_obj.agent_label,
                    }
                answer_streamed = True
                break

            response = None
            step_timed_out = False
            try:
                async with asyncio.timeout(step_timeout_seconds):
                    async for event in _stream_chat_turn(
                        client=client,
                        messages=messages,
                        tools=tool_defs or None,
                        tool_choice="auto" if tool_defs else None,
                        temperature=config_obj.temperature,
                    ):
                        chunk = event.get("content")
                        if chunk:
                            answer += str(chunk)
                            answer_streamed = True
                            yield {
                                "type": "content",
                                "data": str(chunk),
                                "agent": config_obj.agent_label,
                            }
                        if event.get("response") is not None:
                            response = event["response"]
            except TimeoutError:
                step_timed_out = True
                yield make_agent_event(
                    agent=config_obj.agent_label,
                    stage="step_timeout",
                    status="degraded",
                    summary=(
                        f"{config_obj.display_name} 单步 LLM 决策超过 "
                        f"{step_timeout_seconds:g}s，提前收尾。"
                    ),
                    payload={"step_timeout_seconds": step_timeout_seconds},
                    trace_id=trace_id,
                    span_id=f"{config_obj.agent_label}:{trace_id}:step_timeout",
                )
            if step_timed_out:
                break
            if response is None:
                break
            merge_usage(usage_total, response.usage)

            if not response.tool_calls:
                if not answer:
                    answer = response.content
                break

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=[tool_call_payload(tc) for tc in response.tool_calls],
                )
            )

            tool_results = await executor.execute(response.tool_calls, tools)
            args_by_id = {tc.id: tc.arguments for tc in response.tool_calls}
            processor = config_obj.process_result
            async for event in stream_tool_results(
                tool_results,
                messages=messages,
                agent_label=config_obj.agent_label,
                trace_id=trace_id,
                args_by_id=args_by_id,
                process_result=processor,
                measure_duration=True,
            ):
                yield event
            if config_obj.close_after_tools:
                closed_after_tools = True
                break
        else:
            # Exhausted rounds without a final answer: ask once more, no tools.
            async for chunk in _stream_final_answer(
                client=client,
                messages=messages,
                temperature=config_obj.temperature,
            ):
                answer += chunk
                yield {
                    "type": "content",
                    "data": chunk,
                    "agent": config_obj.agent_label,
                }
            answer_streamed = True

        if closed_after_tools:
            yield make_agent_event(
                agent=config_obj.agent_label,
                stage="evidence_return",
                status="completed",
                summary="Delegated tool evidence returned to parent without expert synthesis.",
                payload={
                    "tool_rounds_used": rounds_used,
                    "shared_kernel": kernel_flag,
                },
                trace_id=trace_id,
                span_id=f"{config_obj.agent_label}:{trace_id}:evidence_return",
            )

        if answer and not answer_streamed:
            yield {
                "type": "content",
                "data": answer,
                "agent": config_obj.agent_label,
            }

        yield make_agent_event(
            agent=config_obj.agent_label,
            stage="complete",
            status="completed",
            summary=f"{config_obj.display_name}处理完成",
            payload={
                "answer_chars": len(answer),
                "shared_kernel": kernel_flag,
                "tool_rounds_used": rounds_used,
            },
            trace_id=trace_id,
            span_id=span_id,
            duration_ms=(time.perf_counter() - started) * 1000,
            usage=usage_total or None,
        )
    except Exception as exc:
        logger.error(f"{config_obj.agent_label} 执行失败: {exc}", exc_info=True)
        yield make_agent_event(
            agent=config_obj.agent_label,
            stage="error",
            status="degraded",
            summary=f"{config_obj.display_name}执行出错：{exc}",
            payload={"error": str(exc), "shared_kernel": kernel_flag},
            trace_id=trace_id,
            span_id=span_id,
            duration_ms=(time.perf_counter() - started) * 1000,
            usage=usage_total or None,
        )
        yield {
            "type": "content",
            "data": (
                f"抱歉，{config_obj.display_name}在处理时出现问题：{exc}。"
                "请稍后重试或补充信息。"
            ),
            "agent": config_obj.agent_label,
        }


def merge_delegate_results(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Light merge/dedup for parallel or multi-aux delegate payloads (WP-B4)."""
    by_expert: dict[str, dict[str, Any]] = {}
    tool_success_keys: set[str] = set()
    folded_tools: list[str] = []
    conflicts: list[str] = []

    for item in results or []:
        if not isinstance(item, dict):
            continue
        expert = str(item.get("expert") or "?").strip() or "?"
        status = str(item.get("status") or "")
        if expert in by_expert:
            prev = by_expert[expert]
            # Prefer completed over failed/degraded; keep first answer on tie.
            rank = {"completed": 3, "degraded": 2, "failed": 1}
            if rank.get(status, 0) > rank.get(str(prev.get("status") or ""), 0):
                by_expert[expert] = item
            elif status == str(prev.get("status") or "") and (
                str(item.get("answer") or "").strip()
                and str(item.get("answer") or "").strip()
                != str(prev.get("answer") or "").strip()
            ):
                conflicts.append(expert)
        else:
            by_expert[expert] = item

        for ev in list(item.get("events") or []):
            if not isinstance(ev, dict) or ev.get("type") != "tool_event":
                continue
            tool = str(ev.get("tool") or "").strip()
            st = str(ev.get("status") or "").lower()
            if not tool or st not in {"completed", "success", "ok"}:
                continue
            key = f"{expert}:{tool}"
            if key in tool_success_keys:
                continue
            tool_success_keys.add(key)
            folded_tools.append(tool)

    merged_results = list(by_expert.values())
    any_ok = any(
        str(r.get("status") or "") in {"completed", "degraded"} for r in merged_results
    )
    all_failed = bool(merged_results) and all(
        str(r.get("status") or "") == "failed" for r in merged_results
    )
    return {
        "status": "failed" if all_failed else ("completed" if any_ok else "degraded"),
        "results": merged_results,
        "folded_tools": folded_tools,
        "conflicts": conflicts,
        "experts": list(by_expert.keys()),
    }


async def _stream_final_answer(
    *,
    client: Any,
    messages: list[ChatMessage],
    temperature: float,
) -> AsyncGenerator[str, None]:
    stream_complete = getattr(client, "stream_complete", None)
    if stream_complete is None:
        response = await client.complete(messages, temperature=temperature)
        yield response.content
        return
    async for chunk in stream_complete(messages, temperature=temperature):
        yield str(chunk)


async def _stream_chat_turn(
    *,
    client: Any,
    messages: list[ChatMessage],
    temperature: float,
    tools: list[Any] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
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
