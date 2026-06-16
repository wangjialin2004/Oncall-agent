from app.agent.aiops.executor import build_failed_step_update, build_success_step_update


def test_build_success_step_update_adds_past_step_evidence_and_event():
    step = {
        "step_id": "plan-1",
        "description": "Check metrics",
        "tool_category": "monitor",
        "expected_evidence": "Latency trend",
    }

    update = build_success_step_update(
        state={"events": [], "evidence": []},
        step=step,
        remaining_plan=[],
        result="Latency is high",
        evidence_records=[
            {
                "tool_name": "query_metrics_alerts",
                "tool_call_id": "call-1",
                "evidence_id": "ev-1",
                "source": "monitor",
                "success": True,
                "duration_ms": 9,
                "summary": "P95 latency high",
            }
        ],
    )

    assert update["plan"] == []
    assert update["past_steps"][0]["step_id"] == "plan-1"
    assert update["evidence"][0]["evidence_id"] == "ev-1"
    assert update["events"][-1]["type"] == "tool_event"
    assert update["events"][-1]["evidence_id"] == "ev-1"


def test_build_failed_step_update_records_failure_event():
    step = {"step_id": "plan-1", "description": "Check logs"}

    update = build_failed_step_update(
        state={"events": [], "evidence": []},
        step=step,
        remaining_plan=[],
        error=RuntimeError("tool unavailable"),
    )

    assert update["past_steps"][0]["status"] == "failed"
    assert update["evidence"][0]["status"] == "failed"
    assert "tool unavailable" in update["evidence"][0]["summary"]
    assert update["events"][-1]["status"] == "failed"
