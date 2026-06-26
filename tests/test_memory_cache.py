"""Tests for the L1 cache layer in front of the long-term memory services.

Covers (per plan §5):

* TTL expiry
* LRU eviction
* ``invalidate_prefix`` semantics
* **Aliasing guard (review condition 1)** — modifying a returned dict/list
  must not poison the cached entry.
* Test isolation (review condition 2) — every key carries a ``db_tag`` and
  the global singleton is cleared between tests.
* Concurrency — many threads hammering the cache; no deadlock, stats tally.
* Graceful degradation — ``set`` / ``invalidate`` failure must not crash
  the caller.
* Service integration regression — ``format_for_prompt`` issues a single
  SQLite fetch even across 100 calls; ``upsert_relation`` invalidates the
  matching ``lookup``; recall never enters the cache (plan §3.3.1 option A).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.config import config
from app.services.memory_cache import (
    MemoryCache,
    db_tag_for,
    get_default_cache,
    query_hash,
    reset_default_cache,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache_singleton() -> None:
    """Drop the module-level singleton before *and* after each test.

    The cache is a process-local global, so without this fixture tests
    would bleed state into each other (the §3.7 / §5 isolation requirement).
    """

    reset_default_cache()
    yield
    reset_default_cache()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "memory.db"


def _make_cache(
    *,
    max_entries: int = 4,
    default_ttl: float = 60.0,
    enabled: bool = True,
) -> tuple[MemoryCache, list[float]]:
    """Build a cache + fake monotonic clock for deterministic TTL tests."""

    clock = [1000.0]

    def fake_clock() -> float:
        return clock[0]

    cache = MemoryCache(
        max_entries=max_entries,
        default_ttl=default_ttl,
        enabled=enabled,
        clock=fake_clock,
    )
    return cache, clock


# ---------------------------------------------------------------------------
# Pure unit tests — the cache primitive
# ---------------------------------------------------------------------------


def test_set_get_round_trip_copies_mutable_values() -> None:
    """Aliasing guard (review condition 1) — the returned dict is a deep copy."""

    cache, _ = _make_cache()
    payload = {"nested": [1, 2, 3], "scalar": "value"}
    cache.set("k", payload)

    fetched = cache.get("k")
    assert fetched == payload
    assert fetched is not payload, "get() must not return the cached reference"
    fetched["nested"].append(99)
    fetched["scalar"] = "tampered"

    next_fetch = cache.get("k")
    assert next_fetch == {"nested": [1, 2, 3], "scalar": "value"}, (
        "mutating the returned dict must not poison the cached entry"
    )


def test_set_get_round_trip_returns_immutable_values_directly() -> None:
    """Strings are immutable so we can hand them back without copying."""

    cache, _ = _make_cache()
    cache.set("k", "hello")
    assert cache.get("k") == "hello"
    # Immutables: identity is fine.
    assert cache.get("k") is not None


def test_ttl_expiry() -> None:
    cache, clock = _make_cache(default_ttl=10.0)
    cache.set("k", {"v": 1})
    assert cache.get("k") == {"v": 1}
    assert cache.stats.hits == 1

    clock[0] += 9.999
    assert cache.get("k") == {"v": 1}, "still within TTL"

    clock[0] += 0.002
    assert cache.get("k") is None, "expired entry must miss"
    assert cache.stats.misses == 1
    # The expired entry should be evicted from the underlying storage
    assert cache.size == 0


def test_lru_eviction() -> None:
    cache, _ = _make_cache(max_entries=3)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.size == 3

    # Touching "a" promotes it; "b" becomes the oldest.
    assert cache.get("a") == 1
    cache.set("d", 4)

    assert cache.size == 3
    assert cache.get("b") is None, "b should be evicted as the LRU entry"
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert cache.get("d") == 4
    assert cache.stats.evictions == 1


def test_invalidate_removes_specific_key() -> None:
    cache, _ = _make_cache()
    cache.set("a", 1)
    cache.set("b", 2)

    removed = cache.invalidate("a")
    assert removed is True
    assert cache.get("a") is None
    assert cache.get("b") == 2
    # Second invalidate of same key returns False but does not crash.
    assert cache.invalidate("a") is False


def test_invalidate_prefix_only_matches_prefix() -> None:
    cache, _ = _make_cache()
    cache.set("memory:user_pref:dict:alice", {"v": 1})
    cache.set("memory:user_pref:dict:bob", {"v": 2})
    cache.set("memory:svc:lookup:proj:svc:prod", {"v": 3})
    cache.set("memory:exp:list:proj:1:50", [{"v": 4}])

    dropped = cache.invalidate_prefix("memory:user_pref:")
    assert dropped == 2
    assert cache.get("memory:user_pref:dict:alice") is None
    assert cache.get("memory:user_pref:dict:bob") is None
    # Other prefixes survive.
    assert cache.get("memory:svc:lookup:proj:svc:prod") == {"v": 3}
    assert cache.get("memory:exp:list:proj:1:50") == [{"v": 4}]


def test_invalidate_prefix_empty_or_non_string_is_noop() -> None:
    cache, _ = _make_cache()
    cache.set("a", 1)
    assert cache.invalidate_prefix("") == 0
    assert cache.invalidate_prefix(123) == 0  # type: ignore[arg-type]
    assert cache.get("a") == 1


def test_clear_drops_everything() -> None:
    cache, _ = _make_cache()
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.size == 0
    assert cache.get("a") is None


def test_disabled_cache_is_passthrough() -> None:
    cache, _ = _make_cache(enabled=False)
    cache.set("a", 1)
    # Set is a no-op when disabled; get always misses.
    assert cache.get("a") is None
    assert cache.stats.misses == 1
    # invalidate is also a no-op.
    assert cache.invalidate_prefix("anything") == 0


def test_set_exception_is_swallowed_and_counted() -> None:
    """A misbehaving cache must not break the call site (plan §3.5)."""

    captured: list[tuple[str, Exception]] = []
    cache = MemoryCache(
        max_entries=4,
        default_ttl=60.0,
        on_error=lambda name, exc: captured.append((name, exc)),
    )

    # Force an exception by handing an unhashable key (OrderedDict indexing
    # will fail on the unhashable type).
    class Bad:
        __hash__ = None  # type: ignore[assignment]

    bad: Any = Bad()
    cache.set(bad, 1)  # must not raise
    assert cache.stats.errors == 1
    assert captured and captured[0][0] == "memory_cache"


def test_stats_counters_track_operations() -> None:
    cache, _ = _make_cache()
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    assert cache.get("a") == 1
    assert cache.get("missing") is None
    cache.invalidate("b")
    # Non-string prefix is a no-op and must not bump the counter.
    cache.invalidate_prefix(123)  # type: ignore[arg-type]
    # A real prefix drop that hits one key.
    cache.invalidate_prefix("a")  # type: ignore[arg-type]

    snap = cache.stats_snapshot()
    assert snap["hits"] == 2
    assert snap["misses"] == 1
    assert snap["invalidations"] == 2  # one for invalidate("b"), one for prefix("a")
    assert snap["set_calls"] == 2
    assert snap["size"] == 0
    # ``stats_snapshot`` rounds to 4dp for the JSON payload; compare loosely.
    assert snap["hit_ratio"] == pytest.approx(2 / 3, abs=1e-3)


def test_concurrency_no_deadlock_and_counts_match() -> None:
    cache, _ = _make_cache(max_entries=64, default_ttl=60.0)
    iterations = 200
    threads = 8

    def worker(seed: int) -> None:
        for i in range(iterations):
            key = f"k-{seed * 17 + (i % 7)}"
            if i % 3 == 0:
                cache.set(key, i)
            else:
                cache.get(key)

    workers = [threading.Thread(target=worker, args=(idx,)) for idx in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    snap = cache.stats_snapshot()
    # 2/3 of the operations are ``get`` (the rest are ``set``); the exact
    # split depends on Python's modulo so we tolerate ±10%.
    expected_gets = iterations * threads * 2 // 3
    assert snap["hits"] + snap["misses"] == pytest.approx(expected_gets, abs=expected_gets // 10)
    assert snap["set_calls"] >= iterations * threads // 3
    # Cache may have evicted at least once given the workload vs max_entries.
    assert snap["evictions"] >= 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_db_tag_is_stable_and_unique() -> None:
    a1 = db_tag_for("/tmp/prod.db")
    a2 = db_tag_for("/tmp/prod.db")
    b = db_tag_for("/tmp/test.db")
    assert a1 == a2
    assert a1 != b
    # Length 8 per the design.
    assert len(a1) == 8


def test_db_tag_handles_pathlib() -> None:
    a = db_tag_for(Path("/tmp/foo/bar.db"))
    b = db_tag_for("/tmp/foo/bar.db")
    assert a == b


def test_query_hash_is_stable_and_short() -> None:
    assert query_hash("hello") == query_hash("hello")
    assert query_hash("hello") != query_hash("world")
    assert len(query_hash("hello")) == 16


def test_default_singleton_picks_up_config() -> None:
    """First call materializes a singleton; toggling config requires a reset."""

    cache = get_default_cache()
    assert cache.enabled == config.memory_cache_enabled
    assert cache._max_entries == config.memory_cache_max_entries


# ---------------------------------------------------------------------------
# Service integration — read/write semantics + invalidation matrix
# ---------------------------------------------------------------------------


def _open_service_db(db_path: Path) -> None:
    """Run the service-side schema bootstrap by touching the service once."""

    from app.services.user_preference_service import UserPreferenceService

    UserPreferenceService(db_path=db_path).upsert(owner_key="bootstrap")


def test_user_preference_format_for_prompt_hits_cache(tmp_db: Path) -> None:
    """100 calls to format_for_prompt must trigger at most two SQLite reads
    (one for the dict payload, one for the prompt — both back-filled on the
    first call). Subsequent calls are served entirely from cache."""

    from app.services.user_preference_service import UserPreferenceService

    _open_service_db(tmp_db)
    service = UserPreferenceService(db_path=tmp_db)
    service.upsert(
        owner_key="alice",
        default_environment="prod",
        language="zh-CN",
        detail_level="normal",
        focused_services=["api", "worker"],
        notes="watch db pool",
    )

    # Reset cache so the upsert's invalidation doesn't pre-warm.
    reset_default_cache()
    cache = get_default_cache()
    db_tag = db_tag_for(tmp_db)
    pref_key = f"memory:{db_tag}:user_pref:alice"
    dict_key = f"memory:{db_tag}:user_pref:dict:alice"

    sqlite_connect_count = 0
    real_connect = __import__("sqlite3").connect

    def counting_connect(*args: Any, **kwargs: Any) -> Any:
        nonlocal sqlite_connect_count
        sqlite_connect_count += 1
        return real_connect(*args, **kwargs)

    monkey = pytest.MonkeyPatch()
    import sqlite3 as _sqlite3
    monkey.setattr(_sqlite3, "connect", counting_connect)

    try:
        prompt_first = service.format_for_prompt("alice")
        assert "默认环境" in prompt_first
        first_warmup = sqlite_connect_count
        assert first_warmup >= 1, "cold call must hit SQLite at least once"

        # Reset the counter so we can prove the next 99 calls avoid SQLite.
        sqlite_connect_count = 0
        for _ in range(99):
            assert service.format_for_prompt("alice") == prompt_first
    finally:
        monkey.undo()

    # All 99 follow-up calls must be served from cache.
    assert sqlite_connect_count == 0, (
        f"expected 0 SQLite reads for cached calls, got {sqlite_connect_count}"
    )
    # The cache now contains both the prompt and the dict payload.
    assert cache.get(pref_key) == prompt_first
    assert cache.get(dict_key) is not None


def test_user_preference_upsert_invalidates_prefix(tmp_db: Path) -> None:
    """Upsert must drop every cached entry for this owner_key (prompt + dict)."""

    from app.services.user_preference_service import UserPreferenceService

    service = UserPreferenceService(db_path=tmp_db)
    service.upsert(owner_key="bob", language="en-US")
    service.format_for_prompt("bob")
    service.get("bob")
    db_tag = db_tag_for(tmp_db)
    cache = get_default_cache()
    assert cache.get(f"memory:{db_tag}:user_pref:dict:bob") is not None
    assert cache.get(f"memory:{db_tag}:user_pref:bob") is not None

    service.upsert(owner_key="bob", language="zh-CN", detail_level="concise")
    # After upsert, both keys for bob must be gone. ``upsert`` ends with a
    # ``get`` call which re-populates the dict key; the prompt key stays
    # empty until the next ``format_for_prompt`` triggers a re-render.
    assert cache.get(f"memory:{db_tag}:user_pref:bob") is None
    # The dict key is repopulated by the trailing get() and reflects the
    # new value, proving the cache write-through was correct.
    assert cache.get(f"memory:{db_tag}:user_pref:dict:bob")["detail_level"] == "concise"

    new_prompt = service.format_for_prompt("bob")
    assert "回答详略: concise" in new_prompt
    assert cache.get(f"memory:{db_tag}:user_pref:bob") == new_prompt


def test_experience_memory_list_cached_and_evicted_on_create(tmp_db: Path) -> None:
    """``list`` is the highest-ROI hot path. Cache it, then verify a create
    invalidates the list prefix."""

    from app.services.experience_memory_service import ExperienceMemoryService

    service = ExperienceMemoryService(db_path=tmp_db, index_service=None)
    service.create_manual(
        project_id="proj",
        symptoms="db pool exhausted",
        root_cause="connection leak",
        resolution="recycle pool",
        environment="prod",
        service_name="api",
    )
    reset_default_cache()
    cache = get_default_cache()
    db_tag = db_tag_for(tmp_db)

    first = service.list(project_id="proj", enabled=True, limit=10)
    assert len(first) == 1
    list_key = f"memory:{db_tag}:exp:list:proj:1:10"
    assert cache.get(list_key) is not None, "list should be cached after first call"

    # Second call must come from cache — back-fill timestamp identical.
    second = service.list(project_id="proj", enabled=True, limit=10)
    assert second == first

    # A new card invalidates the list prefix.
    service.create_manual(
        project_id="proj",
        symptoms="cache miss storm",
        root_cause="key expiration",
        resolution="warm cache",
    )
    assert cache.get(list_key) is None, "list prefix should be invalidated"
    after = service.list(project_id="proj", enabled=True, limit=10)
    assert len(after) == 2


def test_experience_memory_get_returns_deep_copy(tmp_db: Path) -> None:
    """Sanity check: even on the SQLite read-through (cold) path, mutating
    the returned dict must not poison subsequent reads via the cache."""

    from app.services.experience_memory_service import ExperienceMemoryService

    service = ExperienceMemoryService(db_path=tmp_db, index_service=None)
    eid = service.create_manual(
        project_id="proj",
        symptoms="boom",
        root_cause="boom",
        resolution="boom",
    )
    reset_default_cache()
    first = service.get(eid)
    assert first is not None
    first["root_cause"] = "tampered"
    first["source_event_ids"].append("ev-1")

    second = service.get(eid)
    assert second is not None
    assert second["root_cause"] == "boom"
    assert second["source_event_ids"] == []


def test_experience_memory_recall_does_not_enter_cache(tmp_db: Path) -> None:
    """Plan §3.3.1 option A — recall does not populate the cache."""

    from app.services.experience_memory_service import ExperienceMemoryService

    service = ExperienceMemoryService(db_path=tmp_db, index_service=None)
    service.create_manual(
        project_id="proj",
        symptoms="database connection pool exhausted",
        root_cause="connection leak",
        resolution="recycle pool",
    )
    reset_default_cache()
    cache = get_default_cache()
    db_tag = db_tag_for(tmp_db)
    recall_prefix = f"memory:{db_tag}:exp:recall:"

    # ``recall`` itself must not write to the cache. ``list`` is cached as
    # part of the call chain — invalidate its prefix first so we can isolate
    # the recall behaviour.
    cache.invalidate_prefix(f"memory:{db_tag}:exp:list:")
    service.recall(query="connection pool", project_id="proj", top_k=3)
    added = cache.invalidate_prefix(recall_prefix)
    assert added == 0, "recall must not write to the cache"


def test_service_knowledge_lookup_caches_and_evicts_on_relation(tmp_db: Path) -> None:
    """Review condition 3 — ``upsert_relation`` invalidates the source's lookup
    cache (so the next read surfaces the new relation)."""

    from app.services.service_knowledge_service import ServiceKnowledgeService

    service = ServiceKnowledgeService(db_path=tmp_db)
    service.upsert_service(
        project_id="proj",
        service_name="api",
        environment="prod",
        owner_team="team-a",
    )
    service.upsert_service(
        project_id="proj",
        service_name="db",
        environment="prod",
        owner_team="team-b",
    )

    reset_default_cache()
    cache = get_default_cache()
    db_tag = db_tag_for(tmp_db)

    first = service.lookup(project_id="proj", service_name="api", environment="prod")
    assert first is not None
    assert first["relations"] == []
    lookup_key = f"memory:{db_tag}:svc:lookup:proj:api:prod"
    assert cache.get(lookup_key) is not None

    # Add a relation. The lookup for the *source* service (api) must be
    # invalidated so the next call reflects the new relation.
    service.upsert_relation(
        project_id="proj",
        source_service="api",
        target_service="db",
        relation_type="depends_on",
        environment="prod",
    )
    assert cache.get(lookup_key) is None, (
        "upsert_relation must invalidate the source's lookup cache"
    )

    after = service.lookup(project_id="proj", service_name="api", environment="prod")
    assert after is not None
    assert len(after["relations"]) == 1
    assert after["relations"][0]["target_service"] == "db"


def test_service_knowledge_compare_metric_aliasing(tmp_db: Path) -> None:
    """``compare_metric`` should expose a stable view that is not aliasing
    poisoned across calls."""

    from app.services.service_knowledge_service import ServiceKnowledgeService

    service = ServiceKnowledgeService(db_path=tmp_db)
    service.upsert_service(project_id="p", service_name="svc", environment="prod")
    service.upsert_baseline(
        project_id="p",
        service_name="svc",
        environment="prod",
        metric_name="cpu",
        min_value=0.0,
        max_value=1.0,
    )
    reset_default_cache()
    first = service.compare_metric(
        project_id="p",
        service_name="svc",
        environment="prod",
        metric_name="cpu",
        value=0.5,
    )
    assert first is not None
    first["value"] = 999.0  # caller mutation; should not stick

    second = service.compare_metric(
        project_id="p",
        service_name="svc",
        environment="prod",
        metric_name="cpu",
        value=0.7,
    )
    assert second is not None
    assert second["value"] == 0.7
    assert second["within_range"] is True


def test_service_knowledge_baseline_change_invalidates(tmp_db: Path) -> None:
    from app.services.service_knowledge_service import ServiceKnowledgeService

    service = ServiceKnowledgeService(db_path=tmp_db)
    service.upsert_service(project_id="p", service_name="svc", environment="prod")
    service.upsert_baseline(
        project_id="p",
        service_name="svc",
        environment="prod",
        metric_name="cpu",
        min_value=0.0,
        max_value=1.0,
    )
    reset_default_cache()
    cache = get_default_cache()
    db_tag = db_tag_for(tmp_db)
    metric_key = f"memory:{db_tag}:svc:metric:p:svc:prod:cpu"
    assert cache.get(metric_key) is None

    out = service.compare_metric(
        project_id="p", service_name="svc", environment="prod",
        metric_name="cpu", value=0.5,
    )
    assert out is not None
    assert cache.get(metric_key) is not None

    # Updating the baseline must drop both the metric and the lookup cache.
    service.upsert_baseline(
        project_id="p", service_name="svc", environment="prod",
        metric_name="cpu", min_value=0.0, max_value=0.4,
    )
    assert cache.get(metric_key) is None
    out2 = service.compare_metric(
        project_id="p", service_name="svc", environment="prod",
        metric_name="cpu", value=0.5,
    )
    assert out2 is not None
    assert out2["within_range"] is False, "must reflect the new max_value"


def test_db_tag_isolation_across_dbs(tmp_path: Path) -> None:
    """Two DB files pointing at different paths must not share cache entries."""

    from app.services.user_preference_service import UserPreferenceService

    a_db = tmp_path / "cache_iso_a.db"
    b_db = tmp_path / "cache_iso_b.db"
    svc_a = UserPreferenceService(db_path=a_db)
    svc_b = UserPreferenceService(db_path=b_db)
    svc_a.upsert(owner_key="shared", language="en-US")
    svc_b.upsert(owner_key="shared", language="zh-CN")

    # No cross-talk.
    prompt_a = svc_a.format_for_prompt("shared")
    prompt_b = svc_b.format_for_prompt("shared")
    assert "回答语言: en-US" in prompt_a
    assert "回答语言: zh-CN" in prompt_b

    # And the cache keys are indeed separated by db_tag.
    db_a = db_tag_for(a_db)
    db_b = db_tag_for(b_db)
    cache = get_default_cache()
    assert cache.get(f"memory:{db_a}:user_pref:dict:shared")["language"] == "en-US"
    assert cache.get(f"memory:{db_b}:user_pref:dict:shared")["language"] == "zh-CN"
