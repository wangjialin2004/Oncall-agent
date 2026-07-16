"""Tests for ``app.agent.context.state``."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.agent.context.state import (
    SCHEMA_VERSION,
    AgentContextState,
    ConversationBlock,
    EvidenceItem,
    EvidenceState,
    IdentityBlock,
    IntentBlock,
    OutputBlock,
    SECTION_EVIDENCE,
    SECTION_IDENTITY,
    SECTION_INTENT,
    SECTION_NAMES,
    SECTION_OUTPUT,
    SECTION_TOOL,
    SECTION_WORKING,
    ToolBlock,
    ToolSummary,
    WorkingBlock,
)


def _sample_state() -> AgentContextState:
    state = AgentContextState(owner_key="u", session_id="s")
    state.identity.case_id = "c-1"
    state.intent.current_goal = "查 Redis 告警"
    state.working.plan = "ping → info"
    state.evidence.observed_facts.append(
        EvidenceItem(source="check_redis_health", status="success", facts=["ping ok"])
    )
    return state


def test_default_state_has_all_sections() -> None:
    state = AgentContextState()
    assert state.schema_version == SCHEMA_VERSION
    assert state.version == 0
    assert isinstance(state.updated_at, str) and state.updated_at
    for section in SECTION_NAMES:
        assert state.section(section) is not None


def test_section_dispatch_returns_block_dataclass() -> None:
    state = _sample_state()
    assert state.section(SECTION_IDENTITY) is state.identity
    assert state.section(SECTION_INTENT) is state.intent
    assert state.section(SECTION_WORKING) is state.working
    assert state.section(SECTION_EVIDENCE) is state.evidence
    assert state.section(SECTION_TOOL) is state.tool
    assert state.section(SECTION_OUTPUT) is state.output
    # Conversation is the seventh and final section.
    assert isinstance(state.section("conversation"), ConversationBlock)


def test_section_dispatch_rejects_unknown() -> None:
    state = AgentContextState()
    with pytest.raises(KeyError):
        state.section("runtime")  # Removed in plan §3.2


def test_record_patch_drops_oldest() -> None:
    state = AgentContextState()
    state.record_patch({"op": "noop", "i": 0}, history_limit=3)
    state.record_patch({"op": "noop", "i": 1}, history_limit=3)
    state.record_patch({"op": "noop", "i": 2}, history_limit=3)
    state.record_patch({"op": "noop", "i": 3}, history_limit=3)
    assert [entry["i"] for entry in state.patch_tail] == [1, 2, 3]


def test_record_patch_disabled_when_limit_zero() -> None:
    state = AgentContextState()
    state.record_patch({"op": "noop"}, history_limit=0)
    state.record_patch({"op": "noop"}, history_limit=-1)
    assert state.patch_tail == []


def test_touch_updates_timestamp() -> None:
    state = AgentContextState()
    first = state.updated_at
    state.touch(at="2026-07-08T00:00:00+00:00")
    assert state.updated_at == "2026-07-08T00:00:00+00:00"
    assert state.updated_at != first


def test_evidence_block_split_enforced_by_type() -> None:
    state = AgentContextState()
    state.evidence.observed_facts.append(EvidenceItem(source="t", status="ok"))
    state.evidence.tool_summaries.append(ToolSummary(tool="t", status="ok"))
    state.evidence.model_notes.append("可能与昨天故障相关")
    assert len(state.evidence.observed_facts) == 1
    assert len(state.evidence.tool_summaries) == 1
    assert state.evidence.model_notes == ["可能与昨天故障相关"]


def test_block_dataclass_slots_prevent_unexpected_attrs() -> None:
    state = _sample_state()
    with pytest.raises(AttributeError):
        state.identity.does_not_exist  # type: ignore[attr-defined]


def test_updated_at_is_iso_string() -> None:
    state = AgentContextState()
    # Should parse as ISO 8601 — failing would mean the helper regressed.
    datetime.fromisoformat(state.updated_at)
