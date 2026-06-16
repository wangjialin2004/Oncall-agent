"""Normalized timeline events for the OnCall multi-agent workflow."""

from __future__ import annotations

from typing import Any

OnCallEvent = dict[str, Any]


def make_agent_event(
    *,
    agent: str,
    stage: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> OnCallEvent:
    return {
        "type": "agent_event",
        "agent": agent,
        "stage": stage,
        "status": status,
        "summary": summary,
        "payload": payload or {},
    }


def make_tool_event(
    *,
    agent: str,
    tool: str,
    status: str,
    evidence_id: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> OnCallEvent:
    return {
        "type": "tool_event",
        "agent": agent,
        "tool": tool,
        "status": status,
        "evidence_id": evidence_id,
        "summary": summary,
        "payload": payload or {},
    }


def make_decision_event(
    *,
    agent: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> OnCallEvent:
    return {
        "type": "decision_event",
        "agent": agent,
        "status": status,
        "summary": summary,
        "payload": payload or {},
    }


def append_event(state: dict[str, Any], event: OnCallEvent) -> dict[str, list[OnCallEvent]]:
    return {"events": list(state.get("events", [])) + [event]}
