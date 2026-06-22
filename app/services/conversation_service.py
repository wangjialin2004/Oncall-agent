"""Conversation persistence for multi-turn chat history.

Stores the user-visible conversation (question + answer + the run's
route/case/timeline) per ``(owner_key, session_id)`` in a plain SQLite table.
This is deliberately independent of the LangGraph checkpointer: the checkpointer
is unused on the active ``/api/assistant`` path, so we persist turns directly,
mirroring the ``UserPreferenceService`` / ``ServiceKnowledgeService`` pattern.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import config
from app.utils.serialization import json_loads as _json_loads
from app.utils.time import utc_now as _utc_now

_TITLE_MAX_LEN = 40


class ConversationService:
    """Persist and read back chat turns per owner/session."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or config.memory_db_path)
        self._initialized = False

    def append_turn(
        self,
        *,
        owner_key: str,
        session_id: str,
        user_message: str,
        assistant_answer: str,
        route: str = "",
        case_id: str = "",
        events: list[dict[str, Any]] | None = None,
    ) -> int:
        """Append one user/assistant turn; create or refresh the conversation row.

        Returns the 0-based ``turn_index`` of the inserted turn.
        """
        timestamp = _utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(turn_index), -1) AS max_idx FROM conversation_turns "
                "WHERE owner_key = ? AND session_id = ?",
                (owner_key, session_id),
            ).fetchone()
            turn_index = int(row["max_idx"]) + 1
            connection.execute(
                """
                INSERT INTO conversation_turns (
                    owner_key, session_id, turn_index, user_message,
                    assistant_answer, route, case_id, events_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_key,
                    session_id,
                    turn_index,
                    user_message,
                    assistant_answer,
                    route,
                    case_id,
                    json.dumps(events or [], ensure_ascii=False),
                    timestamp,
                ),
            )
            # Title is set on first insert and kept stable afterwards (only bump
            # updated_at), so the sidebar shows the conversation's opening question.
            connection.execute(
                """
                INSERT INTO conversations (owner_key, session_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_key, session_id) DO UPDATE SET
                    updated_at = excluded.updated_at
                """,
                (owner_key, session_id, _make_title(user_message), timestamp, timestamp),
            )
        return turn_index

    def list_sessions(self, owner_key: str) -> list[dict[str, Any]]:
        """Return the owner's conversations, most-recently-updated first."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT c.session_id, c.title, c.created_at, c.updated_at,
                       COUNT(t.id) AS turn_count
                FROM conversations c
                LEFT JOIN conversation_turns t
                    ON t.owner_key = c.owner_key AND t.session_id = c.session_id
                WHERE c.owner_key = ?
                GROUP BY c.session_id
                ORDER BY c.updated_at DESC
                """,
                (owner_key,),
            ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "turn_count": int(row["turn_count"]),
            }
            for row in rows
        ]

    def get_turns(self, owner_key: str, session_id: str) -> list[dict[str, Any]]:
        """Return all turns of a session in chronological order."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT turn_index, user_message, assistant_answer, route,
                       case_id, events_json, created_at
                FROM conversation_turns
                WHERE owner_key = ? AND session_id = ?
                ORDER BY turn_index ASC
                """,
                (owner_key, session_id),
            ).fetchall()
        return [
            {
                "turn_index": int(row["turn_index"]),
                "user_message": row["user_message"],
                "assistant_answer": row["assistant_answer"],
                "route": row["route"],
                "case_id": row["case_id"],
                "events": _json_loads(row["events_json"], []),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def delete_session(self, owner_key: str, session_id: str) -> bool:
        """Delete a conversation and its turns. Returns True if it existed."""
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM conversation_turns WHERE owner_key = ? AND session_id = ?",
                (owner_key, session_id),
            )
            cursor = connection.execute(
                "DELETE FROM conversations WHERE owner_key = ? AND session_id = ?",
                (owner_key, session_id),
            )
        return cursor.rowcount > 0

    def _ensure_database(self) -> None:
        if self._initialized:
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
                    PRIMARY KEY (owner_key, session_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    user_message TEXT NOT NULL DEFAULT '',
                    assistant_answer TEXT NOT NULL DEFAULT '',
                    route TEXT NOT NULL DEFAULT '',
                    case_id TEXT NOT NULL DEFAULT '',
                    events_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_turns_session "
                "ON conversation_turns (owner_key, session_id, turn_index)"
            )
        self._initialized = True

    @contextmanager
    def _connection(self):
        self._ensure_database()
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def _make_title(user_message: str) -> str:
    text = " ".join((user_message or "").split())
    if not text:
        return "新会话"
    return text[:_TITLE_MAX_LEN]


conversation_service = ConversationService()
