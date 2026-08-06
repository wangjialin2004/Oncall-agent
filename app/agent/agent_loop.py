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
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
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

# Substrings that mark a non-retryable error. Retrying authorization,
# configuration, or invalid-input failures only wastes the investigation budget.
_NON_RETRYABLE_TOKENS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "permission denied",
    "authentication",
    "invalid argument",
    "invalid input",
    "not configured",
    "misconfigured",
    "unsupported",
    "not implemented",
    "dependency missing",
)
_STRUCTURED_FAILURE_STATUSES = frozenset({"error", "failed", "timeout", "timed_out"})
_NON_RETRYABLE_STRUCTURED_STATUSES = frozenset(
    {
        "disabled",
        "dependency_missing",
        "unsupported",
        "not_configured",
        "misconfigured",
        "configuration_error",
        "invalid_argument",
        "invalid_arguments",
        "invalid_input",
        "permission_denied",
        "unauthorized",
        "forbidden",
    }
)
_NON_RETRYABLE_ERROR_CODES = frozenset(
    {
        "invalid_timezone",
        "missing_request_scope",
        "invalid_argument",
        "invalid_input",
        "unsupported",
        "not_configured",
        "misconfigured",
    }
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
        if not tool_calls:
            return []

        parallel = bool(getattr(config, "harness_parallel_tool_calls", True)) and len(tool_calls) > 1

        async def _one(tool_call: ToolCall) -> ToolExecutionResult:
            tool = tool_by_name.get(tool_call.name)
            if tool is None:
                return _failed(tool_call, f"Tool not found: {tool_call.name}")
            if self.allowlist is not None and tool.name not in self.allowlist:
                return _failed(tool_call, f"Tool not allowed: {tool.name}")
            return await self._run_one(tool, tool_call)

        if parallel:
            # Read-only tool surface: run concurrent calls; preserve input order.
            return list(await asyncio.gather(*[_one(tc) for tc in tool_calls]))

        results: list[ToolExecutionResult] = []
        for tool_call in tool_calls:
            results.append(await _one(tool_call))
        return results

    async def _run_one(self, tool: RuntimeTool, tool_call: ToolCall) -> ToolExecutionResult:
        attempts = max(0, self.max_retries) + 1
        last_error = ""
        timeout_seconds = (
            float(tool.timeout_seconds)
            if tool.timeout_seconds is not None
            else self.timeout_seconds
        )
        for attempt in range(attempts):
            try:
                started_at = time.perf_counter()
                raw = await asyncio.wait_for(
                    run_tool(tool, tool_call.arguments),
                    timeout=timeout_seconds,
                )
                latency_ms = int((time.perf_counter() - started_at) * 1000)
            except TimeoutError:
                last_error = f"Tool execution timed out after {timeout_seconds:g}s"
                # Preserve the existing no-retry timeout boundary. A later model
                # turn may choose to retry after the upstream condition changes.
                return _failed(tool_call, last_error, retryable=True)
            except Exception as exc:
                last_error = f"Tool execution failed: {exc}"
                retryable = _is_retryable(exc)
                if retryable and attempt < attempts - 1:
                    await self._sleep_backoff(attempt, tool.name, last_error)
                    continue
                return _failed(tool_call, last_error, retryable=retryable)

            content = self._postprocess_output(_stringify_tool_result(raw))
            failure = _classify_tool_failure(raw)
            if failure is not None:
                reason, retryable = failure
                last_error = f"Tool reported structured failure: {reason}"
                if retryable and attempt < attempts - 1:
                    await self._sleep_backoff(attempt, tool.name, last_error)
                    continue
                return ToolExecutionResult(
                    call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=content,
                    success=False,
                    retryable=retryable,
                    raw=raw,
                    latency_ms=latency_ms,
                )
            return ToolExecutionResult(
                call_id=tool_call.id,
                tool_name=tool_call.name,
                content=content,
                success=True,
                raw=raw,
                latency_ms=latency_ms,
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


def _unwrap_tool_result(raw: Any) -> Any:
    return raw[0] if isinstance(raw, tuple) and raw else raw


def _structured_payload(raw: Any) -> Mapping[str, Any] | None:
    payload = _unwrap_tool_result(raw)
    structured_content = getattr(payload, "structuredContent", None)
    if isinstance(structured_content, Mapping):
        payload = structured_content
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, Mapping) else None


def _is_mcp_execution_error(raw: Any) -> bool:
    payload = _unwrap_tool_result(raw)
    if isinstance(payload, Mapping):
        return bool(payload.get("isError") is True or payload.get("is_error") is True)
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, Mapping):
            return bool(decoded.get("isError") is True or decoded.get("is_error") is True)
    return bool(
        getattr(payload, "isError", False) is True
        or getattr(payload, "is_error", False) is True
    )


def _failure_reason(payload: Mapping[str, Any]) -> str:
    for key in ("error", "message", "detail", "note", "error_code"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return str(payload.get("status") or "success=false").strip() or "success=false"


def _structured_failure_retryable(payload: Mapping[str, Any], reason: str) -> bool:
    explicit = payload.get("retryable")
    if isinstance(explicit, bool):
        return explicit

    if payload.get("source_available") is False or payload.get("capability_available") is False:
        return False
    status = str(payload.get("status") or "").strip().lower()
    if status in _NON_RETRYABLE_STRUCTURED_STATUSES:
        return False
    error_code = str(payload.get("error_code") or "").strip().lower()
    if error_code in _NON_RETRYABLE_ERROR_CODES:
        return False
    if str(payload.get("gap") or "").strip().lower() == "missing_change_datasource":
        return False
    return _is_retryable(RuntimeError(reason))


def _classify_tool_failure(raw: Any) -> tuple[str, bool] | None:
    """Return a provider-declared failure and whether it can be retried.

    Runtime tools frequently return structured payloads instead of raising. MCP
    tool execution errors use ``isError=True`` and must not be retried here,
    because the MCP client has already exhausted its own bounded retries.
    """

    if _is_mcp_execution_error(raw):
        reason = _stringify_tool_result(raw).strip() or "MCP tool reported an execution error"
        return reason, False

    payload = _structured_payload(raw)
    if payload is None:
        return None

    status = str(payload.get("status") or "").strip().lower()
    failed = payload.get("success") is False or status in _STRUCTURED_FAILURE_STATUSES
    if not failed:
        return None
    reason = _failure_reason(payload)
    return reason, _structured_failure_retryable(payload, reason)


def _structured_failure_reason(raw: Any) -> str | None:
    """Compatibility helper for existing callers that only need the reason."""

    failure = _classify_tool_failure(raw)
    return failure[0] if failure is not None else None


def _failed(
    tool_call: ToolCall,
    content: str,
    *,
    retryable: bool = False,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id=tool_call.id,
        tool_name=tool_call.name,
        content=content,
        success=False,
        retryable=retryable,
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
        tool_latency_ms = int(getattr(result, "latency_ms", 0) or 0)
        payload = {
            "arguments": args_by_id.get(result.call_id, {}),
            "result": content,
            "tool_latency_ms": tool_latency_ms,
        }
        if not result.success:
            # Private harness state needs this for later menu selection. The
            # public event projector intentionally does not allowlist it.
            payload["retryable"] = bool(result.retryable)
        tool_event = make_tool_event(
            agent=agent_label,
            tool=result.tool_name,
            status="completed" if result.success else "failed",
            evidence_id=result.call_id,
            summary=summarize(content),
            payload=payload,
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
