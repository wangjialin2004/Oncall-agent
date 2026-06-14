"""SQLite-backed diagnosis case and tool evidence memory."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import config


class DiagnosisMemoryService:
    """Persist diagnosis cases and tool evidence in SQLite."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or config.diagnosis_memory_db_path)
        self._initialized = False

    def create_case(
        self,
        session_id: str,
        user_input: str,
        case_id: str | None = None,
    ) -> str:
        case_id = case_id or f"case-{uuid.uuid4().hex}"
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO diagnosis_cases (
                    case_id, session_id, user_input, status, plan_json,
                    executed_steps_json, final_report, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (case_id, session_id, user_input, "running", "[]", "[]", "", now, now),
            )
        return case_id

    def update_case_plan(self, case_id: str, plan: list[str]) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE diagnosis_cases
                SET plan_json = ?, updated_at = ?
                WHERE case_id = ?
                """,
                (_json_dumps(plan), _utc_now(), case_id),
            )

    def complete_case(
        self,
        case_id: str,
        executed_steps: list[Any],
        final_report: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE diagnosis_cases
                SET status = ?, executed_steps_json = ?, final_report = ?, updated_at = ?
                WHERE case_id = ?
                """,
                (
                    "completed",
                    _json_dumps(executed_steps),
                    final_report,
                    _utc_now(),
                    case_id,
                ),
            )

    def fail_case(self, case_id: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE diagnosis_cases
                SET status = ?, final_report = ?, updated_at = ?
                WHERE case_id = ?
                """,
                ("failed", error, _utc_now(), case_id),
            )

    def record_tool_evidence(
        self,
        case_id: str,
        session_id: str,
        evidence_records: list[dict[str, Any]],
    ) -> None:
        if not evidence_records:
            return

        now = _utc_now()
        rows = [
            (
                case_id,
                session_id,
                record.get("tool_name") or "unknown_tool",
                record.get("tool_call_id"),
                record.get("evidence_id") or "",
                record.get("source") or "",
                1 if record.get("success") else 0,
                record.get("duration_ms"),
                record.get("summary") or "",
                _json_dumps(record.get("arguments") or {}),
                record.get("raw_result") or "",
                now,
            )
            for record in evidence_records
        ]
        with self._connection() as connection:
            connection.executemany(
                """
                INSERT INTO tool_evidence (
                    case_id, session_id, tool_name, tool_call_id, evidence_id,
                    source, success, duration_ms, summary, arguments_json,
                    raw_result, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT case_id, session_id, user_input, status, plan_json,
                       executed_steps_json, final_report
                FROM diagnosis_cases
                WHERE case_id = ?
                """,
                (case_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "case_id": row["case_id"],
            "session_id": row["session_id"],
            "user_input": row["user_input"],
            "status": row["status"],
            "plan": _json_loads(row["plan_json"], []),
            "executed_steps": _json_loads(row["executed_steps_json"], []),
            "final_report": row["final_report"],
        }

    def list_tool_evidence(self, case_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT tool_name, tool_call_id, evidence_id, source, success,
                       duration_ms, summary, arguments_json, raw_result
                FROM tool_evidence
                WHERE case_id = ?
                ORDER BY id
                """,
                (case_id,),
            ).fetchall()

        return [
            {
                "tool_name": row["tool_name"],
                "tool_call_id": row["tool_call_id"],
                "evidence_id": row["evidence_id"],
                "source": row["source"],
                "success": bool(row["success"]),
                "duration_ms": row["duration_ms"],
                "summary": row["summary"],
                "arguments": _json_loads(row["arguments_json"], {}),
                "raw_result": row["raw_result"],
            }
            for row in rows
        ]

    def record_feedback(
        self,
        case_id: str,
        session_id: str,
        user_accepted: bool,
        actual_root_cause: str = "",
        final_resolution: str = "",
        comment: str = "",
    ) -> str:
        feedback_id = f"feedback-{uuid.uuid4().hex}"
        with self._connection() as connection:
            if not self._case_exists(connection, case_id):
                raise ValueError(f"Diagnosis case not found: {case_id}")

            connection.execute(
                """
                INSERT INTO diagnosis_feedback (
                    feedback_id, case_id, session_id, user_accepted, actual_root_cause,
                    final_resolution, comment, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    case_id,
                    session_id,
                    1 if user_accepted else 0,
                    actual_root_cause,
                    final_resolution,
                    comment,
                    _utc_now(),
                ),
            )
        return feedback_id

    def list_feedback(self, case_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if not self._case_exists(connection, case_id):
                raise ValueError(f"Diagnosis case not found: {case_id}")

            rows = connection.execute(
                """
                SELECT case_id, session_id, user_accepted, actual_root_cause,
                       final_resolution, comment
                FROM diagnosis_feedback
                WHERE case_id = ?
                ORDER BY id
                """,
                (case_id,),
            ).fetchall()

        return [
            {
                "case_id": row["case_id"],
                "session_id": row["session_id"],
                "user_accepted": bool(row["user_accepted"]),
                "actual_root_cause": row["actual_root_cause"],
                "final_resolution": row["final_resolution"],
                "comment": row["comment"],
            }
            for row in rows
        ]

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_database()
        connection = self._open_connection()
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _open_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _case_exists(self, connection: sqlite3.Connection, case_id: str) -> bool:
        row = connection.execute(
            """
            SELECT 1
            FROM diagnosis_cases
            WHERE case_id = ?
            """,
            (case_id,),
        ).fetchone()
        return row is not None

    def _ensure_database(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._open_connection()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnosis_cases (
                    case_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    executed_steps_json TEXT NOT NULL,
                    final_report TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_call_id TEXT,
                    evidence_id TEXT,
                    source TEXT,
                    success INTEGER NOT NULL,
                    duration_ms REAL,
                    summary TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    raw_result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES diagnosis_cases(case_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_evidence_case_id
                ON tool_evidence(case_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnosis_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    feedback_id TEXT,
                    case_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_accepted INTEGER NOT NULL,
                    actual_root_cause TEXT NOT NULL,
                    final_resolution TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES diagnosis_cases(case_id)
                )
                """
            )
            feedback_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(diagnosis_feedback)")
            }
            if "feedback_id" not in feedback_columns:
                connection.execute("ALTER TABLE diagnosis_feedback ADD COLUMN feedback_id TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_diagnosis_feedback_case_id
                ON diagnosis_feedback(case_id)
                """
            )
            connection.commit()
            self._initialized = True
        finally:
            connection.close()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


diagnosis_memory_service = DiagnosisMemoryService()
