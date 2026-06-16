"""Helpers for structured OnCall diagnosis plans."""

from __future__ import annotations

from typing import Any


def normalize_plan_steps(steps: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if isinstance(step, dict):
            description = str(step.get("description") or step.get("step") or step)
            tool_category = str(step.get("tool_category") or "unknown")
            expected_evidence = str(
                step.get("expected_evidence") or "Evidence requested by the plan step."
            )
        else:
            description = str(step)
            tool_category = "unknown"
            expected_evidence = "Evidence requested by the plan step."
        normalized.append(
            {
                "step_id": str(step.get("step_id"))
                if isinstance(step, dict) and step.get("step_id")
                else f"plan-{index}",
                "description": description,
                "tool_category": tool_category,
                "expected_evidence": expected_evidence,
            }
        )
    return normalized


def plan_step_text(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("description") or step)
    return str(step)


def pop_next_plan_step(plan: list[Any]) -> tuple[Any | None, list[Any]]:
    if not plan:
        return None, []
    return plan[0], plan[1:]
