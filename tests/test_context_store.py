"""Tests for ``app.agent.context.store``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agent.context.persistence import state_from_dict, state_to_dict
from app.agent.context.state import (
    SCHEMA_VERSION,
    AgentContextState,
)
from app.agent.context.store import (
    ContextStateStore,
    ContextStateStoreSettings,
    _build_redis_key,
)
from app.services.context_snapshot_service import ContextSnapshotService


def _failing_get(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("snapshot_get should not be called when Redis hits")


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "store.db"


@pytest.fixture()
def snapshot_service(db_path: Path) -> ContextSnapshotService:
    return ContextSnapshotService(db_path=db_path)


def _in_memory_redis() -> Any:
    """A tiny in-memory backend matching the (op, key, value, ttl) contract."""

    class _Redis:
        def __init__(self) -> None:
            self.data: dict[str, str] = {}

        async def __call__(self, op: str, key: str, value: Any, ttl: Any) -> Any:
            if op == "get":
                return self.data.get(key)
            if op == "set":
                self.data[key] = str(value)
                return True
            raise ValueError(op)

    return _Redis()


def _seeded() -> AgentContextState:
    state = AgentContextState(owner_key="u", session_id="s")
    state.intent.current_goal = "ping redis"
    state.version = 7
    return state


async def test_redis_hit_skips_snapshot_and_rebuild() -> None:
    backend = _in_memory_redis()
    # Prime Redis with a valid state.
    seeded = _seeded()
    backend.data[_build_redis_key("super_biz_agent", "u", "s")] = json.dumps(
        state_to_dict(seeded), ensure_ascii=False,
    )
    store = ContextStateStore(
        db_snapshot_get=_failing_get,
        db_snapshot_save=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("save should not be called on read"),
        ),
        redis_get_set=backend,
    )
    result = await store.get_or_rebuild("u", "s")
    assert result.source == "redis"
    assert result.state.intent.current_goal == "ping redis"
    assert result.state.version == 7


async def test_redis_miss_falls_back_to_db_snapshot(db_path: Path) -> None:
    snapshot_service = ContextSnapshotService(db_path=db_path)
    # Seed DB snapshot.
    snapshot_service.save_context_snapshot(
        owner_key="u", session_id="s", state=_seeded(),
    )
    backend = _in_memory_redis()  # empty
    saves: list[tuple[str, str, AgentContextState]] = []

    async def db_save(owner_key: str, session_id: str, state: AgentContextState) -> None:
        saves.append((owner_key, session_id, state))

    store = ContextStateStore(
        db_snapshot_get=snapshot_service.get_latest_context_snapshot,
        db_snapshot_save=db_save,
        redis_get_set=backend,
    )
    result = await store.get_or_rebuild("u", "s")
    assert result.source == "db_snapshot"
    assert result.state.intent.current_goal == "ping redis"
    # Redis should be warmed.
    assert _build_redis_key("super_biz_agent", "u", "s") in backend.data


async def test_redis_and_db_miss_invokes_rebuild() -> None:
    backend = _in_memory_redis()
    rebuild_calls: list[tuple[str, str]] = []

    async def fake_rebuild(owner_key: str, session_id: str) -> AgentContextState:
        rebuild_calls.append((owner_key, session_id))
        rebuilt = AgentContextState(owner_key=owner_key, session_id=session_id)
        rebuilt.intent.current_goal = "rebuilt"
        rebuilt.version = 99
        return rebuilt

    snapshot_saves: list[tuple[str, str]] = []

    async def fake_snapshot_get(owner_key: str, session_id: str) -> AgentContextState | None:
        return None

    async def fake_snapshot_save(owner_key: str, session_id: str, state: AgentContextState) -> None:
        snapshot_saves.append((owner_key, session_id))

    store = ContextStateStore(
        db_snapshot_get=fake_snapshot_get,
        db_snapshot_save=fake_snapshot_save,
        redis_get_set=backend,
    )
    result = await store.get_or_rebuild("u", "s", rebuild=fake_rebuild)
    assert result.source == "rebuilt"
    assert result.state.intent.current_goal == "rebuilt"
    assert rebuild_calls == [("u", "s")]
    assert snapshot_saves == [("u", "s")]


async def test_no_redis_no_db_no_rebuild_returns_fresh() -> None:
    store = ContextStateStore(
        db_snapshot_get=lambda *a, **k: (_ for _ in ()).throw(AssertionError),
        db_snapshot_save=lambda *a, **k: (_ for _ in ()).throw(AssertionError),
        redis_get_set=None,
    )
    result = await store.get_or_rebuild("u", "s")
    assert result.source == "fresh"
    assert result.state.owner_key == "u"
    assert result.state.session_id == "s"


async def test_redis_read_failure_falls_back_to_db(db_path: Path) -> None:
    snapshot_service = ContextSnapshotService(db_path=db_path)
    snapshot_service.save_context_snapshot(
        owner_key="u", session_id="s", state=_seeded(),
    )

    class FailingRedis:
        async def __call__(self, op: str, key: str, value: Any, ttl: Any) -> Any:
            raise ConnectionError("redis down")

    store = ContextStateStore(
        db_snapshot_get=snapshot_service.get_latest_context_snapshot,
        db_snapshot_save=snapshot_service.save_context_snapshot,
        redis_get_set=FailingRedis(),
    )
    result = await store.get_or_rebuild("u", "s")
    assert result.source == "db_snapshot"
    # Warning recorded but main path still succeeded.
    assert any("redis read failed" in w for w in result.warnings)


async def test_redis_write_failure_does_not_block_main_path() -> None:
    class FailingRedis:
        async def __call__(self, op: str, key: str, value: Any, ttl: Any) -> Any:
            if op == "get":
                return None
            raise ConnectionError(f"redis {op} down")

    store = ContextStateStore(
        db_snapshot_get=lambda *a, **k: (_ for _ in ()).throw(AssertionError),
        db_snapshot_save=lambda *a, **k: None,
        redis_get_set=FailingRedis(),
        settings=ContextStateStoreSettings(
            redis_enabled=True, db_snapshot_enabled=False,
        ),
    )
    result = await store.get_or_rebuild("u", "s")
    assert result.source == "fresh"
    assert any("redis write failed" in w for w in result.warnings)


async def test_corrupt_redis_payload_falls_back_to_db(db_path: Path) -> None:
    snapshot_service = ContextSnapshotService(db_path=db_path)
    snapshot_service.save_context_snapshot(
        owner_key="u", session_id="s", state=_seeded(),
    )
    backend = _in_memory_redis()
    backend.data[_build_redis_key("super_biz_agent", "u", "s")] = "{ not json"

    store = ContextStateStore(
        db_snapshot_get=snapshot_service.get_latest_context_snapshot,
        db_snapshot_save=snapshot_service.save_context_snapshot,
        redis_get_set=backend,
    )
    result = await store.get_or_rebuild("u", "s")
    assert result.source == "db_snapshot"


async def test_schema_mismatch_redis_payload_treated_as_miss(db_path: Path) -> None:
    snapshot_service = ContextSnapshotService(db_path=db_path)
    snapshot_service.save_context_snapshot(
        owner_key="u", session_id="s", state=_seeded(),
    )
    backend = _in_memory_redis()
    bogus = state_to_dict(_seeded())
    bogus["schema_version"] = SCHEMA_VERSION + 99
    backend.data[_build_redis_key("super_biz_agent", "u", "s")] = json.dumps(bogus)

    store = ContextStateStore(
        db_snapshot_get=snapshot_service.get_latest_context_snapshot,
        db_snapshot_save=snapshot_service.save_context_snapshot,
        redis_get_set=backend,
    )
    result = await store.get_or_rebuild("u", "s")
    assert result.source == "db_snapshot"


async def test_save_writes_redis_and_db(db_path: Path) -> None:
    snapshot_service = ContextSnapshotService(db_path=db_path)
    backend = _in_memory_redis()
    store = ContextStateStore(
        db_snapshot_get=snapshot_service.get_latest_context_snapshot,
        db_snapshot_save=snapshot_service.save_context_snapshot,
        redis_get_set=backend,
    )
    state = _seeded()
    warnings = await store.save("u", "s", state)
    assert warnings == []
    assert _build_redis_key("super_biz_agent", "u", "s") in backend.data
    loaded = snapshot_service.get_latest_context_snapshot("u", "s")
    assert loaded is not None
    assert loaded.intent.current_goal == "ping redis"


async def test_save_returns_warning_when_db_snapshot_disabled() -> None:
    backend = _in_memory_redis()
    store = ContextStateStore(
        db_snapshot_get=lambda *a, **k: (_ for _ in ()).throw(AssertionError),
        db_snapshot_save=lambda *a, **k: (_ for _ in ()).throw(AssertionError),
        redis_get_set=backend,
        settings=ContextStateStoreSettings(
            redis_enabled=True, db_snapshot_enabled=False,
        ),
    )
    warnings = await store.save("u", "s", _seeded())
    assert warnings == []


async def test_build_redis_key_format() -> None:
    key = _build_redis_key("ns", "u", "s")
    assert key == "ns:context:u:s"
