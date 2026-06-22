"""Shared kernel for the streaming tool-calling agent loop.

Both the single-domain experts (``app/agent/experts/base.py``) and the unified
harness (``app/agent/harness/loop.py``) run the same core loop: ask the model,
run any tool calls through the guarded executor, stream normalized timeline
events, and feed the results back. This module is the single source of truth for
that loop's shared pieces so the two orchestrators don't reimplement them:

- token/usage/serialization primitives (``estimate_tokens`` / ``merge_usage`` / …);
- the guarded tool executor (timeout + allowlist + truncation + retry);
- ``stream_tool_results``, the per-result event-streaming body.

It deliberately lives directly under ``app/agent`` (not under ``app/agent/harness``)
because ``harness/__init__`` eagerly imports the harness loop; importing the
kernel from there would create an experts → harness → experts import cycle.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from loguru import logger

from app.agent.events import make_tool_event
from app.config import config
from app.core.llm_client import ChatMessage, ToolCall
from app.core.runtime_tools import RuntimeTool, run_tool
from app.core.tool_calling import ToolExecutionResult, _stringify_tool_result

# --------------------------------------------------------------------- primitives


def estimate_tokens(text: str) -> int:
    """Cheap, provider-neutral token estimate.

    CJK-heavy operational text averages well under 2 chars/token, but ~2.5 is a
    safe upper bound for budgeting. We only need an order-of-magnitude gate.
    """
    return max(1, int(len(text) / 2.5))


def summarize(text: str, limit: int = 300) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:limit]


def tool_call_payload(tool_call: ToolCall) -> dict[str, Any]:
    """OpenAI-format ``tool_calls`` entry for the assistant turn after a tool call."""
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
        },
    }


def merge_usage(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + int(value)


# ---------------------------------------------------------------- guarded executor

# Substrings that mark a non-retryable error (auth / permission). Retrying these
# only wastes budget, so they fail fast.
_NON_RETRYABLE_TOKENS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "permission denied",
    "authentication",
)


class GuardedToolExecutor:
    """Runs tool calls with timeout, allowlist, output truncation, and retry.

    Shared by the experts and the harness so there is a single tool-execution
    path. Experts construct it with ``max_output_chars=0`` to keep raw content
    for their own post-processing hooks; the harness keeps truncation on.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        max_output_chars: int | None = None,
        allowlist: set[str] | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> None:
        self.timeout_seconds = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(getattr(config, "harness_tool_timeout_seconds", 30.0))
        )
        self.max_output_chars = (
            int(max_output_chars)
            if max_output_chars is not None
            else int(getattr(config, "harness_tool_max_output_chars", 6000))
        )
        self.allowlist = allowlist
        self.max_retries = (
            int(max_retries)
            if max_retries is not None
            else int(getattr(config, "harness_tool_max_retries", 1))
        )
        self.retry_backoff_seconds = (
            float(retry_backoff_seconds)
            if retry_backoff_seconds is not None
            else float(getattr(config, "harness_tool_retry_backoff_seconds", 0.5))
        )

    async def execute(
        self,
        tool_calls: list[ToolCall],
        tools: list[RuntimeTool],
    ) -> list[ToolExecutionResult]:
        tool_by_name = {tool.name: tool for tool in tools}
        results: list[ToolExecutionResult] = []
        for tool_call in tool_calls:
            tool = tool_by_name.get(tool_call.name)
            if tool is None:
                results.append(_failed(tool_call, f"Tool not found: {tool_call.name}"))
                continue
            if self.allowlist is not None and tool.name not in self.allowlist:
                results.append(_failed(tool_call, f"Tool not allowed: {tool.name}"))
                continue
            results.append(await self._run_one(tool, tool_call))
        return results

    async def _run_one(self, tool: RuntimeTool, tool_call: ToolCall) -> ToolExecutionResult:
        attempts = max(0, self.max_retries) + 1
        last_error = ""
        for attempt in range(attempts):
            try:
                raw = await asyncio.wait_for(
                    run_tool(tool, tool_call.arguments),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                last_error = f"Tool execution timed out after {self.timeout_seconds:g}s"
                if attempt < attempts - 1:
                    await self._sleep_backoff(attempt, tool.name, last_error)
                    continue
                return _failed(tool_call, last_error)
            except Exception as exc:
                last_error = f"Tool execution failed: {exc}"
                if _is_retryable(exc) and attempt < attempts - 1:
                    await self._sleep_backoff(attempt, tool.name, last_error)
                    continue
                return _failed(tool_call, last_error)

            content = self._postprocess_output(_stringify_tool_result(raw))
            return ToolExecutionResult(
                call_id=tool_call.id,
                tool_name=tool_call.name,
                content=content,
                success=True,
                raw=raw,
            )
        return _failed(tool_call, last_error)

    async def _sleep_backoff(self, attempt: int, tool_name: str, reason: str) -> None:
        delay = self.retry_backoff_seconds * (2**attempt)
        logger.warning(
            f"工具 {tool_name} 调用失败（{reason}），{delay:.2f}s 后重试 "
            f"(attempt {attempt + 1}/{self.max_retries})"
        )
        if delay > 0:
            await asyncio.sleep(delay)

    def _postprocess_output(self, content: str) -> str:
        if self.max_output_chars <= 0 or len(content) <= self.max_output_chars:
            return content
        return (
            content[: self.max_output_chars]
            + f"\n\n[输出已截断：原始长度 {len(content)} 字符，"
            + f"保留前 {self.max_output_chars} 字符]"
        )


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return not any(token in text for token in _NON_RETRYABLE_TOKENS)


def _failed(tool_call: ToolCall, content: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id=tool_call.id,
        tool_name=tool_call.name,
        content=content,
        success=False,
    )


# ----------------------------------------------------------- per-result streaming

# A processed result: (content_for_model, events_before_tool_event, events_after).
ProcessedResult = tuple[str, list[dict[str, Any]], list[dict[str, Any]]]
ResultProcessor = Callable[[ToolExecutionResult], Awaitable[ProcessedResult]]


async def _passthrough(result: ToolExecutionResult) -> ProcessedResult:
    return result.content, [], []


async def stream_tool_results(
    tool_results: list[ToolExecutionResult],
    *,
    messages: list[ChatMessage],
    agent_label: str,
    trace_id: str,
    args_by_id: dict[str, Any],
    process_result: ResultProcessor | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    measure_duration: bool = False,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream normalized tool events for a batch of results and feed them back.

    Shared by the experts and the harness. ``process_result`` post-processes a
    result's content (expert ``transform_tool_result`` hook, harness log
    pipeline) and may contribute extra timeline events to emit before (``pre``)
    and after (``post``) the tool event. ``on_event`` lets a caller observe every
    emitted event (the harness appends them to its state timeline).
    ``measure_duration`` stamps ``duration_ms`` on the tool event.
    """
    processor = process_result or _passthrough
    for result in tool_results:
        started = time.perf_counter() if measure_duration else None
        content, pre_events, post_events = await processor(result)
        for event in pre_events:
            if on_event:
                on_event(event)
            yield event
        duration_ms = (time.perf_counter() - started) * 1000 if started is not None else None
        tool_event = make_tool_event(
            agent=agent_label,
            tool=result.tool_name,
            status="completed" if result.success else "failed",
            evidence_id=result.call_id,
            summary=summarize(content),
            payload={"arguments": args_by_id.get(result.call_id, {})},
            trace_id=trace_id,
            span_id=f"tool:{result.call_id}",
            duration_ms=duration_ms,
        )
        if on_event:
            on_event(tool_event)
        yield tool_event
        for event in post_events:
            if on_event:
                on_event(event)
            yield event
        messages.append(
            ChatMessage(role="tool", content=content, tool_call_id=result.call_id)
        )
