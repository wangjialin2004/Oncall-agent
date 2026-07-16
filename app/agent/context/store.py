"""Redis-first :class:`AgentContextStateStore` with DB snapshot fallback.

Per plan ``plan/2026-07-08-stateful-agent-context.md`` §5 the canonical read
path is:

1. Redis hit → return state.
2. Redis miss / corrupt / schema mismatch → DB snapshot.
3. DB snapshot miss / corrupt / schema mismatch → rebuild from conversation
   turns (handled by :func:`rebuild_from_conversation_turns`, supplied by the
   caller — we accept it as a callable so this module doesn't depend on the
   ``conversation_service`` directly).

Writes always go to Redis first; DB snapshot is best-effort. Redis write
failures are recorded as warnings rather than aborting the main turn (plan
§2.3).
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from app.agent.context.persistence import (
    ContextSchemaError,
    state_from_dict,
    state_to_dict,
)
from app.agent.context.state import SCHEMA_VERSION, AgentContextState
from app.config import config

# Re-export for downstream imports.
__all__ = [
    "ContextStateStore",
    "ContextStateStoreSettings",
    "StoreResult",
]


def _build_redis_key(namespace: str, owner_key: str, session_id: str) -> str:
    return f"{namespace}:context:{owner_key}:{session_id}"


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it's awaitable; return it directly otherwise.

    Lets the store accept either sync callables (current
    ``ContextSnapshotService`` is sync) or async callables without forcing
    every caller to wrap their service method in ``asyncio.to_thread``.
    """
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(slots=True)
class ContextStateStoreSettings:
    """Knobs for the store.

    Defaults match the plan sections (§5 / §6). The TTL is in seconds — Redis
    treats values past it as missing.
    """

    redis_enabled: bool = True
    redis_namespace: str = "super_biz_agent"
    redis_ttl_seconds: int = 86_400
    db_snapshot_enabled: bool = True
    patch_history_limit: int = 200


@dataclass(slots=True)
class StoreResult:
    """Returned from :meth:`ContextStateStore.get_or_rebuild`.

    ``source`` indicates where the state was found — useful for metrics and
    for tests that want to assert the read path didn't fan out to disk.
    """

    state: AgentContextState
    source: str  # "redis" | "db_snapshot" | "rebuilt" | "fresh"
    warnings: list[str] = field(default_factory=list)


#: Rebuild strategy: ``rebuild_from_conversation_turns(owner_key, session_id)``.
#: Provided by the harness integration to avoid importing
#: ``conversation_service`` here. ``None`` means "do not attempt rebuild" —
#: only relevant when Redis and DB both miss.
RebuildCallable = Callable[[str, str], Awaitable[AgentContextState | None]]


class ContextStateStore:
    """Hot/cold storage for the whiteboard."""

    def __init__(
        self,
        *,
        db_snapshot_get: Callable[..., Any],
        db_snapshot_save: Callable[..., Any],
        redis_get_set: Callable[[str, str, str, int | None], Awaitable[Any]]
        | None = None,
        settings: ContextStateStoreSettings | None = None,
    ) -> None:
        """Construct a store.

        ``db_snapshot_get`` and ``db_snapshot_save`` accept kwargs
        ``owner_key`` / ``session_id`` (and ``state`` for save). They may be
        either sync (current ``ContextSnapshotService``) or async — the store
        awaits the result if it's a coroutine.

        ``redis_get_set`` is the Redis access layer:

        - ``await redis_get_set("get", key, None, None)`` → str | None
        - ``await redis_get_set("set", key, value, ttl)`` → bool | None

        Passing ``None`` for ``redis_get_set`` disables Redis entirely (used
        by tests).
        """
        self._snapshot_get = db_snapshot_get
        self._snapshot_save = db_snapshot_save
        self._redis_get_set = redis_get_set
        self.settings = settings or ContextStateStoreSettings(
            redis_enabled=bool(getattr(config, "redis_enabled", True)),
            redis_namespace=str(getattr(config, "redis_namespace", "super_biz_agent")),
            redis_ttl_seconds=int(getattr(config, "harness_context_redis_ttl_seconds", 86_400)),
            db_snapshot_enabled=bool(getattr(config, "harness_context_db_snapshot_enabled", True)),
            patch_history_limit=int(getattr(config, "harness_context_patch_history_limit", 200)),
        )

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    async def get_or_rebuild(
        self,
        owner_key: str,
        session_id: str,
        *,
        rebuild: RebuildCallable | None = None,
    ) -> StoreResult:
        """Read state with the Redis → DB → rebuild ladder.

        ``rebuild`` is the callable that handles the final fallthrough using
        ``conversation_service.get_turns``. When Redis and DB both miss and no
        ``rebuild`` callable is supplied, the store returns a fresh empty state.
        """
        warnings: list[str] = []

        # 1. Redis
        redis_state = await self._load_from_redis(
            owner_key, session_id, warnings=warnings,
        )
        if redis_state is not None:
            return StoreResult(state=redis_state, source="redis", warnings=warnings)

        # 2. DB snapshot
        if self.settings.db_snapshot_enabled:
            try:
                snapshot = await _maybe_await(
                    self._snapshot_get(owner_key=owner_key, session_id=session_id)
                )
            except Exception as exc:  # noqa: BLE001 — covered by plan §2.3
                warnings.append(f"snapshot read failed: {exc!r}")
                snapshot = None
            if snapshot is not None:
                # Re-warm Redis but tolerate failure.
                await self._write_redis(snapshot, warnings=warnings)
                return StoreResult(state=snapshot, source="db_snapshot", warnings=warnings)

        # 3. Rebuild from conversation turns.
        rebuilt: AgentContextState | None = None
        if rebuild is not None:
            try:
                rebuilt = await rebuild(owner_key, session_id)
            except Exception as exc:  # noqa: BLE001 — same reasoning
                warnings.append(f"rebuild failed: {exc!r}")

        if rebuilt is not None:
            await self._write_redis(rebuilt, warnings=warnings)
            await self._save_snapshot(rebuilt, owner_key, session_id, warnings=warnings)
            return StoreResult(state=rebuilt, source="rebuilt", warnings=warnings)

        # 4. Brand-new empty state (no Redis / DB / rebuild available).
        fresh = AgentContextState(owner_key=owner_key, session_id=session_id)
        await self._write_redis(fresh, warnings=warnings)
        return StoreResult(state=fresh, source="fresh", warnings=warnings)

    async def save(
        self,
        owner_key: str,
        session_id: str,
        state: AgentContextState,
        *,
        persist_snapshot: bool = True,
    ) -> list[str]:
        """Persist a state: Redis (best-effort) + DB snapshot (optional).

        Returns the list of warnings collected during writes — callers can
        surface them to the timeline / observability channel.
        """
        warnings: list[str] = []
        await self._write_redis(state, warnings=warnings)
        if persist_snapshot and self.settings.db_snapshot_enabled:
            await self._save_snapshot(state, owner_key, session_id, warnings=warnings)
        return warnings

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _build_key(self, owner_key: str, session_id: str) -> str:
        return _build_redis_key(self.settings.redis_namespace, owner_key, session_id)

    async def _load_from_redis(
        self, owner_key: str, session_id: str, *, warnings: list[str] | None = None,
    ) -> AgentContextState | None:
        if not self.settings.redis_enabled or self._redis_get_set is None:
            return None
        key = self._build_key(owner_key, session_id)
        try:
            raw = await self._redis_get_set("get", key, None, None)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 — see plan §2.3
            logger.warning(f"context redis read failed (ignored): {exc}")
            if warnings is not None:
                warnings.append(f"redis read failed: {exc!r}")
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return state_from_dict(payload)
        except (ContextSchemaError, json.JSONDecodeError) as exc:
            logger.warning(f"corrupt context redis payload (ignored): {exc}")
            if warnings is not None:
                warnings.append(f"corrupt redis payload: {exc!r}")
            return None

    async def _write_redis(
        self, state: AgentContextState, *, warnings: list[str],
    ) -> None:
        if not self.settings.redis_enabled or self._redis_get_set is None:
            return
        key = self._build_key(state.owner_key, state.session_id)
        payload_json = json.dumps(state_to_dict(state), ensure_ascii=False)
        try:
            await self._redis_get_set(  # type: ignore[arg-type]
                "set", key, payload_json, self.settings.redis_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — see plan §2.3
            logger.warning(f"context redis write failed (ignored): {exc}")
            warnings.append(f"redis write failed: {exc!r}")

    async def _save_snapshot(
        self,
        state: AgentContextState,
        owner_key: str,
        session_id: str,
        *,
        warnings: list[str],
    ) -> None:
        try:
            await _maybe_await(
                self._snapshot_save(
                    owner_key=owner_key, session_id=session_id, state=state,
                )
            )
        except Exception as exc:  # noqa: BLE001 — plan §2.3
            logger.warning(f"context db snapshot save failed (ignored): {exc}")
            warnings.append(f"snapshot save failed: {exc!r}")
