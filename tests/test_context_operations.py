"""Tests for ``app.agent.context.operations``."""

from __future__ import annotations

import pytest

from app.agent.context.operations import (
    ContextPatchError,
    append_evidence_gap,
    append_observed_fact,
    append_recent_turns,
    append_tool_summary,
    framework_patch,
    llm_note,
    set_intent,
)
from app.agent.context.state import (
    AgentContextState,
    EvidenceItem,
    IntentBlock,
    SECTION_EVIDENCE,
    SECTION_IDENTITY,
    SECTION_INTENT,
    SECTION_WORKING,
    ToolSummary,
)


def test_framework_patch_set_increments_version() -> None:
    state = AgentContextState()
    result = framework_patch(state, SECTION_INTENT, "current_goal", op="set", value="x")
    assert result.version_after == 1
    assert state.intent.current_goal == "x"
    assert state.version == 1


def test_framework_patch_append_to_list() -> None:
    state = AgentContextState()
    append_evidence_gap(state, "no current_goal set")
    append_evidence_gap(state, "redis client missing")
    assert state.evidence.evidence_gaps == [
        "no current_goal set",
        "redis client missing",
    ]
    assert state.version == 2


def test_framework_patch_rejects_unknown_field() -> None:
    state = AgentContextState()
    with pytest.raises(ContextPatchError):
        framework_patch(state, SECTION_INTENT, "does_not_exist", op="set", value="x")


def test_framework_patch_rejects_unknown_op() -> None:
    state = AgentContextState()
    with pytest.raises(ContextPatchError):
        framework_patch(state, SECTION_INTENT, "current_goal", op="flip", value="x")


def test_llm_note_allowed_field_succeeds() -> None:
    state = AgentContextState()
    llm_note(state, SECTION_EVIDENCE, "model_notes", op="append", append_value="h1")
    llm_note(state, SECTION_EVIDENCE, "model_notes", op="append", append_value="h2")
    assert state.evidence.model_notes == ["h1", "h2"]
    # Patch audit trail records writer="llm".
    assert all(entry["writer"] == "llm" for entry in state.patch_tail)


def test_llm_note_blocked_from_observed_facts() -> None:
    state = AgentContextState()
    item = EvidenceItem(source="t", status="ok")
    with pytest.raises(ContextPatchError):
        llm_note(state, SECTION_EVIDENCE, "observed_facts", op="append", append_value=item)


def test_llm_note_blocked_from_identity() -> None:
    state = AgentContextState()
    with pytest.raises(ContextPatchError):
        llm_note(state, SECTION_IDENTITY, "case_id", op="set", value="x")


def test_llm_note_can_edit_working_plan() -> None:
    state = AgentContextState()
    llm_note(state, SECTION_WORKING, "plan", op="set", value="plan v1")
    assert state.working.plan == "plan v1"


def test_framework_patch_merge_dict() -> None:
    state = AgentContextState()
    state.tool.recent_calls.append({"tool": "ping", "status": "ok"})
    # tool.recent_calls is a list; merge should fail loudly.
    with pytest.raises(ContextPatchError):
        framework_patch(state, "tool", "recent_calls", op="merge", merge_value={"x": 1})


def test_framework_patch_delete_dict_key() -> None:
    state = AgentContextState()
    # Identity.block is dataclass — pick a real dict field: tool.do_not_repeat.
    framework_patch(state, "tool", "do_not_repeat", op="append", append_value="bad")
    state.tool.do_not_repeat = {"bad": True}  # type: ignore[assignment]
    framework_patch(state, "tool", "do_not_repeat", op="delete", value="bad")
    assert state.tool.do_not_repeat == {}  # type: ignore[comparison-overlap]


def test_append_recent_turns_replaces_snapshot() -> None:
    state = AgentContextState()
    append_recent_turns(
        state,
        [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "..."}],
        last_turn_index=2,
    )
    assert state.conversation.recent_turns[0]["text"] == "hi"
    assert state.conversation.last_turn_index == 2


def test_set_intent_updates_both_when_provided() -> None:
    state = AgentContextState()
    set_intent(state, current_question="redis ok?", current_goal="查 redis")
    assert state.intent.current_question == "redis ok?"
    assert state.intent.current_goal == "查 redis"


def test_append_observed_fact_records_evidence() -> None:
    state = AgentContextState()
    item = EvidenceItem(source="check_redis_health", status="success",
                        facts=["ping ok"], raw_ref="timeline:1")
    append_observed_fact(state, item)
    assert state.evidence.observed_facts[0].source == "check_redis_health"
    assert state.evidence.observed_facts[0].raw_ref == "timeline:1"


def test_append_tool_summary_records_summary() -> None:
    state = AgentContextState()
    s = ToolSummary(tool="check_redis_health", status="success", latency_ms=42,
                    note="ping ok", raw_ref="timeline:1")
    append_tool_summary(state, s)
    assert state.evidence.tool_summaries[0].tool == "check_redis_health"
    assert state.evidence.tool_summaries[0].latency_ms == 42


def test_patch_history_limit_trims_oldest() -> None:
    state = AgentContextState()
    for i in range(5):
        framework_patch(state, SECTION_INTENT, "current_goal",
                       op="set", value=f"v{i}", history_limit=3)
    assert len(state.patch_tail) == 3
    versions = [entry["version"] for entry in state.patch_tail]
    assert versions == [3, 4, 5]


def test_patch_history_limit_zero_disables_tail() -> None:
    state = AgentContextState()
    framework_patch(state, SECTION_INTENT, "current_goal",
                    op="set", value="x", history_limit=0)
    assert state.patch_tail == []


def test_validate_unknown_section_raises() -> None:
    state = AgentContextState()
    with pytest.raises(ContextPatchError):
        framework_patch(state, "runtime", "foo", op="set", value="x")
