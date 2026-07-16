"""M2 W8: delegate merge → whiteboard + full suite ordering."""

from __future__ import annotations

from app.agent.context.state import AgentContextState
from app.agent.context.tools_evidence import record_delegate_merge
from app.agent.harness.sub_harness import merge_delegate_results
from scripts.evaluate_oncall_local import FULL_IDS, load_cases


def test_record_delegate_merge_writes_observed_fact():
    state = AgentContextState(owner_key="o", session_id="s")
    record_delegate_merge(
        state,
        mode="parallel",
        experts=["metric", "log"],
        folded_tools=["query_prometheus_alerts", "search_logs", "query_prometheus_alerts"],
        status="completed",
        wall_ms=42,
    )
    assert state.evidence.tool_summaries
    assert any(s.tool == "delegate_merge" for s in state.evidence.tool_summaries)
    note = " ".join(
        s.note for s in state.evidence.tool_summaries if s.tool == "delegate_merge"
    )
    assert "metric" in note and "log" in note
    assert "query_prometheus_alerts" in note
    assert state.evidence.observed_facts
    assert any("delegate_merge" in (f.raw_ref or "") for f in state.evidence.observed_facts)


def test_aux_style_merge_then_record():
    results = [
        {
            "expert": "metric",
            "status": "completed",
            "answer": "cpu high",
            "events": [
                {"type": "tool_event", "tool": "query_prometheus_alerts", "status": "completed"},
            ],
        },
        {
            "expert": "log",
            "status": "completed",
            "answer": "errors",
            "events": [
                {"type": "tool_event", "tool": "search_logs", "status": "completed"},
            ],
        },
    ]
    merged = merge_delegate_results(results)
    state = AgentContextState(owner_key="o", session_id="s")
    record_delegate_merge(
        state,
        mode="parallel",
        experts=list(merged.get("experts") or []),
        folded_tools=list(merged.get("folded_tools") or []),
        status=str(merged.get("status") or "completed"),
        wall_ms=10,
    )
    assert merged["status"] == "completed"
    assert set(merged["folded_tools"]) >= {"query_prometheus_alerts", "search_logs"}
    assert any(s.tool == "delegate_merge" for s in state.evidence.tool_summaries)


def test_full_suite_loads_twenty_three_ordered_cases():
    cases = load_cases(suite="full")
    assert len(cases) == 23
    ids = [c["id"] for c in cases]
    # FULL_IDS order for known cases
    for expected, actual in zip(FULL_IDS, ids):
        assert expected == actual
    assert "P1-parallel-cross-domain" in ids
    assert "N2-no-write-action" in ids
    assert "K3-experience-recall" in ids
