"""Unified context repository with one load path and one durable commit owner.

The runtime is gated by ``HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED``. This
service never creates or alters schema: operators must explicitly apply schema
v3 before the unified path can load or commit.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from app.agent.context.envelope import (
    CommitResult,
    CompletedTurnCommit,
    ContextEnvelope,
    ContextScope,
    ContextWatermark,
)
from app.agent.context.projection import (
    PROJECTION_SCHEMA_V2,
    projection_from_dict,
    projection_to_dict,
)
from app.agent.context.reducer import apply_committed_turns, rebuild_projection_from_turns
from app.agent.context.state import AgentContextState
from app.config import config
from app.utils.serialization import json_loads
from app.utils.time import utc_now

RedisGetSet = Callable[[str, str, Any, int | None], Awaitable[Any]]


def _committed_redis_key(namespace: str, owner_key: str, session_id: str) -> str:
    return f"{namespace}:context:committed:{owner_key}:{session_id}"


def _inflight_redis_key(namespace: str, owner_key: str, session_id: str, run_id: str) -> str:
    return f"{namespace}:context:inflight:{owner_key}:{session_id}:{run_id}"


def _title_from_message(user_message: str) -> str:
    text = " ".join((user_message or "").split())
    return text[:40] if text else "新会话"


def _validate_scope(owner_key: str, session_id: str) -> None:
    if not str(owner_key or "").strip() or not str(session_id or "").strip():
        raise ValueError("owner_key and session_id are required")


@dataclass(slots=True)
class ContextRepositorySettings:
    redis_enabled: bool = True
    redis_namespace: str = "super_biz_agent"
    redis_ttl_seconds: int = 86_400
    inflight_ttl_seconds: int = 1_800
    db_snapshot_enabled: bool = True
    history_max_turns: int = 6


class ContextRepository:
    """Load one envelope and atomically commit a turn with its projection."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        redis_get_set: RedisGetSet | None = None,
        settings: ContextRepositorySettings | None = None,
    ) -> None:
        self.db_path = Path(db_path or config.memory_db_path)
        self._redis_get_set = redis_get_set
        self.settings = settings or ContextRepositorySettings(
            redis_enabled=bool(getattr(config, "redis_enabled", True)),
            redis_namespace=str(getattr(config, "redis_namespace", "super_biz_agent")),
            redis_ttl_seconds=int(getattr(config, "harness_context_redis_ttl_seconds", 86_400)),
            inflight_ttl_seconds=int(getattr(config, "harness_checkpoint_ttl_seconds", 1_800)),
            db_snapshot_enabled=bool(getattr(config, "harness_context_db_snapshot_enabled", True)),
            history_max_turns=int(getattr(config, "harness_history_max_turns", 6)),
        )
        self._schema_verified = False

    async def load_envelope(self, owner_key: str, session_id: str) -> ContextEnvelope:
        """Load projection metadata and a SQL-limited canonical turn window."""

        _validate_scope(owner_key, session_id)
        warnings: list[str] = []
        repair_actions: list[str] = []

        with self._connection() as connection:
            conv = connection.execute(
                """
                SELECT context_state_json, context_state_version,
                       context_projection_version, context_last_applied_turn_id,
                       context_last_applied_turn_index, context_projection_status,
                       memory_summary, memory_summary_turn_index
                FROM conversations
                WHERE owner_key = ? AND session_id = ?
                """,
                (owner_key, session_id),
            ).fetchone()
            latest = connection.execute(
                """
                SELECT id, turn_index
                FROM conversation_turns
                WHERE owner_key = ? AND session_id = ?
                ORDER BY turn_index DESC LIMIT 1
                """,
                (owner_key, session_id),
            ).fetchone()
            turn_rows = self._load_turn_window(connection, owner_key, session_id)

        latest_turn_id = int(latest["id"]) if latest is not None else None
        latest_turn_index = int(latest["turn_index"]) if latest is not None else -1
        turn_window = [self._turn_window_item(row) for row in turn_rows]

        projection: AgentContextState | None = None
        projection_version = 0
        last_applied_turn_id: int | None = None
        last_applied_turn_index = -1
        projection_status = "missing"
        rolling_summary = ""
        rolling_summary_turn_index = -1
        source = "fresh"

        if conv is not None:
            projection_version = int(conv["context_projection_version"] or 0)
            raw_last_id = conv["context_last_applied_turn_id"]
            last_applied_turn_id = int(raw_last_id) if raw_last_id is not None else None
            raw_last_index = conv["context_last_applied_turn_index"]
            last_applied_turn_index = int(raw_last_index) if raw_last_index is not None else -1
            projection_status = str(conv["context_projection_status"] or "missing")
            rolling_summary = str(conv["memory_summary"] or "")
            raw_summary_index = conv["memory_summary_turn_index"]
            rolling_summary_turn_index = (
                int(raw_summary_index) if raw_summary_index is not None else -1
            )
            if conv["context_state_json"]:
                try:
                    projection = projection_from_dict(
                        json.loads(str(conv["context_state_json"])),
                        owner_key=owner_key,
                        session_id=session_id,
                    )
                    source = "sqlite_projection"
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"projection_corrupt:{type(exc).__name__}")
                    projection_status = "corrupt"
                    repair_actions.append("rebuild_from_turns")

        if projection is not None and projection_status == "ready":
            cached = await self._load_committed_cache(
                owner_key,
                session_id,
                expected_version=projection_version,
                expected_last_turn_id=last_applied_turn_id,
                expected_last_turn_index=last_applied_turn_index,
                expected_status=projection_status,
                warnings=warnings,
            )
            if cached is not None:
                try:
                    projection = projection_from_dict(
                        cached["projection"],
                        owner_key=owner_key,
                        session_id=session_id,
                    )
                    source = "redis_committed"
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"redis_cache_corrupt:{type(exc).__name__}")

        needs_full_rebuild = projection is None or projection_status in {
            "missing",
            "disabled",
            "corrupt",
        }
        is_behind = latest_turn_index > last_applied_turn_index
        if latest is not None and (needs_full_rebuild or is_behind):
            start_after = -1 if needs_full_rebuild else last_applied_turn_index
            with self._connection() as connection:
                reducer_rows = self._load_reducer_turns(
                    connection,
                    owner_key,
                    session_id,
                    start_after=start_after,
                )
            if needs_full_rebuild:
                projection = rebuild_projection_from_turns(
                    owner_key=owner_key,
                    session_id=session_id,
                    turns=reducer_rows,
                )
                source = "turns_rebuild"
                repair_actions.append("rebuild_projection_from_turns")
            else:
                assert projection is not None
                apply_committed_turns(projection, reducer_rows)
                repair_actions.append("reduce_missing_committed_turns")
            if is_behind:
                warnings.append("projection_behind_turns")
        elif projection is not None and latest is None and projection_status != "ready":
            # Snapshot-only rows are not trusted as committed evidence.
            projection = AgentContextState(owner_key=owner_key, session_id=session_id)
            source = "fresh"
            warnings.append("untrusted_projection_without_turns")
            repair_actions.append("ignore_snapshot_without_turns")

        if projection is None:
            projection = AgentContextState(owner_key=owner_key, session_id=session_id)

        refs = self._merge_attachment_indexes(projection, turn_window)
        projection.owner_key = owner_key
        projection.session_id = session_id
        projection.identity.owner_key = owner_key
        projection.identity.session_id = session_id

        watermark = ContextWatermark(
            schema_version=PROJECTION_SCHEMA_V2,
            projection_version=projection_version,
            state_version=int(projection.version or 0),
            latest_turn_id=latest_turn_id,
            latest_turn_index=latest_turn_index,
            last_applied_turn_id=last_applied_turn_id,
            last_applied_turn_index=last_applied_turn_index,
            projection_status=projection_status,  # type: ignore[arg-type]
        )
        return ContextEnvelope(
            scope=ContextScope(owner_key=owner_key, session_id=session_id),
            watermark=watermark,
            projection=projection,
            turn_window=turn_window,
            rolling_summary=rolling_summary,
            rolling_summary_turn_index=rolling_summary_turn_index,
            active_attachment_refs=refs,
            source=source,  # type: ignore[arg-type]
            warnings=warnings,
            repair_actions=repair_actions,
        )

    async def commit_completed_turn(self, commit: CompletedTurnCommit) -> CommitResult:
        """Insert one immutable turn and update its projection in one transaction."""

        _validate_scope(commit.owner_key, commit.session_id)
        if not commit.commit_id.strip():
            raise ValueError("commit_id is required")
        if commit.projection is not None:
            if commit.projection.owner_key not in {"", commit.owner_key}:
                raise ValueError("projection owner scope mismatch")
            if commit.projection.session_id not in {"", commit.session_id}:
                raise ValueError("projection session scope mismatch")

        warnings: list[str] = []
        projection_enabled = bool(self.settings.db_snapshot_enabled)
        compact_payload = (
            projection_to_dict(commit.projection)
            if projection_enabled and commit.projection is not None
            else None
        )
        timestamp = utc_now()
        projection_version: int | None = None
        projection_status = "ready" if projection_enabled else "disabled"
        turn_id: int | None = None
        turn_index: int | None = None

        with self._connection(begin_immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT id, turn_index FROM conversation_turns
                WHERE owner_key = ? AND session_id = ? AND commit_id = ?
                """,
                (commit.owner_key, commit.session_id, commit.commit_id),
            ).fetchone()
            if existing is not None:
                row = connection.execute(
                    """
                    SELECT context_projection_version, context_projection_status
                    FROM conversations WHERE owner_key = ? AND session_id = ?
                    """,
                    (commit.owner_key, commit.session_id),
                ).fetchone()
                return CommitResult(
                    committed=True,
                    turn_id=int(existing["id"]),
                    turn_index=int(existing["turn_index"]),
                    projection_version=(
                        int(row["context_projection_version"] or 0) if row is not None else None
                    ),
                    idempotent_replay=True,
                    projection_status=(
                        str(row["context_projection_status"] or projection_status)
                        if row is not None
                        else projection_status
                    ),  # type: ignore[arg-type]
                    warnings=warnings,
                )

            max_row = connection.execute(
                """
                SELECT COALESCE(MAX(turn_index), -1) AS max_idx
                FROM conversation_turns WHERE owner_key = ? AND session_id = ?
                """,
                (commit.owner_key, commit.session_id),
            ).fetchone()
            turn_index = int(max_row["max_idx"]) + 1
            cursor = connection.execute(
                """
                INSERT INTO conversation_turns (
                    owner_key, session_id, turn_index, user_message, user_context,
                    attachment_refs_json, assistant_answer, route, case_id,
                    events_json, created_at, commit_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    commit.owner_key,
                    commit.session_id,
                    turn_index,
                    commit.user_message,
                    commit.user_context,
                    json.dumps(commit.attachment_refs or [], ensure_ascii=False),
                    commit.assistant_answer,
                    commit.route,
                    commit.case_id,
                    json.dumps(commit.events or [], ensure_ascii=False),
                    timestamp,
                    commit.commit_id,
                ),
            )
            turn_id = int(cursor.lastrowid)

            connection.execute(
                """
                INSERT INTO conversations (
                    owner_key, session_id, title, created_at, updated_at,
                    memory_summary, memory_summary_turn_index,
                    context_projection_version, context_last_applied_turn_index,
                    context_projection_status
                ) VALUES (?, ?, ?, ?, ?, '', -1, 0, -1, 'missing')
                ON CONFLICT(owner_key, session_id) DO UPDATE SET
                    updated_at = excluded.updated_at
                """,
                (
                    commit.owner_key,
                    commit.session_id,
                    _title_from_message(commit.title_hint or commit.user_message),
                    timestamp,
                    timestamp,
                ),
            )

            current = connection.execute(
                """
                SELECT context_state_json, context_projection_version,
                       context_last_applied_turn_index, context_projection_status
                FROM conversations WHERE owner_key = ? AND session_id = ?
                """,
                (commit.owner_key, commit.session_id),
            ).fetchone()
            if projection_enabled:
                if compact_payload is None:
                    state: AgentContextState | None = None
                    start_after = -1
                    if current["context_state_json"] and str(
                        current["context_projection_status"] or ""
                    ) in {"ready", "stale"}:
                        try:
                            state = projection_from_dict(
                                json.loads(str(current["context_state_json"])),
                                owner_key=commit.owner_key,
                                session_id=commit.session_id,
                            )
                            start_after = int(
                                current["context_last_applied_turn_index"]
                                if current["context_last_applied_turn_index"] is not None
                                else -1
                            )
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(f"projection_rebuild_on_commit:{type(exc).__name__}")
                    reducer_rows = self._load_reducer_turns(
                        connection,
                        commit.owner_key,
                        commit.session_id,
                        start_after=start_after if state is not None else -1,
                    )
                    if state is None:
                        state = rebuild_projection_from_turns(
                            owner_key=commit.owner_key,
                            session_id=commit.session_id,
                            turns=reducer_rows,
                        )
                    else:
                        apply_committed_turns(state, reducer_rows)
                    compact_payload = projection_to_dict(state)

                projection_version = int(current["context_projection_version"] or 0) + 1
                connection.execute(
                    """
                    UPDATE conversations
                    SET context_state_json = ?, context_state_version = ?,
                        context_state_updated_at = ?, context_projection_version = ?,
                        context_last_applied_turn_id = ?,
                        context_last_applied_turn_index = ?,
                        context_projection_status = 'ready', updated_at = ?
                    WHERE owner_key = ? AND session_id = ?
                    """,
                    (
                        json.dumps(compact_payload, ensure_ascii=False),
                        PROJECTION_SCHEMA_V2,
                        timestamp,
                        projection_version,
                        turn_id,
                        turn_index,
                        timestamp,
                        commit.owner_key,
                        commit.session_id,
                    ),
                )
                projection_status = "ready"
            else:
                connection.execute(
                    """
                    UPDATE conversations
                    SET context_projection_status = 'disabled', updated_at = ?
                    WHERE owner_key = ? AND session_id = ?
                    """,
                    (timestamp, commit.owner_key, commit.session_id),
                )
                projection_status = "disabled"

        if projection_enabled and compact_payload is not None and projection_version is not None:
            await self._write_committed_cache(
                owner_key=commit.owner_key,
                session_id=commit.session_id,
                projection_version=projection_version,
                last_applied_turn_id=turn_id,
                last_applied_turn_index=int(turn_index if turn_index is not None else -1),
                projection_status=projection_status,
                projection=compact_payload,
                warnings=warnings,
            )

        return CommitResult(
            committed=True,
            turn_id=turn_id,
            turn_index=turn_index,
            projection_version=projection_version,
            idempotent_replay=False,
            projection_status=projection_status,  # type: ignore[arg-type]
            warnings=warnings,
        )

    async def save_inflight(
        self,
        *,
        owner_key: str,
        session_id: str,
        run_id: str,
        state: AgentContextState,
        base_projection_version: int = 0,
    ) -> list[str]:
        """Save a coalesced recovery projection; normal loads never read it."""

        _validate_scope(owner_key, session_id)
        if not run_id.strip():
            raise ValueError("run_id is required")
        if state.owner_key not in {"", owner_key} or state.session_id not in {"", session_id}:
            raise ValueError("inflight projection scope mismatch")
        warnings: list[str] = []
        if not self.settings.redis_enabled or self._redis_get_set is None:
            return warnings
        key = _inflight_redis_key(self.settings.redis_namespace, owner_key, session_id, run_id)
        version = int(state.version or 0)
        try:
            existing_raw = await self._redis_get_set("get", key, None, None)
            if existing_raw:
                existing = json.loads(existing_raw)
                if (
                    isinstance(existing, dict)
                    and existing.get("owner_key") == owner_key
                    and existing.get("session_id") == session_id
                    and existing.get("run_id") == run_id
                    and int(existing.get("base_projection_version") or 0)
                    == int(base_projection_version)
                    and int(existing.get("state_version") or 0) >= version
                ):
                    return warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"inflight_read_failed:{type(exc).__name__}")

        payload = {
            "schema_version": PROJECTION_SCHEMA_V2,
            "kind": "inflight",
            "owner_key": owner_key,
            "session_id": session_id,
            "run_id": run_id,
            "base_projection_version": int(base_projection_version),
            "state_version": version,
            "projection": projection_to_dict(state),
        }
        try:
            await self._redis_get_set(
                "set",
                key,
                json.dumps(payload, ensure_ascii=False),
                self.settings.inflight_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"inflight_write_failed:{type(exc).__name__}")
            logger.warning(f"context inflight redis write failed: {exc}")
        return warnings

    async def load_inflight(
        self,
        *,
        owner_key: str,
        session_id: str,
        run_id: str,
        expected_base_projection_version: int,
        expected_state_version: int | None = None,
    ) -> tuple[AgentContextState | None, list[str]]:
        """Explicit checkpoint-only inflight read with scope/version checks."""

        _validate_scope(owner_key, session_id)
        warnings: list[str] = []
        if not self.settings.redis_enabled or self._redis_get_set is None:
            return None, warnings
        key = _inflight_redis_key(self.settings.redis_namespace, owner_key, session_id, run_id)
        try:
            raw = await self._redis_get_set("get", key, None, None)
        except Exception as exc:  # noqa: BLE001
            return None, [f"inflight_read_failed:{type(exc).__name__}"]
        if not raw:
            return None, ["inflight_missing"]
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("inflight payload is not an object")
            if (
                payload.get("kind") != "inflight"
                or payload.get("owner_key") != owner_key
                or payload.get("session_id") != session_id
                or payload.get("run_id") != run_id
            ):
                return None, ["inflight_scope_mismatch"]
            if int(payload.get("base_projection_version") or 0) != int(
                expected_base_projection_version
            ):
                return None, ["inflight_base_version_mismatch"]
            state_version = int(payload.get("state_version") or 0)
            if expected_state_version is not None and state_version != int(expected_state_version):
                return None, ["inflight_state_version_mismatch"]
            state = projection_from_dict(
                payload.get("projection") or {},
                owner_key=owner_key,
                session_id=session_id,
            )
            if int(state.version or 0) != state_version:
                return None, ["inflight_projection_version_mismatch"]
            return state, warnings
        except Exception as exc:  # noqa: BLE001
            return None, [f"inflight_corrupt:{type(exc).__name__}"]

    async def delete_inflight(
        self,
        *,
        owner_key: str,
        session_id: str,
        run_id: str,
    ) -> list[str]:
        warnings: list[str] = []
        if not self.settings.redis_enabled or self._redis_get_set is None or not run_id:
            return warnings
        key = _inflight_redis_key(self.settings.redis_namespace, owner_key, session_id, run_id)
        try:
            await self._redis_get_set("delete", key, None, None)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"inflight_delete_failed:{type(exc).__name__}")
            logger.warning(f"context inflight redis delete failed: {exc}")
        return warnings

    def _load_turn_window(
        self,
        connection: sqlite3.Connection,
        owner_key: str,
        session_id: str,
    ) -> list[sqlite3.Row]:
        limit = max(0, int(self.settings.history_max_turns or 0))
        if limit == 0:
            return []
        rows = connection.execute(
            """
            SELECT id, turn_index, user_message, user_context, attachment_refs_json,
                   assistant_answer, route, case_id, created_at, commit_id
            FROM conversation_turns
            WHERE owner_key = ? AND session_id = ?
            ORDER BY turn_index DESC LIMIT ?
            """,
            (owner_key, session_id, limit),
        ).fetchall()
        return list(reversed(rows))

    @staticmethod
    def _load_reducer_turns(
        connection: sqlite3.Connection,
        owner_key: str,
        session_id: str,
        *,
        start_after: int,
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT id, turn_index, user_message, attachment_refs_json,
                   assistant_answer, route, case_id, events_json, created_at
            FROM conversation_turns
            WHERE owner_key = ? AND session_id = ? AND turn_index > ?
            ORDER BY turn_index ASC
            """,
            (owner_key, session_id, int(start_after)),
        ).fetchall()

    @staticmethod
    def _turn_window_item(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "turn_index": int(row["turn_index"]),
            "user_message": str(row["user_message"] or ""),
            "user_context": str(row["user_context"] or ""),
            "attachment_refs": json_loads(row["attachment_refs_json"], []),
            "assistant_answer": str(row["assistant_answer"] or ""),
            "route": str(row["route"] or ""),
            "case_id": str(row["case_id"] or ""),
            "created_at": str(row["created_at"] or ""),
            "commit_id": str(row["commit_id"] or ""),
        }

    @staticmethod
    def _merge_attachment_indexes(
        projection: AgentContextState,
        turn_window: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_file: dict[str, dict[str, Any]] = {}
        for item in projection.conversation.active_attachment_refs or []:
            if isinstance(item, dict) and item.get("file_id"):
                by_file[str(item["file_id"])] = dict(item)
        for turn in turn_window:
            for raw in turn.get("attachment_refs") or []:
                if not isinstance(raw, dict) or not raw.get("file_id"):
                    continue
                item = dict(raw)
                item.setdefault("source_turn_id", turn["id"])
                item.setdefault("source_turn_index", turn["turn_index"])
                by_file[str(item["file_id"])] = item
        return list(by_file.values())[-20:]

    async def _load_committed_cache(
        self,
        owner_key: str,
        session_id: str,
        *,
        expected_version: int,
        expected_last_turn_id: int | None,
        expected_last_turn_index: int,
        expected_status: str,
        warnings: list[str],
    ) -> dict[str, Any] | None:
        if not self.settings.redis_enabled or self._redis_get_set is None:
            return None
        key = _committed_redis_key(self.settings.redis_namespace, owner_key, session_id)
        try:
            raw = await self._redis_get_set("get", key, None, None)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"redis_read_failed:{type(exc).__name__}")
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            warnings.append("redis_cache_corrupt")
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("projection"), dict):
            warnings.append("redis_cache_corrupt")
            return None
        if payload.get("owner_key") != owner_key or payload.get("session_id") != session_id:
            warnings.append("redis_cache_scope_mismatch")
            return None
        actual = (
            int(payload.get("projection_version") or 0),
            payload.get("last_applied_turn_id"),
            int(payload.get("last_applied_turn_index") or 0),
            str(payload.get("projection_status") or ""),
        )
        expected = (
            int(expected_version),
            expected_last_turn_id,
            int(expected_last_turn_index),
            expected_status,
        )
        if actual != expected:
            warnings.append("redis_cache_stale")
            return None
        return payload

    async def _write_committed_cache(
        self,
        *,
        owner_key: str,
        session_id: str,
        projection_version: int,
        last_applied_turn_id: int | None,
        last_applied_turn_index: int,
        projection_status: str,
        projection: dict[str, Any],
        warnings: list[str],
    ) -> None:
        if not self.settings.redis_enabled or self._redis_get_set is None:
            return
        payload = {
            "schema_version": PROJECTION_SCHEMA_V2,
            "kind": "committed",
            "owner_key": owner_key,
            "session_id": session_id,
            "projection_version": projection_version,
            "last_applied_turn_id": last_applied_turn_id,
            "last_applied_turn_index": last_applied_turn_index,
            "projection_status": projection_status,
            "projection": projection,
        }
        key = _committed_redis_key(self.settings.redis_namespace, owner_key, session_id)
        try:
            await self._redis_get_set(
                "set",
                key,
                json.dumps(payload, ensure_ascii=False),
                self.settings.redis_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"redis_write_failed:{type(exc).__name__}")
            logger.warning(f"context committed redis write failed: {exc}")

    def _require_schema(self) -> None:
        if self._schema_verified:
            return
        from app.services.database_migration_service import DatabaseMigrationService

        DatabaseMigrationService(self.db_path).require_current()
        self._schema_verified = True

    @contextmanager
    def _connection(self, *, begin_immediate: bool = False) -> Iterator[sqlite3.Connection]:
        self._require_schema()
        connection = sqlite3.connect(str(self.db_path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            if begin_immediate:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def build_default_context_repository(
    *,
    redis_get_set: RedisGetSet | None = None,
) -> ContextRepository:
    if redis_get_set is None:

        async def _redis_get_set(op: str, key: str, value: Any, ttl: Any) -> Any:
            from app.services.redis_client import get_redis_client

            client = await get_redis_client()
            if op == "get":
                return await client.get(key)
            if op == "set":
                return await client.set(key, value, ex=ttl)
            if op == "delete":
                return await client.delete(key)
            raise ValueError(op)

        redis_get_set = _redis_get_set
    return ContextRepository(redis_get_set=redis_get_set)


context_repository = ContextRepository()

__all__ = [
    "ContextRepository",
    "ContextRepositorySettings",
    "build_default_context_repository",
    "context_repository",
]
