from app.agent.aiops.events import (
    append_event,
    make_agent_event,
    make_decision_event,
    make_tool_event,
)


def test_make_agent_event_uses_normalized_shape():
    event = make_agent_event(
        agent="triage",
        stage="triage",
        status="completed",
        summary="Incident structured",
        payload={"incident_type": "slow_response"},
    )

    assert event == {
        "type": "agent_event",
        "agent": "triage",
        "stage": "triage",
        "status": "completed",
        "summary": "Incident structured",
        "payload": {"incident_type": "slow_response"},
    }


def test_make_tool_event_includes_evidence_id():
    event = make_tool_event(
        agent="evidence_collector",
        tool="query_metrics_alerts",
        status="completed",
        evidence_id="ev-1",
        summary="Latency rose after 14:20",
        payload={"duration_ms": 12},
    )

    assert event["type"] == "tool_event"
    assert event["agent"] == "evidence_collector"
    assert event["tool"] == "query_metrics_alerts"
    assert event["evidence_id"] == "ev-1"
    assert event["payload"] == {"duration_ms": 12}


def test_make_decision_event_defaults_payload_to_empty_dict():
    event = make_decision_event(
        agent="diagnosis",
        status="evidence_insufficient",
        summary="Need database evidence",
    )

    assert event == {
        "type": "decision_event",
        "agent": "diagnosis",
        "status": "evidence_insufficient",
        "summary": "Need database evidence",
        "payload": {},
    }


def test_append_event_preserves_existing_events():
    state = {"events": [{"type": "agent_event", "agent": "triage"}]}
    event = make_agent_event(
        agent="planner",
        stage="planning",
        status="completed",
        summary="Generated plan",
    )

    update = append_event(state, event)

    assert update == {
        "events": [
            {"type": "agent_event", "agent": "triage"},
            event,
        ]
    }
