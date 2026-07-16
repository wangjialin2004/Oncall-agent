"""Normalized timeline events shared by the Router + Expert Agents architecture.

Pure dict builders with no pipeline dependency. Emitted by the router and the
experts and streamed straight to the frontend.
"""

from __future__ import annotations

from typing import Any

AgentEvent = dict[str, Any]


def _with_trace(
    event: AgentEvent,
    *,
    trace_id: str | None,
    span_id: str | None,
    duration_ms: float | None,
    usage: dict[str, Any] | None,
    started_at: float | None = None,
) -> AgentEvent:
    """Attach optional observability fields without breaking existing callers.

    ``duration_ms`` is mirrored into ``payload`` as well so timeline panels can
    read it from a single source; the top-level field remains for backward
    compatibility. ``started_at`` is a perf_counter timestamp for cross-event
    ordering in the frontend timeline.
    """

    if trace_id:
        event["trace_id"] = trace_id
    if span_id:
        event["span_id"] = span_id
    if duration_ms is not None:
        rounded = round(float(duration_ms), 2)
        event["duration_ms"] = rounded
        payload = event.get("payload")
        if isinstance(payload, dict):
            payload.setdefault("duration_ms", rounded)
    if started_at is not None:
        event["started_at"] = round(float(started_at), 3)
    if usage:
        event["usage"] = usage
    return event


def make_route_event(
    *,
    route: str,
    reason: str,
    confidence: float | None = None,
    candidates: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> AgentEvent:
    """Routing decision emitted before any expert runs."""

    body = dict(payload or {})
    if confidence is not None:
        body.setdefault("confidence", confidence)
    if candidates:
        body.setdefault("candidates", candidates)
    event: AgentEvent = {
        "type": "route_event",
        "agent": "router",
        "route": route,
        "status": "completed",
        "summary": reason,
        "payload": body,
    }
    return _with_trace(
        event, trace_id=trace_id, span_id=span_id, duration_ms=None, usage=None
    )


def make_agent_event(
    *,
    agent: str,
    stage: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    duration_ms: float | None = None,
    usage: dict[str, Any] | None = None,
) -> AgentEvent:
    event: AgentEvent = {
        "type": "agent_event",
        "agent": agent,
        "stage": stage,
        "status": status,
        "summary": summary,
        "payload": payload or {},
    }
    return _with_trace(
        event, trace_id=trace_id, span_id=span_id, duration_ms=duration_ms, usage=usage
    )


def make_tool_event(
    *,
    agent: str,
    tool: str,
    status: str,
    evidence_id: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    duration_ms: float | None = None,
    usage: dict[str, Any] | None = None,
) -> AgentEvent:
    event: AgentEvent = {
        "type": "tool_event",
        "agent": agent,
        "tool": tool,
        "status": status,
        "evidence_id": evidence_id,
        "summary": summary,
        "payload": payload or {},
    }
    return _with_trace(
        event, trace_id=trace_id, span_id=span_id, duration_ms=duration_ms, usage=usage
    )


def make_decision_event(
    *,
    agent: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    duration_ms: float | None = None,
    usage: dict[str, Any] | None = None,
) -> AgentEvent:
    event: AgentEvent = {
        "type": "decision_event",
        "agent": agent,
        "status": status,
        "summary": summary,
        "payload": payload or {},
    }
    return _with_trace(
        event, trace_id=trace_id, span_id=span_id, duration_ms=duration_ms, usage=usage
    )
