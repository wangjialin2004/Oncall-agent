"""Tests for scripts/audit_context_storage.py (read-only guarantees)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from app.agent.context.operations import append_recent_turns, set_intent
from app.agent.context.persistence import state_to_dict
from app.agent.context.state import AgentContextState
from app.services.conversation_service import ConversationService
from scripts import audit_context_storage as auditor
from tests._context_db import initialize_context_db


def _fingerprint(path: Path) -> tuple[int, int, str]:
    data = path.read_bytes()
    return path.stat().st_mtime_ns, path.stat().st_size, hashlib.sha256(data).hexdigest()


def _write_legacy_projection(db_path: Path, state: AgentContextState) -> None:
    """Seed pre-repository data for audit compatibility tests only."""
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


def _seed_db(db_path: Path) -> None:
    initialize_context_db(db_path)
    conversations = ConversationService(db_path=db_path)

    conversations.append_turn(
        owner_key="owner-a",
        session_id="sess-aligned",
        user_message="hello",
        assistant_answer="world",
        route="harness",
        case_id="c1",
        events=[{"type": "agent_event", "stage": "complete"}],
    )
    state = AgentContextState(owner_key="owner-a", session_id="sess-aligned")
    set_intent(state, current_question="hello", current_goal="hello")
    append_recent_turns(
        state,
        [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "world"}],
        last_turn_index=1,
    )
    _write_legacy_projection(db_path, state)

    # Snapshot-only ahead session (no turns).
    ahead = AgentContextState(owner_key="owner-b", session_id="sess-ahead")
    set_intent(ahead, current_question="ghost", current_goal="ghost")
    append_recent_turns(ahead, [{"role": "user", "text": "ghost"}], last_turn_index=5)
    _write_legacy_projection(db_path, ahead)

    # Turn-only session.
    conversations.append_turn(
        owner_key="owner-c",
        session_id="sess-turn-only",
        user_message="only turn",
        assistant_answer="ok",
    )


def test_audit_sqlite_is_read_only_and_aggregate(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    _seed_db(db_path)
    before = _fingerprint(db_path)

    report = auditor.audit_sqlite(db_path)

    after = _fingerprint(db_path)
    assert after == before
    assert report["mutated"] is False
    assert report["status"] in {"ok", "degraded"}
    assert report["totals"]["conversations"] >= 3
    assert report["totals"]["conversation_turns"] == 2
    assert report["totals"]["structured_snapshots"] == 2
    classes = report["migration_classes"]
    assert classes["snapshot_turn_aligned"] == 1
    assert classes["snapshot_only_ahead"] == 1
    assert classes["turn_only"] == 1
    # Aggregate only — no owner/session/text leakage in serialized report.
    serialized = json.dumps(report, ensure_ascii=False)
    assert "owner-a" not in serialized
    assert "sess-aligned" not in serialized
    assert "hello" not in serialized
    assert "ghost" not in serialized


def test_audit_sqlite_reports_corrupt_json_without_mutating(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    _seed_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE conversations SET context_state_json = ? "
            "WHERE owner_key = ? AND session_id = ?",
            ("not-json", "owner-a", "sess-aligned"),
        )
        connection.commit()
    before = _fingerprint(db_path)

    report = auditor.audit_sqlite(db_path)

    assert _fingerprint(db_path) == before
    assert report["integrity"]["snapshot_json_corrupt"] >= 1
    assert report["status"] in {"degraded", "ok", "blocked"}


def test_run_audit_skips_redis_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    _seed_db(db_path)
    report = auditor.run_audit(db_path=db_path, include_redis=False)
    assert report["redis"]["status"] == "skipped"
    assert report["mutated"] is False


def test_audit_redis_blocked_on_ping_failure(monkeypatch) -> None:
    class _Boom:
        async def ping(self):
            raise TimeoutError("slow")

        async def aclose(self):
            return None

    class _Redis:
        @staticmethod
        def from_url(*_args, **_kwargs):
            return _Boom()

    monkeypatch.setitem(
        __import__("sys").modules,
        "redis.asyncio",
        type("M", (), {"Redis": _Redis})(),
    )

    import asyncio

    report = asyncio.run(
        auditor.audit_redis(
            enabled=True,
            namespace="super_biz_agent",
            url="redis://localhost:6379/0",
            socket_timeout=0.2,
        )
    )
    assert report["status"] == "blocked"
    assert report["ping_ok"] is False
    assert report["mutated"] is False
    assert any("ping_failed" in w for w in report["warnings"])
