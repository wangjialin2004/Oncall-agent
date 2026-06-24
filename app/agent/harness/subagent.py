"""Serial expert delegation support for the harness."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from typing import Any

from app.agent.experts.registry import DEFAULT_ROUTE, EXPERT_ROUTES, get_expert
from app.config import config
from app.core.runtime_tools import RuntimeTool


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
        route = str(arguments.get("expert") or DEFAULT_ROUTE).strip()
        if route not in EXPERT_ROUTES:
            route = DEFAULT_ROUTE
        subtask = str(arguments.get("subtask") or "").strip()
        if not subtask:
            return {
                "expert": route,
                "status": "failed",
                "answer": "",
                "error": "subtask is required",
            }

        expert = expert_getter(route)
        answer_parts: list[str] = []
        timeline: list[dict[str, Any]] = []
        generator = expert.run(
            message=subtask,
            session_id=session_id,
            trace_id=f"{trace_id}:delegate:{route}",
            context=context_getter(),
        )

        async def _drain() -> None:
            async for event in generator:
                event_type = event.get("type")
                if event_type == "content":
                    answer_parts.append(str(event.get("data") or ""))
                elif event_type in {"agent_event", "tool_event", "decision_event"}:
                    timeline.append(event)

        # 子专家独立限时，避免单个慢子专家吃光父级 harness 的总超时
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
                "subtask": subtask,
                "answer": "".join(answer_parts),
                "error": f"delegate timed out after {resolved_timeout:g}s",
                "events": timeline[-12:],
            }

        return {
            "expert": route,
            "status": "completed",
            "subtask": subtask,
            "answer": "".join(answer_parts),
            "events": timeline[-12:],
        }

    return RuntimeTool(
        name="delegate_to_expert",
        description=(
            "将只读子任务串行委派给一个专项专家。expert 可选：knowledge、metric、log、"
            "change、diagnosis；subtask 为清晰、独立的排查问题。"
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
    )


async def _aclose(generator: AsyncGenerator[dict[str, Any], None]) -> None:
    aclose = getattr(generator, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        pass
