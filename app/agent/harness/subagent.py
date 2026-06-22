"""Serial expert delegation support for the harness."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agent.experts.registry import DEFAULT_ROUTE, EXPERT_ROUTES, get_expert
from app.core.runtime_tools import RuntimeTool


def create_delegate_tool(
    *,
    session_id: str,
    trace_id: str,
    context_getter: Callable[[], str],
    expert_getter: Callable[[str], Any] = get_expert,
) -> RuntimeTool:
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
        async for event in expert.run(
            message=subtask,
            session_id=session_id,
            trace_id=f"{trace_id}:delegate:{route}",
            context=context_getter(),
        ):
            event_type = event.get("type")
            if event_type == "content":
                answer_parts.append(str(event.get("data") or ""))
            elif event_type in {"agent_event", "tool_event", "decision_event"}:
                timeline.append(event)

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
