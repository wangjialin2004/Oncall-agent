"""Tests for ``app.services.context_snapshot_service``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.context.operations import (
    append_observed_fact,
    append_recent_turns,
    append_tool_summary,
    set_intent,
)
from app.agent.context.persistence import state_from_dict, state_to_dict
from app.agent.context.state import (
    SCHEMA_VERSION,
    AgentContextState,
    EvidenceItem,
    ToolSummary,
)
from app.services.context_snapshot_service import (
    ContextSnapshotService,
    ContextSnapshotUnavailable,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "snapshot.db"


def _seeded_state() -> AgentContextState:
    state = AgentContextState(owner_key="u", session_id="s")
    set_intent(state, current_question="redis ok?", current_goal="ping redis")
    append_observed_fact(state, EvidenceItem(
        source="check_redis_health", status="success",
        facts=["ping ok"], raw_ref="timeline:1"))
    append_tool_summary(state, ToolSummary(
        tool="check_redis_health", status="success", latency_ms=42,
        note="ping ok", raw_ref="timeline:1"))
    append_recent_turns(state, [{"role": "user", "text": "hi"}], last_turn_index=1)
    return state


def test_save_and_load_roundtrip(db_path: Path) -> None:
    service = ContextSnapshotService(db_path=db_path)
    state = _seeded_state()
    service.save_context_snapshot(
        owner_key="u", session_id="s", state=state,
    )
    loaded = service.get_latest_context_snapshot("u", "s")
    assert loaded is not None
    assert loaded.intent.current_goal == "ping redis"
    assert loaded.evidence.observed_facts[0].source == "check_redis_health"
    assert loaded.conversation.recent_turns[0]["text"] == "hi"
    assert loaded.schema_version == SCHEMA_VERSION


def test_get_returns_none_when_missing(db_path: Path) -> None:
    service = ContextSnapshotService(db_path=db_path)
    assert service.get_latest_context_snapshot("u", "missing") is None


def test_get_raises_on_schema_version_mismatch(db_path: Path) -> None:
    service = ContextSnapshotService(db_path=db_path)
    state = _seeded_state()
    service.save_context_snapshot(owner_key="u", session_id="s", state=state)
    # Tamper with the stored version directly.
    import sqlite3
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE conversations SET context_state_version = ? "
            "WHERE owner_key = ? AND session_id = ?",
            (SCHEMA_VERSION + 999, "u", "s"),
        )
        conn.commit()
    with pytest.raises(ContextSnapshotUnavailable):
        service.get_latest_context_snapshot("u", "s")


def test_get_raises_on_corrupt_json(db_path: Path) -> None:
    service = ContextSnapshotService(db_path=db_path)
    state = _seeded_state()
    service.save_context_snapshot(owner_key="u", session_id="s", state=state)
    import sqlite3
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE conversations SET context_state_json = ? "
            "WHERE owner_key = ? AND session_id = ?",
            ("not-json", "u", "s"),
        )
        conn.commit()
    with pytest.raises(ContextSnapshotUnavailable):
        service.get_latest_context_snapshot("u", "s")


def test_snapshot_does_not_include_raw_tool_result(db_path: Path) -> None:
    service = ContextSnapshotService(db_path=db_path)
    state = _seeded_state()
    service.save_context_snapshot(owner_key="u", session_id="s", state=state)
    payload = state_to_dict(state)
    serialized = json.dumps(payload, ensure_ascii=False)
    # Raw tool payloads would contain arbitrary content — check the marker
    # 'raw_ref' is present and the schema doesn't carry an entire tool payload.
    assert "raw_ref" in serialized
    # Ensure no EvidenceItem accidentally gained an unbounded ``content`` field.
    for item in state.evidence.observed_facts:
        assert not hasattr(item, "raw_content")


def test_clear_removes_snapshot(db_path: Path) -> None:
    service = ContextSnapshotService(db_path=db_path)
    service.save_context_snapshot(
        owner_key="u", session_id="s", state=_seeded_state(),
    )
    assert service.get_latest_context_snapshot("u", "s") is not None
    service.clear_context_snapshot("u", "s")
    assert service.get_latest_context_snapshot("u", "s") is None


def test_save_overwrites_previous_snapshot(db_path: Path) -> None:
    service = ContextSnapshotService(db_path=db_path)
    first = _seeded_state()
    first.intent.current_goal = "goal-1"
    service.save_context_snapshot(owner_key="u", session_id="s", state=first)
    second = state_from_dict(state_to_dict(first))
    second.intent.current_goal = "goal-2"
    service.save_context_snapshot(owner_key="u", session_id="s", state=second)
    loaded = service.get_latest_context_snapshot("u", "s")
    assert loaded is not None and loaded.intent.current_goal == "goal-2"


def test_patch_tail_bounded_to_history_limit(db_path: Path) -> None:
    service = ContextSnapshotService(db_path=db_path)
    state = AgentContextState(owner_key="u", session_id="s")
    # Drive 1000 patches.
    from app.agent.context.operations import framework_patch
    from app.agent.context.state import SECTION_INTENT
    for _ in range(1000):
        framework_patch(state, SECTION_INTENT, "current_goal",
                       op="set", value="x", history_limit=10_000)
    service.save_context_snapshot(owner_key="u", session_id="s", state=state)
    loaded = service.get_latest_context_snapshot("u", "s")
    assert loaded is not None
    # Service truncates to _MAX_PATCH_TAIL (50).
    assert len(loaded.patch_tail) <= 50