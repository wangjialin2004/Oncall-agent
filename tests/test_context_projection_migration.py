"""Tests for projection migration dry-run / apply policy."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from app.agent.context.operations import append_recent_turns, set_intent
from app.agent.context.persistence import state_to_dict
from app.agent.context.projection import is_compact_projection
from app.agent.context.state import AgentContextState
from app.services.conversation_service import ConversationService
from scripts.migrate_context_projection import apply, dry_run
from tests._context_db import create_verified_backup, initialize_context_db


def _write_legacy_projection(db_path: Path, state: AgentContextState) -> None:
    timestamp = "2026-08-01T00:00:00Z"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO conversations (owner_key, session_id, title, created_at, updated_at)
            VALUES (?, ?, '', ?, ?)
            ON CONFLICT(owner_key, session_id) DO NOTHING
            """,
            (state.owner_key, state.session_id, timestamp, timestamp),
        )
        connection.execute(
            """
            UPDATE conversations
            SET context_state_json = ?, context_state_version = ?,
                context_state_updated_at = ?
            WHERE owner_key = ? AND session_id = ?
            """,
            (
                json.dumps(state_to_dict(state), ensure_ascii=False),
                state.schema_version,
                timestamp,
                state.owner_key,
                state.session_id,
            ),
        )


def _seed(db_path: Path) -> None:
    initialize_context_db(db_path)
    conversations = ConversationService(db_path=db_path)

    conversations.append_turn(
        owner_key="o1",
        session_id="aligned",
        user_message="u1",
        assistant_answer="a1",
    )
    state = AgentContextState(owner_key="o1", session_id="aligned")
    set_intent(state, current_question="u1", current_goal="u1")
    append_recent_turns(state, [{"role": "user", "text": "u1"}], last_turn_index=1)
    state.patch_tail.append({"op": "x"})
    _write_legacy_projection(db_path, state)

    # snapshot-only ahead
    ahead = AgentContextState(owner_key="o2", session_id="ahead")
    append_recent_turns(ahead, [{"role": "user", "text": "ghost"}], last_turn_index=9)
    _write_legacy_projection(db_path, ahead)

    # turn-only
    conversations.append_turn(
        owner_key="o3",
        session_id="turnonly",
        user_message="only",
        assistant_answer="ans",
    )

    # snapshot+turn behind: snapshot covers turn 0, turn 1 must be reduced.
    conversations.append_turn(
        owner_key="o4",
        session_id="behind",
        user_message="old question",
        assistant_answer="old answer",
    )
    behind = AgentContextState(owner_key="o4", session_id="behind")
    set_intent(behind, current_question="old question", current_goal="root goal")
    append_recent_turns(behind, [], last_turn_index=1)
    _write_legacy_projection(db_path, behind)
    conversations.append_turn(
        owner_key="o4",
        session_id="behind",
        user_message="new question",
        assistant_answer="new answer",
    )

    # snapshot+turn ahead: unsupported snapshot state must be rebuilt from turns.
    conversations.append_turn(
        owner_key="o5",
        session_id="turn-ahead",
        user_message="canonical",
        assistant_answer="canonical answer",
    )
    turn_ahead = AgentContextState(owner_key="o5", session_id="turn-ahead")
    set_intent(turn_ahead, current_question="ghost", current_goal="ghost")
    append_recent_turns(turn_ahead, [], last_turn_index=9)
    _write_legacy_projection(db_path, turn_ahead)


def test_dry_run_classifies_without_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    _seed(db_path)
    before = db_path.read_bytes()
    report = dry_run(db_path)
    assert db_path.read_bytes() == before
    assert report["mutated"] is False
    classes = report["classes"]
    assert classes["snapshot_turn_aligned"] == 1
    assert classes["snapshot_only_ahead"] == 1
    assert classes["turn_only"] == 1
    assert classes["snapshot_turn_behind"] == 1
    assert classes["snapshot_turn_ahead"] == 1


def test_apply_audit_first_policy(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    backup_dir = tmp_path / "backups"
    _seed(db_path)
    create_verified_backup(db_path, backup_dir)

    result = apply(db_path, backup_dir=backup_dir)
    assert result["status"] == "ok"
    assert result["cleared_untrusted_snapshots"] == 1
    assert result["compacted"] == 1
    assert result["rebuilt_from_turns"] == 3

    with sqlite3.connect(db_path) as connection:
        ahead = connection.execute(
            "SELECT context_state_json, context_projection_status, context_last_applied_turn_index "
            "FROM conversations WHERE session_id='ahead'"
        ).fetchone()
        aligned = connection.execute(
            "SELECT context_state_json, context_last_applied_turn_index "
            "FROM conversations WHERE session_id='aligned'"
        ).fetchone()
        turnonly = connection.execute(
            "SELECT context_state_json, context_last_applied_turn_index "
            "FROM conversations WHERE session_id='turnonly'"
        ).fetchone()
        turns_ahead = connection.execute(
            "SELECT COUNT(*) FROM conversation_turns WHERE session_id='ahead'"
        ).fetchone()[0]
        behind = connection.execute(
            "SELECT context_state_json, context_last_applied_turn_index "
            "FROM conversations WHERE session_id='behind'"
        ).fetchone()
        turn_ahead = connection.execute(
            "SELECT context_state_json, context_last_applied_turn_index "
            "FROM conversations WHERE session_id='turn-ahead'"
        ).fetchone()

    assert turns_ahead == 0  # never fabricated
    assert ahead[0] is None
    assert ahead[1] == "missing"
    assert ahead[2] == -1
    aligned_payload = json.loads(aligned[0])
    assert is_compact_projection(aligned_payload)
    assert aligned[1] == 0
    assert turnonly[0] is not None
    assert turnonly[1] == 0
    behind_payload = json.loads(behind[0])
    assert behind_payload["intent"]["current_question"] == "new question"
    assert behind_payload["output"]["last_answer_summary"] == "new answer"
    assert behind[1] == 1
    turn_ahead_payload = json.loads(turn_ahead[0])
    assert turn_ahead_payload["intent"]["current_question"] == "canonical"
    assert turn_ahead_payload["intent"]["current_goal"] == "canonical"
    assert turn_ahead[1] == 0


def test_apply_requires_schema_v3_without_mutating_v2_fixture(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    backup_dir = tmp_path / "backups"
    _seed(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
    create_verified_backup(db_path, backup_dir)
    before = db_path.read_bytes()

    try:
        apply(db_path, backup_dir=backup_dir)
    except RuntimeError as exc:
        assert "database schema is not current" in str(exc)
    else:  # pragma: no cover - safety assertion
        raise AssertionError("schema v2 apply must be blocked")
    assert db_path.read_bytes() == before


def test_apply_rejects_corrupt_stale_and_count_mismatch_backups(tmp_path: Path) -> None:
    corrupt_db = tmp_path / "corrupt.db"
    _seed(corrupt_db)
    corrupt_backups = tmp_path / "corrupt-backups"
    corrupt_backups.mkdir()
    (corrupt_backups / corrupt_db.name).write_bytes(b"not sqlite")
    try:
        apply(corrupt_db, backup_dir=corrupt_backups)
    except RuntimeError as exc:
        assert "healthy SQLite" in str(exc)
    else:  # pragma: no cover - safety assertion
        raise AssertionError("corrupt backup must be blocked")

    stale_db = tmp_path / "stale.db"
    _seed(stale_db)
    stale_backups = tmp_path / "stale-backups"
    stale_backup = create_verified_backup(stale_db, stale_backups)
    newer = stale_backup.stat().st_mtime_ns + 1_000_000
    os.utime(stale_db, ns=(newer, newer))
    try:
        apply(stale_db, backup_dir=stale_backups)
    except RuntimeError as exc:
        assert "older than source" in str(exc)
    else:  # pragma: no cover - safety assertion
        raise AssertionError("stale backup must be blocked")

    mismatch_db = tmp_path / "mismatch.db"
    _seed(mismatch_db)
    mismatch_backups = tmp_path / "mismatch-backups"
    mismatch_backup = create_verified_backup(mismatch_db, mismatch_backups)
    ConversationService(db_path=mismatch_db).append_turn(
        owner_key="extra",
        session_id="extra",
        user_message="new",
        assistant_answer="new",
    )
    newest = mismatch_db.stat().st_mtime_ns + 1_000_000
    os.utime(mismatch_backup, ns=(newest, newest))
    try:
        apply(mismatch_db, backup_dir=mismatch_backups)
    except RuntimeError as exc:
        assert "row counts do not match" in str(exc)
    else:  # pragma: no cover - safety assertion
        raise AssertionError("count-mismatched backup must be blocked")
