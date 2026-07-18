"""Persist compact :class:`AgentContextState` snapshots to SQLite.

This is the cold-backup layer behind the Redis hot store (see plan
``plan/2026-07-08-stateful-agent-context.md`` §5.2). Snapshots are *compact*:

- the seven block dataclasses are serialized via :func:`state_to_dict`.
- raw tool result payloads are NOT stored — only ``raw_ref`` pointers.
- ``patch_tail`` is dropped beyond ``history_limit`` (so DB row size is bounded).

We piggy-back on the same SQLite file as ``conversation_service`` rather than
opening a new file: the conversation DB is the natural per-tenant store and we
already enforce an explicit column-add migration there. Schema changes here
are limited to two new columns on ``conversations``:

- ``context_state_json`` (TEXT) — last persisted :class:`AgentContextState`.
- ``context_state_version`` (INTEGER) — schema version that wrote it.

If you later decide to split this out into its own database, only
:func:`save_context_snapshot` and :func:`get_latest_context_snapshot` need to
swap their DB connection — the public API stays the same.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from app.agent.context.persistence import state_from_dict, state_to_dict
from app.agent.context.state import AgentContextState, SCHEMA_VERSION
from app.config import config
from app.utils.serialization import json_loads
from app.utils.time import utc_now

_MAX_PATCH_TAIL = 50  # rows are bounded; older audit frames dropped


class ContextSnapshotUnavailable(Exception):
    """Raised when the persisted snapshot can't be used (schema mismatch / corrupt)."""


class ContextSnapshotService:
    """Save / load compact context snapshots.

    Bound to the same SQLite file as :class:`app.services.conversation_service`.
    Snapshots are scoped to ``(owner_key, session_id)`` and a single row per
    pair — we keep only the most recent snapshot, not a history.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or config.memory_db_path)
        self._initialized = False

    # ----- public API -----

    def save_context_snapshot(
        self,
        *,
        owner_key: str,
        session_id: str,
        state: AgentContextState,
    ) -> None:
        """Persist a compact snapshot. Errors are swallowed by callers — see plan §2.3."""
        payload = state_to_dict(state)
        # Bound the audit trail before serializing so row size stays predictable.
        if len(payload.get("patch_tail", [])) > _MAX_PATCH_TAIL:
            payload["patch_tail"] = payload["patch_tail"][-_MAX_PATCH_TAIL:]
        payload_json = json.dumps(payload, ensure_ascii=False)
        timestamp = utc_now()
        with self._connection() as connection:
            _ensure_conversation_row(
                connection,
                owner_key=owner_key,
                session_id=session_id,
                title="",
                timestamp=timestamp,
            )
            connection.execute(
                """
                UPDATE conversations
                SET context_state_json = ?,
                    context_state_version = ?,
                    context_state_updated_at = ?,
                    updated_at = ?
                WHERE owner_key = ? AND session_id = ?
                """,
                (payload_json, SCHEMA_VERSION, timestamp, timestamp,
                 owner_key, session_id),
            )

    def get_latest_context_snapshot(
        self, owner_key: str, session_id: str,
    ) -> AgentContextState | None:
        """Return the latest snapshot, or None if missing/unreadable.

        Raises :class:`ContextSnapshotUnavailable` when a row exists but the
        stored schema_version is incompatible with the running code — the
        caller (store.get_or_rebuild) should treat that as a miss and rebuild
        from turns.
        """
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT context_state_json, context_state_version
                FROM conversations
                WHERE owner_key = ? AND session_id = ?
                """,
                (owner_key, session_id),
            ).fetchone()
        if row is None or not row["context_state_json"]:
            return None
        try:
            stored_version = int(row["context_state_version"] or 0)
        except (TypeError, ValueError):
            stored_version = 0
        if stored_version != SCHEMA_VERSION:
            raise ContextSnapshotUnavailable(
                f"stored snapshot schema_version={stored_version} "
                f"!= runtime schema_version={SCHEMA_VERSION}"
            )
        try:
            payload = json.loads(row["context_state_json"])
        except json.JSONDecodeError as exc:
            raise ContextSnapshotUnavailable(
                f"snapshot row is corrupt: {exc}"
            ) from exc
        return state_from_dict(payload)

    def clear_context_snapshot(
        self, owner_key: str, session_id: str,
    ) -> None:
        """Remove the snapshot — used by debug tooling."""
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE conversations
                SET context_state_json = NULL,
                    context_state_version = NULL,
                    context_state_updated_at = NULL
                WHERE owner_key = ? AND session_id = ?
                """,
                (owner_key, session_id),
            )

    # ----- internals -----

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_database()
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_database(self) -> None:
        if self._initialized:
            return
        if bool(getattr(config, "db_schema_enforcement_enabled", False)):
            from app.services.database_migration_service import DatabaseMigrationService

            DatabaseMigrationService(self.db_path).require_current()
            self._initialized = True
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    owner_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    memory_summary TEXT NOT NULL DEFAULT '',
                    memory_summary_turn_index INTEGER NOT NULL DEFAULT -1,
                    PRIMARY KEY (owner_key, session_id)
                )
                """
            )
            for col, definition in (
                ("context_state_json", "TEXT"),
                ("context_state_version", "INTEGER"),
                ("context_state_updated_at", "TEXT"),
            ):
                _ensure_column(
                    connection, table="conversations", column=col, definition=definition,
                )
        self._initialized = True


def _ensure_column(
    connection: sqlite3.Connection,
    *,
    table: str,
    column: str,
    definition: str,
) -> None:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    if any(row[1] == column for row in rows):
        return
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_conversation_row(
    connection: sqlite3.Connection,
    *,
    owner_key: str,
    session_id: str,
    title: str,
    timestamp: str,
) -> None:
    """Make sure a conversations row exists before UPDATE (no-op if present)."""
    connection.execute(
        """
        INSERT INTO conversations (
            owner_key, session_id, title, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(owner_key, session_id) DO NOTHING
        """,
        (owner_key, session_id, title, timestamp, timestamp),
    )


# Module-level singleton, mirroring ``conversation_service``. Tests construct
# their own instance bound to a tmp path.
context_snapshot_service = ContextSnapshotService()


__all__ = [
    "ContextSnapshotService",
    "ContextSnapshotUnavailable",
    "context_snapshot_service",
]
