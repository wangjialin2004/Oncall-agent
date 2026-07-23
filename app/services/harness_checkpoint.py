"""Step-level checkpoint store for the unified harness loop.

Each completed step writes three keys to Redis:

* ``{ns}:ckpt:{owner}:{session}:meta``     - run-level metadata (state, usage, tail events)
* ``{ns}:ckpt:{owner}:{session}:steps:N``  - per-step tool calls, results, events
* ``{ns}:ckpt:{owner}:{session}:messages`` - serialized ``list[ChatMessage]`` for LLM resume

Resume happens transparently on the next ``POST /api/assistant`` with the same
``session_id``. The harness restores state/context and continues from the next
incomplete step; historical tool calls are **not** re-executed. A request-level
``checkpoint_replay=False`` can still force a close-only finalization.

This module is the only public surface the harness loop should import from.
All Redis IO is wrapped in a fail-soft envelope so Redis outages never break
the streaming response.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from loguru import logger

from app.config import config
from app.core.llm_client import ChatMessage
from app.services.redis_client import (
    is_redis_available,
    redis_health_snapshot,
    reset_redis_client,
)
from app.utils.serialization import json_dumps, json_loads
from app.utils.time import utc_now

_DEFAULT_TOOLS: tuple[str, ...] = (
    # Read-only / idempotent investigation tools (metadata + is_step_idempotent).
    # Resume no longer gates on this list; it continues from next_step by default.
    "delegate_to_expert",
    "delegate_parallel",
    "query_prometheus_alerts",
    "retrieve_knowledge",
    "recall_experience",
    "lookup_service_knowledge",
    "check_redis_health",
    "get_current_time",
    "query_recent_changes",
    "context_read",
    "context_note",
    "read_attachment",
)
_TIMELINE_TAIL_MAX = 20

_CURRENT_VERSION = 2  # bumped: meta now carries context_version/context_snapshot_ref

#: Schema version stored in ``CheckpointResume.context_snapshot_version`` to
#: signal that the checkpoint intentionally does NOT carry the full context
#: body. The harness should recover context via
#: :class:`app.agent.context.store.ContextStateStore` instead.
_CHECKPOINT_CONTEXT_BODY_DROPPED_VERSION = 2


def _coerce_optional_int(value: Any) -> int | None:
    """Return ``value`` as int, or None. Defends against None / odd types."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_str(value: Any) -> str | None:
    """Return ``value`` as str, or None when empty / not a string."""
    if value is None:
        return None
    s = str(value)
    return s or None


# ---------------------------------------------------------------------------
# Resume payload
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CheckpointResume:
    """What the harness needs to skip ahead to the next incomplete step.

    Per plan ``plan/2026-07-08-stateful-agent-context.md`` §9, ``messages``
    remains for the migration window — new code still writes them for replay,
    but :class:`app.agent.context.store.ContextStateStore` is the canonical
    source of context. ``context_version`` and ``context_snapshot_ref`` are
    the only authoritative pointers the harness needs to resume correctly.
    """

    next_step: int
    state_fields: dict[str, Any]
    messages: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    route: str
    route_reason: str
    idempotent_tools: list[str]
    started_at: str
    completed: bool = False
    context_version: int | None = None
    context_snapshot_ref: str | None = None
    # Cached schema version that wrote this checkpoint. Used by the harness
    # to detect "old checkpoint with full messages" vs "new checkpoint with
    # context reference only" and route accordingly.
    checkpoint_version: int = 1


@dataclass(slots=True)
class CheckpointStats:
    saves: int = 0
    resumes: int = 0
    deletes: int = 0
    errors: int = 0
    last_error: str = ""
    last_error_at: str = ""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class HarnessCheckpointStore:
    """Redis-backed checkpoint store with fail-soft IO."""

    def __init__(
        self,
        *,
        namespace: str | None = None,
        ttl_seconds: int | None = None,
        idempotent_tools: tuple[str, ...] | None = None,
        redis_factory: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self.namespace = str(namespace or getattr(config, "redis_namespace", "super_biz_agent"))
        self.ttl_seconds = int(
            ttl_seconds
            if ttl_seconds is not None
            else getattr(config, "harness_checkpoint_ttl_seconds", 1800)
        )
        self.idempotent_tools: tuple[str, ...] = (
            idempotent_tools if idempotent_tools is not None else _DEFAULT_TOOLS
        )
        self._redis_factory = redis_factory
        self._clock = clock or utc_now
        self._lock = asyncio.Lock()
        self.stats = CheckpointStats()

    # ------------------------------------------------------- public surface

    def is_enabled(self) -> bool:
        if self._redis_factory is not None:
            return True
        return (
            bool(getattr(config, "redis_enabled", False))
            and bool(getattr(config, "harness_checkpoint_enabled", False))
            and is_redis_available()
            and redis_health_snapshot().get("status") == "ready"
        )

    async def save_step(
        self,
        *,
        owner_key: str,
        session_id: str,
        state: Any,
        messages: list[ChatMessage],
        step_index: int,
        step_payload: dict[str, Any],
        context_version: int | None = None,
        context_snapshot_ref: str | None = None,
        persist_messages: bool = True,
    ) -> None:
        """Persist the latest run snapshot. Fire-and-forget safe.

        Per plan §9, ``messages`` is no longer required for the stateful
        context path: callers should pass ``persist_messages=False`` once the
        whiteboard is the canonical source. ``context_version`` /
        ``context_snapshot_ref`` are recorded in the meta so resume can ask
        :class:`ContextStateStore` to rehydrate instead of replaying messages.
        """
        try:
            async with self._lock:
                client = await self._client()
                meta_key = self._meta_key(owner_key, session_id)
                existing = await self._safe_get_meta(client, meta_key)
                if existing and int(existing.get("step") or 0) > int(step_index):
                    # Background writers can complete out of order. Never let
                    # an older snapshot move a session backwards.
                    return
                state_snapshot = copy.deepcopy(state)
                meta = self._compose_meta(
                    state=state_snapshot,
                    existing=existing,
                    step_index=step_index,
                    context_version=context_version,
                    context_snapshot_ref=context_snapshot_ref,
                )
                step_key = self._step_key(owner_key, session_id, step_index)
                messages_key = self._messages_key(owner_key, session_id)
                if persist_messages:
                    msgs_payload = json_dumps([m.to_dict() for m in messages])
                else:
                    msgs_payload = json_dumps([])
                async with client.pipeline(transaction=False) as pipe:
                    pipe.set(meta_key, json_dumps(meta), ex=self.ttl_seconds)
                    pipe.set(step_key, json_dumps(step_payload), ex=self.ttl_seconds)
                    pipe.set(messages_key, msgs_payload, ex=self.ttl_seconds)
                    await pipe.execute()
                self.stats.saves += 1
        except Exception as exc:
            self._record_error(exc, "save_step")

    async def try_resume(self, owner_key: str, session_id: str) -> CheckpointResume | None:
        """Return a resume payload if a non-terminal checkpoint exists.

        The ``is_enabled`` gate is enforced by ``HarnessService.__init__`` —
        if a store instance is wired into the service we always attempt the
        read. Tests inject a ``redis_factory`` directly and should not need
        to flip the global config flags.
        """
        try:
            client = await self._client()
            meta_raw = await self._safe_get_meta(client, self._meta_key(owner_key, session_id))
            if not meta_raw:
                self.stats.resumes += 1
                return None
            if bool(meta_raw.get("completed")):
                self.stats.resumes += 1
                return None
            step_index = int(meta_raw.get("step", 0))
            if step_index <= 0:
                self.stats.resumes += 1
                return None
            messages_raw = await self._safe_get_json(
                client, self._messages_key(owner_key, session_id), default=[]
            )
            steps: list[dict[str, Any]] = []
            for index in range(1, step_index + 1):
                payload = await self._safe_get_json(
                    client,
                    self._step_key(owner_key, session_id, index),
                    default={},
                )
                if payload:
                    steps.append(payload)
            self.stats.resumes += 1
            return CheckpointResume(
                next_step=step_index + 1,
                state_fields=dict(meta_raw.get("state_fields") or {}),
                messages=list(messages_raw or []),
                steps=steps,
                route=str(meta_raw.get("route") or "harness"),
                route_reason=str(meta_raw.get("route_reason") or ""),
                idempotent_tools=list(meta_raw.get("idempotent_tools") or []),
                started_at=str(meta_raw.get("started_at") or ""),
                completed=False,
                context_version=_coerce_optional_int(meta_raw.get("context_version")),
                context_snapshot_ref=_coerce_optional_str(meta_raw.get("context_snapshot_ref")),
                checkpoint_version=int(meta_raw.get("version") or 1),
            )
        except Exception as exc:
            self._record_error(exc, "try_resume")
            return None

    async def mark_completed(
        self,
        *,
        owner_key: str,
        session_id: str,
        state: Any,
    ) -> None:
        """Flip the meta ``completed`` flag; key lingers until TTL expires."""
        try:
            async with self._lock:
                client = await self._client()
                meta_key = self._meta_key(owner_key, session_id)
                existing = await self._safe_get_meta(client, meta_key)
                if not existing:
                    return
                existing["completed"] = True
                existing["last_step_at"] = self._clock()
                existing["state_fields"] = self._state_fields(state)
                await client.set(meta_key, json_dumps(existing), ex=self.ttl_seconds)
        except Exception as exc:
            self._record_error(exc, "mark_completed")

    async def get_meta(self, owner_key: str, session_id: str) -> dict[str, Any] | None:
        """Return the meta dict (no messages, no step bodies)."""
        try:
            client = await self._client()
            return await self._safe_get_meta(client, self._meta_key(owner_key, session_id))
        except Exception as exc:
            self._record_error(exc, "get_meta")
            return None

    async def delete(self, owner_key: str, session_id: str) -> int:
        """Delete known keys for this session without a broad Redis scan."""
        try:
            client = await self._client()
            meta = await self._safe_get_meta(client, self._meta_key(owner_key, session_id))
            max_step = int((meta or {}).get("step") or 0)
            keys = [
                self._meta_key(owner_key, session_id),
                self._messages_key(owner_key, session_id),
            ]
            keys.extend(
                self._step_key(owner_key, session_id, index) for index in range(1, max_step + 1)
            )
            removed = int(await client.delete(*keys)) if keys else 0
            self.stats.deletes += 1
            return removed
        except Exception as exc:
            self._record_error(exc, "delete")
            return 0

    def is_step_idempotent(self, tool_names: list[str]) -> bool:
        """Return True only if every tool in the step is on the whitelist."""
        if not tool_names:
            return True
        whitelist = set(self.idempotent_tools)
        return all(name in whitelist for name in tool_names)

    # ------------------------------------------------------------ internals

    def _prefix(self, owner_key: str, session_id: str) -> str:
        scope = hashlib.sha256(f"{owner_key}\x00{session_id}".encode()).hexdigest()[:32]
        return f"{self.namespace}:ckpt:{scope}:"

    def _meta_key(self, owner_key: str, session_id: str) -> str:
        return f"{self._prefix(owner_key, session_id)}meta"

    def _messages_key(self, owner_key: str, session_id: str) -> str:
        return f"{self._prefix(owner_key, session_id)}messages"

    def _step_key(self, owner_key: str, session_id: str, step_index: int) -> str:
        return f"{self._prefix(owner_key, session_id)}steps:{int(step_index)}"

    async def _client(self) -> Any:
        if self._redis_factory is not None:
            return self._redis_factory()
        from app.services.redis_client import get_redis_client

        return await get_redis_client()

    async def _safe_get_meta(self, client: Any, key: str) -> dict[str, Any] | None:
        raw = await client.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        parsed = json_loads(raw, default={})
        return parsed if isinstance(parsed, dict) else None

    async def _safe_get_json(self, client: Any, key: str, *, default: Any) -> Any:
        raw = await client.get(key)
        if not raw:
            return default
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        return json_loads(raw, default=default)

    def _compose_meta(
        self,
        *,
        state: Any,
        existing: dict[str, Any] | None,
        step_index: int,
        context_version: int | None = None,
        context_snapshot_ref: str | None = None,
    ) -> dict[str, Any]:
        existing = dict(existing or {})
        timeline = getattr(state, "timeline_events", None) or []
        tail = list(timeline[-_TIMELINE_TAIL_MAX:]) if isinstance(timeline, list) else []
        existing.update(
            {
                "version": _CURRENT_VERSION,
                "owner_key": str(getattr(state, "owner_key", "") or ""),
                "session_id": str(getattr(state, "session_id", "") or ""),
                "trace_id": str(getattr(state, "trace_id", "") or ""),
                "route": str(getattr(state, "route", "harness") or "harness"),
                "route_reason": str(getattr(state, "route_reason", "") or ""),
                "step": int(step_index),
                "state_fields": self._state_fields(state),
                "timeline_events_tail": tail,
                "idempotent_tools": list(self.idempotent_tools),
                "last_step_at": self._clock(),
                # Plan §9: checkpoint only references the context, never holds it.
                "context_version": _coerce_optional_int(context_version)
                or _coerce_optional_int(existing.get("context_version")),
                "context_snapshot_ref": _coerce_optional_str(context_snapshot_ref)
                or _coerce_optional_str(existing.get("context_snapshot_ref")),
            }
        )
        if not existing.get("started_at"):
            existing["started_at"] = self._clock()
        return existing

    def _state_fields(self, state: Any) -> dict[str, Any]:
        """Project ``HarnessState`` down to JSON-friendly fields.

        We deliberately drop ``timeline_events`` from the snapshot because the
        full list lives in step bodies and SSE history; only the tail survives
        on the meta so the verifier can still see recent evidence on resume.
        """
        try:
            snapshot = asdict(state)
        except Exception:
            return {}
        snapshot.pop("timeline_events", None)
        # Convert any non-JSON-native values defensively.
        return json.loads(json_dumps(snapshot))

    def _record_error(self, exc: Exception, op: str) -> None:
        self.stats.errors += 1
        self.stats.last_error = str(exc)
        self.stats.last_error_at = self._clock()
        logger.warning(f"[checkpoint/{op}] best-effort 失败（已忽略）: {exc}")

    async def close(self) -> None:
        """Tear down the cached Redis client. Idempotent."""
        await reset_redis_client()


# ---------------------------------------------------------------------------
# Singleton wiring
# ---------------------------------------------------------------------------


_DEFAULT_STORE: HarnessCheckpointStore | None = None
_DEFAULT_STORE_LOCK = asyncio.Lock()


def get_default_checkpoint_store() -> HarnessCheckpointStore:
    """Return the process-wide checkpoint store, creating it lazily."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = HarnessCheckpointStore()
    return _DEFAULT_STORE


def reset_default_checkpoint_store() -> None:
    """Drop the cached store. Test fixtures call this between cases."""
    global _DEFAULT_STORE
    _DEFAULT_STORE = None


def messages_from_dict(items: list[dict[str, Any]]) -> list[ChatMessage]:
    """Convert a list of dicts back into ``ChatMessage`` objects, dropping garbage."""
    messages: list[ChatMessage] = []
    for item in items or []:
        msg = ChatMessage.from_dict(item)
        if msg is not None:
            messages.append(msg)
    return messages


# ---------------------------------------------------------------------------
# Helpers used by the harness loop
# ---------------------------------------------------------------------------


def is_checkpoint_active() -> bool:
    """Return True when the global feature flag stack says checkpoint should run."""
    return (
        bool(getattr(config, "harness_enabled", False))
        and bool(getattr(config, "harness_checkpoint_enabled", False))
        and bool(getattr(config, "redis_enabled", False))
        and is_redis_available()
    )
