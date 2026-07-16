"""Tests for ``app.agent.context.tools_evidence``."""

from __future__ import annotations

import pytest

from app.agent.context.operations import (
    ContextPatchError,
    framework_patch,
    llm_note,
)
from app.agent.context.state import (
    AgentContextState,
    SECTION_EVIDENCE,
    SECTION_IDENTITY,
)
from app.agent.context.tools_evidence import (
    MAX_NOTE_CHARS,
    record_tool_call_meta,
    record_tool_result,
    tool_result_to_summary,
)


def _state() -> AgentContextState:
    state = AgentContextState(owner_key="u", session_id="s")
    framework_patch(state, SECTION_IDENTITY, "case_id", op="set", value="c-1")
    return state


def test_record_tool_success_appends_observed_fact() -> None:
    state = _state()
    record_tool_result(
        state,
        tool_name="check_redis_health",
        success=True,
        latency_ms=42,
        raw_ref="timeline:1",
        content_summary="ping ok",
        fact="Redis PING 成功",
    )
    assert len(state.evidence.observed_facts) == 1
    item = state.evidence.observed_facts[0]
    assert item.source == "check_redis_health"
    assert item.facts == ["Redis PING 成功"]
    assert item.raw_ref == "timeline:1"
    assert state.evidence.tool_summaries[0].latency_ms == 42


def test_record_tool_failure_appends_gap() -> None:
    state = _state()
    record_tool_result(
        state,
        tool_name="check_redis_health",
        success=False,
        latency_ms=2000,
        raw_ref="timeline:2",
        content_summary="timeout",
    )
    assert state.evidence.observed_facts == []
    assert "timeout" in state.evidence.evidence_gaps[0]


def test_record_tool_does_not_store_raw_payload() -> None:
    huge = "x" * 10_000
    state = _state()
    record_tool_result(
        state,
        tool_name="t",
        success=True,
        latency_ms=10,
        raw_ref="timeline:3",
        content_summary=huge,
        fact=huge,
    )
    # No 10k chars anywhere.
    assert all(
        all(len(f) <= 121 for f in item.facts)
        for item in state.evidence.observed_facts
    )
    assert all(len(s.note) <= MAX_NOTE_CHARS for s in state.evidence.tool_summaries)


def test_record_tool_failure_without_note_is_noop() -> None:
    state = _state()
    record_tool_result(
        state,
        tool_name="t",
        success=False,
        latency_ms=10,
        raw_ref="timeline:4",
        content_summary="",
    )
    # No fact, no gap → only the ToolSummary row was added.
    assert state.evidence.observed_facts == []
    assert state.evidence.evidence_gaps == []
    assert len(state.evidence.tool_summaries) == 1


def test_tool_call_meta_caps_recent_calls() -> None:
    state = _state()
    for i in range(40):
        record_tool_call_meta(state, tool_name=f"t{i}", raw_ref=f"r{i}",
                              latency_ms=i, status="ok")
    assert len(state.tool.recent_calls) <= 32


def test_tool_result_to_summary_uses_summary_field() -> None:
    summary, fact, ok, latency = tool_result_to_summary(
        {"ok": True, "summary": "ping ok", "latency_ms": 12},
    )
    assert summary == "ping ok"
    assert ok is True
    assert latency == 12
    assert fact is None


def test_tool_result_to_summary_falls_back_to_content() -> None:
    summary, fact, ok, latency = tool_result_to_summary(
        {"ok": True, "content": "some long content"},
    )
    assert "some long content" in summary
    assert ok is True


def test_tool_result_to_summary_picks_up_fact() -> None:
    summary, fact, _, _ = tool_result_to_summary(
        {"ok": True, "summary": "ok", "fact": "Redis 健康"},
    )
    assert fact == "Redis 健康"


def test_tool_result_to_summary_reduces_delegate_json_blob() -> None:
    import json

    blob = json.dumps(
        {
            "expert": "metric",
            "status": "completed",
            "subtask": "对 checkout-api 的 CPU 告警做初步排查",
            "answer": "当前无 firing 告警",
        },
        ensure_ascii=False,
    )
    summary, fact, ok, latency = tool_result_to_summary(
        {"ok": True, "summary": blob, "latency_ms": 67953},
    )
    assert ok is True
    assert latency == 67953
    assert fact is None
    assert "expert=metric" in summary
    assert "status=completed" in summary
    assert "subtask=" in summary
    assert "{" not in summary


def test_tool_result_to_summary_reduces_context_read_payload() -> None:
    import json

    blob = json.dumps(
        {
            "section": "evidence",
            "data": {
                "observed_facts": [],
                "tool_summaries": [{"tool": "delegate_to_expert", "status": "success"}],
                "evidence_gaps": [],
                "model_notes": [],
            },
        },
        ensure_ascii=False,
    )
    summary, _, ok, _ = tool_result_to_summary({"ok": True, "summary": blob})
    assert ok is True
    assert "section=evidence" in summary
    assert "tools=1" in summary
    assert "{" not in summary


def test_tool_result_to_summary_handles_double_encoded_json() -> None:
    import json

    inner = json.dumps(
        {"expert": "metric", "status": "completed", "subtask": "查 CPU"},
        ensure_ascii=False,
    )
    outer = json.dumps(inner, ensure_ascii=False)
    summary, _, ok, _ = tool_result_to_summary({"ok": True, "content": outer})
    assert ok is True
    assert "expert=metric" in summary
    assert "status=completed" in summary


def test_llm_cannot_inject_observed_facts_via_tools() -> None:
    """An LLM-controlled code path must not be able to bypass the ops allowlist."""
    state = _state()
    # Direct llm_note attempt should fail even with a "fact" tool call.
    with pytest.raises(ContextPatchError):
        llm_note(state, SECTION_EVIDENCE, "observed_facts", op="append",
                 append_value={"source": "x", "status": "ok"})
