"""Serial and parallel expert delegation support for the harness."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from typing import Any

from app.agent.experts.registry import DEFAULT_ROUTE, EXPERT_ROUTES, get_expert
from app.agent.harness.sub_harness import merge_delegate_results
from app.config import config
from app.core.runtime_tools import RuntimeTool

_VALID_AUX_MODES = frozenset({"off", "serial", "parallel"})


def aux_execution_mode() -> str:
    """Return normalized aux execution mode; invalid values fall back to off."""
    raw = str(getattr(config, "router_aux_execution_mode", "parallel") or "parallel")
    mode = raw.strip().lower()
    if mode not in _VALID_AUX_MODES:
        return "off"
    return mode


def parallel_max_experts() -> int:
    return max(1, int(getattr(config, "harness_parallel_max_experts", 3) or 3))


def aux_max_probes() -> int:
    return max(0, int(getattr(config, "router_aux_max_probes", 2) or 0))


def normalize_delegate_pairs(
    experts: Sequence[Any],
    subtasks: Sequence[Any],
    *,
    max_experts: int | None = None,
) -> tuple[list[tuple[str, str]], bool]:
    """Zip/clean expert+subtask pairs; de-dupe experts; optionally truncate.

    Returns ``(pairs, truncated)``.
    """
    cap = parallel_max_experts() if max_experts is None else max(1, int(max_experts))
    expert_list = list(experts or [])
    subtask_list = list(subtasks or [])
    n = min(len(expert_list), len(subtask_list))
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for idx in range(n):
        route = str(expert_list[idx] or "").strip()
        if route not in EXPERT_ROUTES:
            route = DEFAULT_ROUTE
        subtask = str(subtask_list[idx] or "").strip()
        if not subtask or route in seen:
            continue
        seen.add(route)
        pairs.append((route, subtask))
    truncated = len(pairs) > cap
    return pairs[:cap], truncated


async def run_one_delegate(
    *,
    expert: str,
    subtask: str,
    session_id: str,
    trace_id: str,
    context: str = "",
    expert_getter: Callable[[str], Any] = get_expert,
    timeout_seconds: float | None = None,
    max_tool_rounds: int | None = None,
    evidence_only: bool | None = None,
) -> dict[str, Any]:
    """Run a single serial expert delegation and return a structured result."""
    route = str(expert or DEFAULT_ROUTE).strip()
    if route not in EXPERT_ROUTES:
        route = DEFAULT_ROUTE
    task = str(subtask or "").strip()
    if not task:
        return {
            "expert": route,
            "status": "failed",
            "subtask": "",
            "answer": "",
            "error": "subtask is required",
            "events": [],
        }

    resolved_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(getattr(config, "harness_delegate_timeout_seconds", 45.0) or 0.0)
    )
    round_cap = max(
        1,
        int(
            max_tool_rounds
            if max_tool_rounds is not None
            else getattr(config, "harness_delegate_max_tool_rounds", 1) or 1
        ),
    )
    close_after_tools = (
        bool(evidence_only)
        if evidence_only is not None
        else bool(getattr(config, "harness_delegate_evidence_only", True))
    )

    expert_obj = expert_getter(route)
    answer_parts: list[str] = []
    timeline: list[dict[str, Any]] = []
    run_kwargs: dict[str, Any] = {
        "message": task,
        "session_id": session_id,
        "trace_id": f"{trace_id}:delegate:{route}",
        "context": context,
    }
    # Older injected test doubles may not expose the optional knobs.
    try:
        parameters = inspect.signature(expert_obj.run).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "max_tool_rounds" in parameters:
        run_kwargs["max_tool_rounds"] = round_cap
    if "close_after_tools" in parameters:
        run_kwargs["close_after_tools"] = close_after_tools
    generator = expert_obj.run(**run_kwargs)

    async def _drain() -> None:
        async for event in generator:
            event_type = event.get("type")
            if event_type == "content":
                answer_parts.append(str(event.get("data") or ""))
            elif event_type in {"agent_event", "tool_event", "decision_event"}:
                timeline.append(event)

    try:
        if resolved_timeout > 0:
            async with asyncio.timeout(resolved_timeout):
                await _drain()
        else:
            await _drain()
    except TimeoutError:
        await _aclose(generator)
        return {
            "expert": route,
            "status": "degraded",
            "subtask": task,
            "answer": "".join(answer_parts),
            "error": f"delegate timed out after {resolved_timeout:g}s",
            "events": timeline[-12:],
        }
    except Exception as exc:  # pragma: no cover - defensive
        await _aclose(generator)
        return {
            "expert": route,
            "status": "failed",
            "subtask": task,
            "answer": "".join(answer_parts),
            "error": f"delegate failed: {exc}",
            "events": timeline[-12:],
        }

    return {
        "expert": route,
        "status": "completed",
        "subtask": task,
        "answer": "".join(answer_parts),
        "events": timeline[-12:],
    }


async def run_parallel_delegates(
    pairs: Sequence[tuple[str, str]],
    *,
    session_id: str,
    trace_id: str,
    context: str = "",
    expert_getter: Callable[[str], Any] = get_expert,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Fan-out multiple experts with ``asyncio.gather``; isolate per-expert failures."""
    started = time.perf_counter()
    if not pairs:
        return {
            "status": "failed",
            "parallel": True,
            "wall_ms": 0,
            "results": [],
            "error": "no_valid_expert_subtask_pairs",
            "truncated": False,
        }

    async def _one(route: str, subtask: str) -> dict[str, Any]:
        return await run_one_delegate(
            expert=route,
            subtask=subtask,
            session_id=session_id,
            trace_id=f"{trace_id}:parallel",
            context=context,
            expert_getter=expert_getter,
            timeout_seconds=timeout_seconds,
        )

    gathered = await asyncio.gather(
        *[_one(route, subtask) for route, subtask in pairs],
        return_exceptions=True,
    )
    results: list[dict[str, Any]] = []
    for idx, item in enumerate(gathered):
        route, subtask = pairs[idx]
        if isinstance(item, BaseException):
            results.append(
                {
                    "expert": route,
                    "status": "failed",
                    "subtask": subtask,
                    "answer": "",
                    "error": f"delegate failed: {item}",
                    "events": [],
                }
            )
        else:
            results.append(item)

    wall_ms = int((time.perf_counter() - started) * 1000)
    merged = merge_delegate_results(results)
    return {
        "status": merged.get("status") or "degraded",
        "parallel": True,
        "wall_ms": wall_ms,
        "results": list(merged.get("results") or results),
        "folded_tools": list(merged.get("folded_tools") or []),
        "conflicts": list(merged.get("conflicts") or []),
        "truncated": False,
    }


def create_delegate_tool(
    *,
    session_id: str,
    trace_id: str,
    context_getter: Callable[[], str],
    expert_getter: Callable[[str], Any] = get_expert,
    timeout_seconds: float | None = None,
) -> RuntimeTool:
    resolved_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(getattr(config, "harness_delegate_timeout_seconds", 45.0) or 0.0)
    )

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        return await run_one_delegate(
            expert=str(arguments.get("expert") or DEFAULT_ROUTE),
            subtask=str(arguments.get("subtask") or ""),
            session_id=session_id,
            trace_id=trace_id,
            context=context_getter(),
            expert_getter=expert_getter,
            timeout_seconds=resolved_timeout,
        )

    return RuntimeTool(
        name="delegate_to_expert",
        description=(
            "将只读子任务串行委派给一个专项专家。expert 可选：knowledge、metric、log、"
            "change、diagnosis；subtask 为清晰、独立的排查问题。"
            "跨域同时需要多个专家时优先使用 delegate_parallel。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "expert": {
                    "type": "string",
                    "enum": list(EXPERT_ROUTES),
                    "description": "要委派的专家领域。",
                },
                "subtask": {
                    "type": "string",
                    "description": "委派给专家的具体只读排查子任务。",
                },
            },
            "required": ["expert", "subtask"],
        },
        handler=handler,
        timeout_seconds=resolved_timeout,
    )


def create_delegate_parallel_tool(
    *,
    session_id: str,
    trace_id: str,
    context_getter: Callable[[], str],
    expert_getter: Callable[[str], Any] = get_expert,
    timeout_seconds: float | None = None,
) -> RuntimeTool:
    resolved_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(getattr(config, "harness_delegate_timeout_seconds", 45.0) or 0.0)
    )
    max_experts = parallel_max_experts()

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        if not bool(getattr(config, "harness_parallel_delegation_enabled", True)):
            return {
                "status": "failed",
                "parallel": True,
                "wall_ms": 0,
                "results": [],
                "error": "parallel_delegation_disabled",
                "truncated": False,
            }
        experts = arguments.get("experts") or []
        subtasks = arguments.get("subtasks") or []
        if not isinstance(experts, list):
            experts = [experts]
        if not isinstance(subtasks, list):
            subtasks = [subtasks]
        pairs, truncated = normalize_delegate_pairs(
            experts, subtasks, max_experts=max_experts
        )
        if not pairs:
            return {
                "status": "failed",
                "parallel": True,
                "wall_ms": 0,
                "results": [],
                "error": "experts/subtasks required and must form valid pairs",
                "truncated": truncated,
            }
        payload = await run_parallel_delegates(
            pairs,
            session_id=session_id,
            trace_id=trace_id,
            context=context_getter(),
            expert_getter=expert_getter,
            timeout_seconds=resolved_timeout,
        )
        payload["truncated"] = bool(truncated)
        return payload

    return RuntimeTool(
        name="delegate_parallel",
        description=(
            "将多个只读子任务并行委派给不同专项专家（asyncio fan-out）。"
            "experts 与 subtasks 等长；同一专家只保留第一条；最多 "
            f"{max_experts} 个专家。适用于跨域同时查 metric+log 等场景。"
            "仍为只读，禁止处置动作。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "experts": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(EXPERT_ROUTES)},
                    "description": "要并行委派的专家列表。",
                },
                "subtasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "与 experts 一一对应的只读子任务。",
                },
            },
            "required": ["experts", "subtasks"],
        },
        handler=handler,
        # Outer tool timeout covers the whole fan-out; each expert still has its own cap.
        timeout_seconds=resolved_timeout * max(1, min(3, max_experts)),
    )


def build_aux_subtask(message: str, aux_route: str) -> str:
    """Deterministic aux probe subtask — no extra LLM call."""
    clipped = (message or "").strip()
    if len(clipped) > 400:
        clipped = f"{clipped[:400]}..."
    return (
        f"只读补充排查：结合用户问题，从 {aux_route} 视角收集证据。"
        f"问题：{clipped or '（空）'}"
    )


def merge_delegate_answers(results: Sequence[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in results:
        expert = str(item.get("expert") or "?")
        status = str(item.get("status") or "")
        answer = str(item.get("answer") or "").strip()
        error = str(item.get("error") or "").strip()
        if answer:
            parts.append(f"[{expert}/{status}] {answer[:1500]}")
        elif error:
            parts.append(f"[{expert}/{status}] {error[:500]}")
        else:
            parts.append(f"[{expert}/{status}] （无文本证据）")
    return "\n".join(parts)


async def _aclose(generator: AsyncGenerator[dict[str, Any], None]) -> None:
    aclose = getattr(generator, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        pass
