"""SQLite-backed long-term experience memory store."""

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


class ExperienceMemoryService:
    """Persist reusable diagnosis experience memories in SQLite."""

    def __init__(self, db_path: str | Path | None = None, index_service: Any | None = None):
        self.db_path = Path(db_path or config.experience_memory_db_path)
        self.index_service = index_service
        self._initialized = False

    def create_memory(
        self,
        project_id: str,
        environment: str,
        service_name: str,
        symptoms: str,
        root_cause: str,
        resolution: str,
        evidence_summary: str,
        source_case_id: str,
        source_feedback_id: str,
        confidence: float,
        milvus_pk: str | None = None,
        *,
        experience_id: str | None = None,
    ) -> str:
        experience_id = experience_id or f"exp-{uuid.uuid4().hex}"
        milvus_pk = milvus_pk or experience_id
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO experience_memories (
                    experience_id, milvus_pk, project_id, environment, service_name,
                    symptoms, root_cause, resolution, evidence_summary,
                    source_case_ids_json, source_feedback_ids_json, confidence,
                    enabled, hit_count, success_count, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experience_id,
                    milvus_pk,
                    project_id,
                    environment,
                    service_name,
                    symptoms,
                    root_cause,
                    resolution,
                    evidence_summary,
                    _json_dumps([source_case_id]),
                    _json_dumps([source_feedback_id]),
                    confidence,
                    1,
                    0,
                    0,
                    now,
                    now,
                ),
            )
        return experience_id

    def get_memory(self, experience_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT experience_id, milvus_pk, project_id, environment, service_name,
                       symptoms, root_cause, resolution, evidence_summary,
                       source_case_ids_json, source_feedback_ids_json, confidence,
                       enabled, hit_count, success_count, created_at, updated_at
                FROM experience_memories
                WHERE experience_id = ?
                """,
                (experience_id,),
            ).fetchone()
        if row is None:
            return None
        return _memory_from_row(row)

    def list_memories(
        self,
        project_id: str | None = None,
        enabled: bool | None = None,
        service_name: str | None = None,
        min_confidence: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where_clauses: list[str] = []
        params: list[Any] = []

        if project_id is not None:
            where_clauses.append("project_id = ?")
            params.append(project_id)
        if enabled is not None:
            where_clauses.append("enabled = ?")
            params.append(1 if enabled else 0)
        if service_name is not None:
            where_clauses.append("service_name = ?")
            params.append(service_name)
        if min_confidence is not None:
            where_clauses.append("confidence >= ?")
            params.append(min_confidence)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        params.extend([limit, offset])

        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT experience_id, milvus_pk, project_id, environment, service_name,
                       symptoms, root_cause, resolution, evidence_summary,
                       source_case_ids_json, source_feedback_ids_json, confidence,
                       enabled, hit_count, success_count, created_at, updated_at
                FROM experience_memories
                {where_sql}
                ORDER BY updated_at DESC, experience_id ASC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()

        return [_memory_from_row(row) for row in rows]

    def set_enabled(self, experience_id: str, enabled: bool) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE experience_memories
                SET enabled = ?, updated_at = ?
                WHERE experience_id = ?
                """,
                (1 if enabled else 0, _utc_now(), experience_id),
            )
        return cursor.rowcount > 0

    def increment_hit_count(self, experience_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE experience_memories
                SET hit_count = hit_count + 1, updated_at = ?
                WHERE experience_id = ?
                """,
                (_utc_now(), experience_id),
            )

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

    def _ensure_database(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._open_connection()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experience_memories (
                    experience_id TEXT PRIMARY KEY,
                    milvus_pk TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    symptoms TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    evidence_summary TEXT NOT NULL,
                    source_case_ids_json TEXT NOT NULL,
                    source_feedback_ids_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    enabled INTEGER NOT NULL,
                    hit_count INTEGER NOT NULL,
                    success_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experience_memories_filters
                ON experience_memories(project_id, enabled, service_name, confidence)
                """
            )
            connection.commit()
            self._initialized = True
        finally:
            connection.close()


def _memory_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "experience_id": row["experience_id"],
        "milvus_pk": row["milvus_pk"],
        "project_id": row["project_id"],
        "environment": row["environment"],
        "service_name": row["service_name"],
        "symptoms": row["symptoms"],
        "root_cause": row["root_cause"],
        "resolution": row["resolution"],
        "evidence_summary": row["evidence_summary"],
        "source_case_ids": _json_loads(row["source_case_ids_json"], []),
        "source_feedback_ids": _json_loads(row["source_feedback_ids_json"], []),
        "confidence": row["confidence"],
        "enabled": bool(row["enabled"]),
        "hit_count": row["hit_count"],
        "success_count": row["success_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


experience_memory_service = ExperienceMemoryService()
