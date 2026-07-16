"""Tests for ``app.agent.context.integration``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agent.context.integration import (
    build_default_store,
    build_stateful_context,
    capture_tool_outcome,
    persist_stateful_context,
    stamp_recent_turns,
    stateful_context_enabled,
)
from app.agent.context.operations import framework_patch
from app.agent.context.state import (
    SECTION_INTENT,
    AgentContextState,
)
from app.agent.context.store import ContextStateStore
from app.services.context_snapshot_service import ContextSnapshotService


class _InMemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def __call__(self, op: str, key: str, value: Any, ttl: Any) -> Any:
        if op == "get":
            return self.data.get(key)
        if op == "set":
            self.data[key] = str(value)
            return True
        raise ValueError(op)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "integration.db"


@pytest.fixture()
def store_with_db(db_path: Path) -> ContextStateStore:
    snap = ContextSnapshotService(db_path=db_path)
    redis = _InMemoryRedis()
    return build_default_store(snapshot_service=snap, redis_get_set=redis)


async def test_stateful_context_enabled_default_on() -> None:
    # The stateful whiteboard is now the default Harness context path; the
    # legacy ContextBuilder path remains available via env rollback.
    assert stateful_context_enabled() is True


async def test_build_stateful_context_initializes_intent(
    store_with_db: ContextStateStore,
) -> None:
    ctx = await build_stateful_context(
        owner_key="u",
        session_id="s",
        current_question="redis ok?",
        current_goal="ping redis",
        store=store_with_db,
    )
    assert ctx.state.intent.current_question == "redis ok?"
    assert ctx.state.intent.current_goal == "ping redis"
    assert "current_goal" in ctx.view


async def test_persist_stateful_context_writes_redis_and_db(
    store_with_db: ContextStateStore,
) -> None:
    ctx = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q", current_goal="g",
        store=store_with_db,
    )
    warnings = await persist_stateful_context(ctx.state, store=store_with_db)
    assert warnings == []
    # Reload should hit Redis (source == "redis").
    again = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q2", current_goal="g2",
        store=store_with_db,
    )
    assert again.source == "redis"
    assert again.state.intent.current_question == "q2"  # newer patch wins


async def test_redis_miss_falls_back_to_db_snapshot(
    db_path: Path,
) -> None:
    snap = ContextSnapshotService(db_path=db_path)
    redis_a = _InMemoryRedis()
    redis_b = _InMemoryRedis()

    store_a = build_default_store(snapshot_service=snap, redis_get_set=redis_a)
    ctx = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q", current_goal="g",
        store=store_a,
    )
    await persist_stateful_context(ctx.state, store=store_a)
    assert redis_a.data

    # New Redis client + new store → first call must rebuild from snapshot.
    store_b = build_default_store(snapshot_service=snap, redis_get_set=redis_b)
    again = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q", current_goal="g",
        store=store_b,
    )
    assert again.source == "db_snapshot"
    # Snapshot Redis was warmed during the miss recovery.
    assert redis_b.data


async def test_capture_tool_outcome_records_evidence(
    store_with_db: ContextStateStore,
) -> None:
    ctx = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q", current_goal="g",
        store=store_with_db,
    )
    capture_tool_outcome(
        ctx.state,
        tool_name="check_redis_health",
        raw_result={"ok": True, "summary": "ping ok", "latency_ms": 12,
                    "fact": "Redis PING 成功"},
        raw_ref="timeline:1",
    )
    assert len(ctx.state.evidence.observed_facts) == 1
    assert ctx.state.evidence.tool_summaries[0].tool == "check_redis_health"
    assert ctx.state.tool.last_latency_ms == 12
    assert ctx.state.tool.last_status == "success"


async def test_capture_tool_outcome_handles_non_dict_result(
    store_with_db: ContextStateStore,
) -> None:
    ctx = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q", current_goal="g",
        store=store_with_db,
    )
    capture_tool_outcome(
        ctx.state,
        tool_name="t",
        raw_result="some text result",
        raw_ref="r",
    )
    assert ctx.state.evidence.tool_summaries[0].note == "some text result"


async def test_stamp_recent_turns_replaces_snapshot(
    store_with_db: ContextStateStore,
) -> None:
    ctx = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q", current_goal="g",
        store=store_with_db,
    )
    stamp_recent_turns(
        ctx.state,
        turns=[{"role": "user", "text": "hi"}, {"role": "assistant", "text": "ok"}],
        last_turn_index=2,
    )
    assert ctx.state.conversation.recent_turns[0]["text"] == "hi"
    assert ctx.state.conversation.last_turn_index == 2


async def test_state_version_increments_per_patch(
    store_with_db: ContextStateStore,
) -> None:
    ctx = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q", current_goal="g",
        store=store_with_db,
    )
    v0 = ctx.state.version
    framework_patch(ctx.state, SECTION_INTENT, "current_goal", op="set", value="g2")
    assert ctx.state.version == v0 + 1


async def test_redis_disabled_skips_redis_layer(
    db_path: Path,
) -> None:
    """With redis_enabled=False, store should still work via DB snapshot."""
    from app.agent.context.integration import build_store_settings
    from app.agent.context.store import ContextStateStore

    snap = ContextSnapshotService(db_path=db_path)
    redis = _InMemoryRedis()  # not used, but kept for the wiring call

    settings = build_store_settings()
    settings.redis_enabled = False

    store = ContextStateStore(
        db_snapshot_get=snap.get_latest_context_snapshot,
        db_snapshot_save=snap.save_context_snapshot,
        redis_get_set=redis,
        settings=settings,
    )
    ctx = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q", current_goal="g",
        store=store,
    )
    # First call → no Redis, no snapshot → fresh.
    assert ctx.source == "fresh"
    await persist_stateful_context(ctx.state, store=store)
    again = await build_stateful_context(
        owner_key="u", session_id="s",
        current_question="q", current_goal="g",
        store=store,
    )
    assert again.source == "db_snapshot"
