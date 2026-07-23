"""Focused tests for unified ContextRepository."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.agent.context.envelope import CompletedTurnCommit
from app.agent.context.operations import set_intent
from app.agent.context.projection import is_compact_projection, projection_to_dict
from app.agent.context.state import AgentContextState
from app.services.context_repository import ContextRepository, ContextRepositorySettings
from tests._context_db import initialize_context_db


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ops: list[str] = []

    async def __call__(self, op: str, key: str, value, ttl):
        self.ops.append(op)
        if op == "get":
            return self.store.get(key)
        if op == "set":
            self.store[key] = value
            return True
        if op == "delete":
            self.store.pop(key, None)
            return 1
        raise ValueError(op)


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[ContextRepository, FakeRedis]:
    redis = FakeRedis()
    db_path = initialize_context_db(tmp_path / "memory.db")
    repository = ContextRepository(
        db_path=db_path,
        redis_get_set=redis,
        settings=ContextRepositorySettings(
            redis_enabled=True,
            redis_namespace="testns",
            redis_ttl_seconds=60,
            db_snapshot_enabled=True,
            history_max_turns=6,
        ),
    )
    return repository, redis


@pytest.mark.asyncio
async def test_commit_is_atomic_and_idempotent(repo) -> None:
    repository, redis = repo
    state = AgentContextState(owner_key="o", session_id="s")
    set_intent(state, current_question="q1", current_goal="g1")
    commit = CompletedTurnCommit(
        owner_key="o",
        session_id="s",
        commit_id="trace-1",
        user_message="hello",
        assistant_answer="world",
        projection=state,
        events=[{"type": "agent_event", "stage": "complete"}],
    )
    first = await repository.commit_completed_turn(commit)
    second = await repository.commit_completed_turn(commit)
    assert first.committed and second.committed
    assert first.turn_index == 0
    assert second.idempotent_replay is True
    assert second.turn_id == first.turn_id
    with sqlite3.connect(repository.db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0]
        row = connection.execute(
            "SELECT context_state_json, context_projection_version, "
            "context_last_applied_turn_index, context_projection_status "
            "FROM conversations WHERE owner_key='o' AND session_id='s'"
        ).fetchone()
    assert count == 1
    assert row[1] == 1
    assert row[2] == 0
    assert row[3] == "ready"
    payload = json.loads(row[0])
    assert is_compact_projection(payload)
    assert redis.store  # post-commit cache written


@pytest.mark.asyncio
async def test_projection_disabled_commits_turn_without_watermark_advance(tmp_path: Path) -> None:
    redis = FakeRedis()
    db_path = initialize_context_db(tmp_path / "memory.db")
    repository = ContextRepository(
        db_path=db_path,
        redis_get_set=redis,
        settings=ContextRepositorySettings(db_snapshot_enabled=False, redis_enabled=True),
    )
    result = await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="o",
            session_id="s",
            commit_id="t1",
            user_message="u",
            assistant_answer="a",
        )
    )
    assert result.committed is True
    assert result.projection_status == "disabled"
    assert result.projection_version is None
    with sqlite3.connect(repository.db_path) as connection:
        row = connection.execute(
            "SELECT context_last_applied_turn_index, context_projection_status, context_state_json "
            "FROM conversations WHERE owner_key='o' AND session_id='s'"
        ).fetchone()
        turns = connection.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0]
    assert turns == 1
    assert row[0] == -1
    assert row[1] == "disabled"
    assert row[2] is None


@pytest.mark.asyncio
async def test_load_envelope_uses_turns_and_rejects_stale_cache(repo) -> None:
    repository, redis = repo
    state = AgentContextState(owner_key="o", session_id="s")
    await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="o",
            session_id="s",
            commit_id="c1",
            user_message="first",
            assistant_answer="A1",
            projection=state,
        )
    )
    envelope = await repository.load_envelope("o", "s")
    assert envelope.watermark.last_applied_turn_index == 0
    assert envelope.turn_window
    assert envelope.turn_window[0]["user_message"] == "first"
    assert envelope.source in {"sqlite_projection", "redis_committed"}

    # Poison cache with wrong version; load must still prefer SQLite watermark match.
    key = "testns:context:committed:o:s"
    redis.store[key] = json.dumps(
        {
            "owner_key": "o",
            "session_id": "s",
            "projection_version": 999,
            "projection": projection_to_dict(state),
        }
    )
    envelope2 = await repository.load_envelope("o", "s")
    assert "redis_cache_stale" in envelope2.warnings
    assert envelope2.watermark.projection_version == 1


@pytest.mark.asyncio
async def test_scope_isolation(repo) -> None:
    repository, _ = repo
    await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="owner-a",
            session_id="s1",
            commit_id="a1",
            user_message="a",
            assistant_answer="aa",
            projection=AgentContextState(owner_key="owner-a", session_id="s1"),
        )
    )
    env_b = await repository.load_envelope("owner-b", "s1")
    assert env_b.turn_window == []
    assert env_b.watermark.latest_turn_index == -1


@pytest.mark.asyncio
async def test_redis_down_does_not_break_sqlite_commit(tmp_path: Path) -> None:
    async def boom(op, key, value, ttl):
        raise TimeoutError("redis down")

    db_path = initialize_context_db(tmp_path / "memory.db")
    repository = ContextRepository(
        db_path=db_path,
        redis_get_set=boom,
        settings=ContextRepositorySettings(redis_enabled=True, db_snapshot_enabled=True),
    )
    result = await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="o",
            session_id="s",
            commit_id="x",
            user_message="u",
            assistant_answer="a",
            projection=AgentContextState(owner_key="o", session_id="s"),
        )
    )
    assert result.committed is True
    assert any("redis_write_failed" in w for w in result.warnings)
    env = await repository.load_envelope("o", "s")
    assert env.watermark.last_applied_turn_index == 0


@pytest.mark.asyncio
async def test_projection_none_preserves_existing_blocks_and_reduces_new_turn(repo) -> None:
    repository, _ = repo
    state = AgentContextState(owner_key="o", session_id="preserve")
    set_intent(state, current_question="first", current_goal="preserve-me")
    await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="o",
            session_id="preserve",
            commit_id="first",
            user_message="first",
            assistant_answer="answer one",
            projection=state,
        )
    )

    result = await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="o",
            session_id="preserve",
            commit_id="clarify",
            user_message="please clarify",
            assistant_answer="clarifying question",
            projection=None,
        )
    )

    assert result.projection_status == "ready"
    with sqlite3.connect(repository.db_path) as connection:
        row = connection.execute(
            "SELECT context_state_json, context_last_applied_turn_index "
            "FROM conversations WHERE owner_key='o' AND session_id='preserve'"
        ).fetchone()
    payload = json.loads(row[0])
    assert payload["intent"]["current_goal"] == "preserve-me"
    assert payload["intent"]["current_question"] == "please clarify"
    assert payload["output"]["last_answer_summary"] == "clarifying question"
    assert row[1] == 1


@pytest.mark.asyncio
async def test_load_repairs_behind_projection_in_memory_without_advancing_watermark(repo) -> None:
    repository, _ = repo
    state = AgentContextState(owner_key="o", session_id="behind")
    set_intent(state, current_question="first", current_goal="root goal")
    await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="o",
            session_id="behind",
            commit_id="b1",
            user_message="first",
            assistant_answer="one",
            projection=state,
        )
    )
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            """
            INSERT INTO conversation_turns (
                owner_key, session_id, turn_index, user_message, user_context,
                attachment_refs_json, assistant_answer, route, case_id,
                events_json, created_at, commit_id
            ) VALUES ('o', 'behind', 1, 'second', '', '[]', 'two', '', '', '[]',
                      '2026-07-19T00:00:00Z', 'b2')
            """
        )

    envelope = await repository.load_envelope("o", "behind")
    assert envelope.projection.intent.current_question == "second"
    assert envelope.projection.intent.current_goal == "root goal"
    assert envelope.projection.output.last_answer_summary == "two"
    assert envelope.watermark.latest_turn_index == 1
    assert envelope.watermark.last_applied_turn_index == 0
    assert "reduce_missing_committed_turns" in envelope.repair_actions


@pytest.mark.asyncio
async def test_history_zero_returns_no_window_but_keeps_latest_watermark(tmp_path: Path) -> None:
    db_path = initialize_context_db(tmp_path / "history-zero.db")
    repository = ContextRepository(
        db_path=db_path,
        settings=ContextRepositorySettings(
            redis_enabled=False,
            db_snapshot_enabled=True,
            history_max_turns=0,
        ),
    )
    for index in range(2):
        await repository.commit_completed_turn(
            CompletedTurnCommit(
                owner_key="o",
                session_id="s",
                commit_id=f"h{index}",
                user_message=f"q{index}",
                assistant_answer=f"a{index}",
                projection=AgentContextState(owner_key="o", session_id="s"),
            )
        )
    envelope = await repository.load_envelope("o", "s")
    assert envelope.turn_window == []
    assert envelope.watermark.latest_turn_index == 1


@pytest.mark.asyncio
async def test_cache_scope_and_watermark_must_both_match(repo) -> None:
    repository, redis = repo
    result = await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="o",
            session_id="cache",
            commit_id="cache-1",
            user_message="q",
            assistant_answer="a",
            projection=AgentContextState(owner_key="o", session_id="cache"),
        )
    )
    key = "testns:context:committed:o:cache"
    payload = json.loads(redis.store[key])
    payload["owner_key"] = "other-owner"
    redis.store[key] = json.dumps(payload)
    envelope = await repository.load_envelope("o", "cache")
    assert "redis_cache_scope_mismatch" in envelope.warnings
    assert envelope.source == "sqlite_projection"

    payload["owner_key"] = "o"
    payload["last_applied_turn_id"] = int(result.turn_id or 0) + 1
    redis.store[key] = json.dumps(payload)
    envelope = await repository.load_envelope("o", "cache")
    assert "redis_cache_stale" in envelope.warnings


@pytest.mark.asyncio
async def test_inflight_is_coalesced_validated_and_deleted(repo) -> None:
    repository, redis = repo
    state = AgentContextState(owner_key="o", session_id="inflight")
    set_intent(state, current_question="q", current_goal="g")
    await repository.save_inflight(
        owner_key="o",
        session_id="inflight",
        run_id="run-1",
        state=state,
        base_projection_version=3,
    )
    await repository.save_inflight(
        owner_key="o",
        session_id="inflight",
        run_id="run-1",
        state=state,
        base_projection_version=3,
    )
    assert redis.ops.count("set") == 1

    loaded, warnings = await repository.load_inflight(
        owner_key="o",
        session_id="inflight",
        run_id="run-1",
        expected_base_projection_version=3,
        expected_state_version=state.version,
    )
    assert warnings == []
    assert loaded is not None and loaded.intent.current_goal == "g"
    rejected, warnings = await repository.load_inflight(
        owner_key="o",
        session_id="inflight",
        run_id="run-1",
        expected_base_projection_version=4,
    )
    assert rejected is None
    assert warnings == ["inflight_base_version_mismatch"]
    await repository.delete_inflight(owner_key="o", session_id="inflight", run_id="run-1")
    assert not redis.store


@pytest.mark.asyncio
async def test_v2_schema_load_is_blocked_without_mutating_file(tmp_path: Path) -> None:
    db_path = initialize_context_db(tmp_path / "schema-v2.db")
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    repository = ContextRepository(
        db_path=db_path,
        settings=ContextRepositorySettings(redis_enabled=False),
    )
    with pytest.raises(RuntimeError, match="database schema is not current"):
        await repository.load_envelope("o", "s")
    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert after == before


@pytest.mark.asyncio
async def test_commit_serialization_failure_rolls_back_entire_transaction(repo) -> None:
    repository, _ = repo
    with pytest.raises(TypeError):
        await repository.commit_completed_turn(
            CompletedTurnCommit(
                owner_key="o",
                session_id="rollback",
                commit_id="bad-json",
                user_message="q",
                assistant_answer="a",
                events=[{"payload": object()}],
                projection=AgentContextState(owner_key="o", session_id="rollback"),
            )
        )
    with sqlite3.connect(repository.db_path) as connection:
        turns = connection.execute(
            "SELECT COUNT(*) FROM conversation_turns WHERE session_id='rollback'"
        ).fetchone()[0]
        conversation = connection.execute(
            "SELECT COUNT(*) FROM conversations WHERE session_id='rollback'"
        ).fetchone()[0]
    assert turns == 0
    assert conversation == 0


def test_concurrent_commits_get_unique_scoped_turn_indexes(tmp_path: Path) -> None:
    db_path = initialize_context_db(tmp_path / "concurrent.db")

    def commit(index: int):
        repository = ContextRepository(
            db_path=db_path,
            settings=ContextRepositorySettings(
                redis_enabled=False,
                db_snapshot_enabled=True,
            ),
        )
        return asyncio.run(
            repository.commit_completed_turn(
                CompletedTurnCommit(
                    owner_key="o",
                    session_id="s",
                    commit_id=f"parallel-{index}",
                    user_message=f"q{index}",
                    assistant_answer=f"a{index}",
                    projection=AgentContextState(owner_key="o", session_id="s"),
                )
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(commit, range(2)))
    assert sorted(result.turn_index for result in results) == [0, 1]
    with sqlite3.connect(db_path) as connection:
        indexes = [
            row[0]
            for row in connection.execute(
                "SELECT turn_index FROM conversation_turns "
                "WHERE owner_key='o' AND session_id='s' ORDER BY turn_index"
            ).fetchall()
        ]
    assert indexes == [0, 1]
