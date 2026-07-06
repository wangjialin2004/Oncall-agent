"""Process-local TTL + LRU cache for the long-term memory subsystem.

Background
==========

The memory subsystem (Experience / Service Knowledge / User Preference) currently
hits SQLite on every read. As the long-term memory grows past a few hundred rows
the ``recall`` / ``lookup`` / ``format_for_prompt`` hot paths become CPU- and
IO-bound, with the harness main loop amplifying the cost (R-hot-1..4 in the
design plan).

This module provides a *unified, degradable, observable* L1 cache that fronts
the three services. The design choices — TTL, capacity, copy-on-read, key
naming, invalidation matrix — are codified in
``plan/memory-cache-layer.md`` (v0.2).

Key invariants
==============

1.  **SQLite remains the source of truth.** Every read cache write is done
    *after* a successful SQLite fetch; every write goes to SQLite first and
    triggers ``invalidate`` *afterwards*. SQLite failure ⇒ no cache action.
2.  **Cache failure never breaks the caller.** ``set`` / ``invalidate`` are
    best-effort; exceptions are caught and counted in ``stats.errors``.
3.  **No aliasing across callers** (the §3.6 correctness constraint). Any
    dict / list value returned by ``get`` is a deep copy of the cached entry.
4.  **Per-database isolation.** Every key is prefixed with a short stable
    hash of the resolved DB path (``db_tag``) so a global singleton does not
    bleed state between test fixtures and production.
5.  **Process restart ⇒ full miss.** This is by design; SQLite is the
    recoverable source of truth.

Public surface
==============

* :class:`MemoryCache` — the cache instance.
* :func:`memory_cache` — the lazily created module-level singleton.
* :func:`db_tag_for` — derive a per-DB prefix fragment.
* :func:`query_hash` — stable hash fragment for a recall query string.

The module is intentionally framework-free: no FastAPI / Pydantic dependency,
so it can be imported from CLI scripts and tests without spinning the app.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Hashable, Optional


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class CacheStats:
    """Counters surfaced to ``/health`` and the bench script."""

    hits: int = 0
    misses: int = 0
    invalidations: int = 0
    evictions: int = 0
    errors: int = 0
    set_calls: int = 0

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def snapshot(self) -> dict[str, int | float]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "invalidations": self.invalidations,
            "evictions": self.evictions,
            "errors": self.errors,
            "set_calls": self.set_calls,
            "hit_ratio": round(self.hit_ratio, 4),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_copy(value: Any) -> Any:
    """Return a deep copy of *value* for mutable containers, otherwise the
    original reference.

    This is the aliasing guard from plan §3.6. ``dict`` and ``list`` instances
    are copied recursively (``copy.deepcopy`` is fine here — service payloads
    are flat JSON-shaped structures a few KB in size). All other types
    (including ``str``, ``int``, ``None``, tuples) are returned as-is because
    they are either immutable or are treated as opaque by callers.
    """

    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    return value


def db_tag_for(db_path: str | Path | None) -> str:
    """Stable, short hash fragment that uniquely identifies a database file.

    Production uses a single ``memory_db_path`` so the tag is constant across
    every call. Tests that point services at temporary databases get unique
    tags and therefore isolated cache entries — this is the §3.2 / §3.7
    "test isolation" requirement.
    """

    resolved = "" if db_path is None else str(Path(db_path).expanduser().resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]


def query_hash(query: str) -> str:
    """Stable, short hash fragment for a recall query string.

    Recall keys include a hash of the user's query. We cannot use
    ``hash(query)`` because Python's built-in hash randomization makes the
    result unstable across processes, and recall caches must survive a
    restart of the API. The query is normalized upstream
    (``app.utils.text.normalize_text``) before being passed in.
    """

    payload = (query or "").strip()
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# LRU + TTL
# ---------------------------------------------------------------------------


class MemoryCache:
    """Thread-safe in-process TTL + LRU cache.

    Parameters
    ----------
    max_entries:
        Maximum number of cached entries. When exceeded, the least-recently
        *inserted* entry is evicted (and ``stats.evictions`` is incremented).
    default_ttl:
        Default lifetime in seconds. Pass ``ttl=0`` to ``set`` to disable TTL
        for a specific entry (still subject to capacity-based eviction).
    enabled:
        When ``False``, the cache is a passthrough. ``MEMORY_CACHE_ENABLED=0``
        in the environment toggles this via :class:`app.config.Settings`.
    clock:
        Injection seam for tests. Defaults to :func:`time.monotonic`.
    on_error:
        Optional callable invoked for swallowed exceptions. Useful in tests;
        production lets exceptions fall through silently so a flaky cache
        cannot block the call site.
    """

    def __init__(
        self,
        *,
        max_entries: int = 1024,
        default_ttl: float = 60.0,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
        on_error: Optional[Callable[[str, Exception], None]] = None,
    ) -> None:
        self._data: "OrderedDict[Hashable, tuple[Any, float]]" = OrderedDict()
        self._max_entries = max(1, int(max_entries))
        self._default_ttl = float(default_ttl)
        self._enabled = bool(enabled)
        self._clock = clock
        self._lock = threading.RLock()
        self._on_error = on_error
        self.stats = CacheStats()

    # ------------------------------------------------------------------ config

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data)

    def stats_snapshot(self) -> dict[str, int | float]:
        with self._lock:
            size = len(self._data)
        snapshot = self.stats.snapshot()
        snapshot["size"] = size
        return snapshot

    # ----------------------------------------------------------------- public

    def get(self, key: Hashable) -> Any:
        """Return a deep-copied value or ``None`` if missing / expired."""

        if not self._enabled:
            self.stats.misses += 1
            return None
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            value, expires_at = entry
            if expires_at and expires_at < self._clock():
                self._data.pop(key, None)
                self.stats.misses += 1
                return None
            # LRU touch — move to the end so it's the *least* likely to be
            # evicted next. ``move_to_end`` is O(1) for OrderedDict.
            self._data.move_to_end(key)
            self.stats.hits += 1
            return _safe_copy(value)

    def set(
        self,
        key: Hashable,
        value: Any,
        *,
        ttl: Optional[float] = None,
    ) -> None:
        """Insert or replace *value* under *key*.

        Any exception raised while storing is swallowed and counted in
        ``stats.errors`` so the caller's write path is never blocked by a
        misbehaving cache.
        """

        if not self._enabled:
            return
        try:
            effective_ttl = self._default_ttl if ttl is None else float(ttl)
            expires_at = self._clock() + effective_ttl if effective_ttl > 0 else 0.0
            with self._lock:
                self._data[key] = (value, expires_at)
                self._data.move_to_end(key)
                while len(self._data) > self._max_entries:
                    self._data.popitem(last=False)
                    self.stats.evictions += 1
                self.stats.set_calls += 1
        except Exception as exc:  # pragma: no cover — defensive
            self._record_error(exc)

    def invalidate(self, key: Hashable) -> bool:
        """Remove *key*. Returns ``True`` when a value was actually dropped."""

        if not self._enabled:
            return False
        with self._lock:
            removed = self._data.pop(key, None) is not None
        if removed:
            self.stats.invalidations += 1
        return removed

    def invalidate_prefix(self, prefix: str) -> int:
        """Invalidate every string key starting with *prefix*.

        Used by write paths that affect an entire domain (e.g. the bulk
        ``import_from_monitor_mcp`` upserts) so we don't have to know every
        individual key upfront. Returns the number of entries dropped.
        """

        if not self._enabled:
            return 0
        if not isinstance(prefix, str) or not prefix:
            return 0
        with self._lock:
            victims = [key for key in self._data if isinstance(key, str) and key.startswith(prefix)]
            for key in victims:
                self._data.pop(key, None)
        if victims:
            self.stats.invalidations += len(victims)
        return len(victims)

    def clear(self) -> None:
        """Drop *every* entry. Reserved for test fixtures and operator
        tooling — production code paths should prefer targeted invalidation.
        """

        with self._lock:
            self._data.clear()

    # ----------------------------------------------------------------- utils

    def _record_error(self, exc: Exception) -> None:
        self.stats.errors += 1
        if self._on_error is not None:
            try:
                self._on_error("memory_cache", exc)
            except Exception:  # pragma: no cover — defensive
                pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_DEFAULT_CACHE: MemoryCache | None = None
_DEFAULT_CACHE_LOCK = threading.Lock()


def get_default_cache() -> MemoryCache:
    """Return the process-wide :class:`MemoryCache` instance.

    The settings are read lazily so that test fixtures which mutate
    ``config.memory_cache_*`` between tests get a fresh cache that reflects
    the new values. ``Settings`` is cached by Pydantic, so this lookup is
    effectively free.
    """

    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        with _DEFAULT_CACHE_LOCK:
            if _DEFAULT_CACHE is None:
                from app.config import config

                _DEFAULT_CACHE = MemoryCache(
                    max_entries=int(config.memory_cache_max_entries),
                    default_ttl=float(config.memory_cache_ttl_experience_seconds),
                    enabled=bool(config.memory_cache_enabled),
                )
    return _DEFAULT_CACHE


def reset_default_cache() -> None:
    """Drop the cached singleton. Test-only; production code never calls this."""

    global _DEFAULT_CACHE
    with _DEFAULT_CACHE_LOCK:
        _DEFAULT_CACHE = None


# Backwards-compatible alias used in the plan (§4.1). Importers that need the
# live singleton should use :func:`get_default_cache` so they pick up config
# changes.
def memory_cache() -> MemoryCache:
    return get_default_cache()


__all__ = [
    "CacheStats",
    "MemoryCache",
    "db_tag_for",
    "get_default_cache",
    "memory_cache",
    "query_hash",
    "reset_default_cache",
]
