"""M3 W10: structured clarification payload for frontend chips."""

from __future__ import annotations

import asyncio

from app.agent.harness.clarifier import ClarificationRequest
from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessState


def test_emit_clarification_includes_structured_fields():
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t-clarify", session_id="s-c", owner_key="o")
    req = ClarificationRequest(
        missing_params=["service", "time_window"],
        question="请补充服务名与时间窗。",
        reason="missing required params",
        evidence_gap="no service",
        defaults={},
    )

    async def _collect():
        events = []
        async for event in svc._emit_clarification(req, state=state, started=0.0):
            events.append(event)
        return events

    events = asyncio.run(_collect())
    types = [e.get("type") for e in events]
    assert "decision_event" in types or any(
        e.get("stage") == "clarify" for e in events if isinstance(e, dict)
    )
    decision = next(
        (
            e
            for e in events
            if e.get("type") == "decision_event" or e.get("stage") == "clarify"
        ),
        None,
    )
    assert decision is not None
    payload = decision.get("payload") or {}
    missing = payload.get("missing_params") or decision.get("missing_params")
    assert "service" in (missing or [])

    complete = next((e for e in events if e.get("type") == "complete"), None)
    assert complete is not None
    assert "service" in (complete.get("missing_params") or [])
    clar = complete.get("clarification") or {}
    assert "time_window" in (clar.get("missing_params") or [])


def test_clarify_missing_params_event_payload():
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t", session_id="s", owner_key="o")
    req = ClarificationRequest(
        missing_params=["env"],
        question="环境？",
        reason="r",
        evidence_gap="g",
        defaults={"env": "prod"},
    )
    event = svc._clarify_missing_params_event(req, state=state)
    assert event["stage"] == "clarify_missing_params"
    assert event["payload"]["missing_params"] == ["env"]
    assert event["payload"]["defaults"]["env"] == "prod"
