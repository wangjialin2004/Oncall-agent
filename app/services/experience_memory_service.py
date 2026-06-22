"""SQLite-backed long-term diagnosis experience memory."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import config
from app.services.memory_safety import redact_memory_text
from app.utils.serialization import json_dumps as _json_dumps, json_loads as _json_loads
from app.utils.text import normalize_text as _normalize, text_similarity as _text_similarity
from app.utils.time import utc_now as _utc_now


class ExperienceMemoryService:
    """Persist governed long-term diagnosis experience cards."""

    def __init__(self, db_path: str | Path | None = None, index_service: Any | None = None):
        self.db_path = Path(db_path or config.memory_db_path)
        self.index_service = index_service
        self._initialized = False

    def create_from_feedback(
        self,
        *,
        project_id: str,
        session_id: str,
        user_message: str,
        assistant_answer: str = "",
        user_accepted: bool,
        actual_root_cause: str = "",
        final_resolution: str = "",
        environment: str = "",
        service_name: str = "",
        events: list[dict[str, Any]] | None = None,
        source_feedback_id: str = "",
    ) -> str:
        if not user_accepted:
            return ""
        return self._create_memory(
            project_id=project_id,
            environment=environment,
            service_name=service_name,
            symptoms=_build_symptoms(user_message, assistant_answer),
            root_cause=actual_root_cause or _fallback_root_cause(assistant_answer),
            resolution=final_resolution,
            evidence_summary=_distill_events(events or []),
            source_type="feedback",
            source_session_id=session_id,
            source_feedback_id=source_feedback_id,
            source_event_ids=_event_ids(events or []),
            confidence=float(config.experience_memory_high_confidence),
        )

    def create_weak_acceptance(
        self,
        *,
        project_id: str,
        session_id: str,
        user_message: str,
        assistant_answer: str = "",
        environment: str = "",
        service_name: str = "",
        events: list[dict[str, Any]] | None = None,
    ) -> str:
        return self._create_memory(
            project_id=project_id,
            environment=environment,
            service_name=service_name,
            symptoms=_build_symptoms(user_message, assistant_answer),
            root_cause=_fallback_root_cause(assistant_answer),
            resolution="",
            evidence_summary=_distill_events(events or []),
            source_type="weak_acceptance",
            source_session_id=session_id,
            source_feedback_id="",
            source_event_ids=_event_ids(events or []),
            confidence=float(config.experience_memory_weak_confidence),
        )

    def create_manual(
        self,
        *,
        project_id: str,
        symptoms: str,
        root_cause: str,
        resolution: str,
        evidence_summary: str = "",
        environment: str = "",
        service_name: str = "",
        confidence: float = 0.8,
    ) -> str:
        return self._create_memory(
            project_id=project_id,
            environment=environment,
            service_name=service_name,
            symptoms=symptoms,
            root_cause=root_cause,
            resolution=resolution,
            evidence_summary=evidence_summary,
            source_type="manual",
            source_session_id="",
            source_feedback_id="",
            source_event_ids=[],
            confidence=confidence,
        )

    def recall(
        self,
        *,
        query: str,
        project_id: str,
        top_k: int = 3,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        if not self.index_service:
            return self._recall_from_sqlite(query=query, project_id=project_id, top_k=top_k, session_id=session_id)
        try:
            candidates = self.index_service.recall(
                query=query, project_id=project_id, top_k=top_k, session_id=session_id
            )
        except TypeError:
            candidates = self.index_service.recall(query=query, project_id=project_id, top_k=top_k)
        except Exception as exc:
            logger.warning(f"experience recall skipped: {exc}")
            return self._recall_from_sqlite(query=query, project_id=project_id, top_k=top_k, session_id=session_id)
        if not candidates:
            return self._recall_from_sqlite(query=query, project_id=project_id, top_k=top_k, session_id=session_id)

        memories: list[dict[str, Any]] = []
        for item in candidates:
            memory_id = str(item.get("experience_id") or item.get("id") or "")
            memory = self.get(memory_id) if memory_id else None
            if not memory or memory["project_id"] != project_id or not memory["enabled"]:
                continue
            memory["similarity"] = float(item.get("similarity", item.get("score", 0)))
            memory["conflict_count"] = self._count_conflicts(memory)
            memories.append(memory)
            self._increment_hit(memory["experience_id"], session_id=session_id)
        if not memories:
            return self._recall_from_sqlite(query=query, project_id=project_id, top_k=top_k, session_id=session_id)
        memories.sort(key=lambda item: (item.get("confidence", 0), item.get("similarity", 0)), reverse=True)
        return memories[:top_k]

    def get(self, experience_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM experience_memories WHERE experience_id = ?",
                (experience_id,),
            ).fetchone()
        return _memory_from_row(row) if row else None

    def list(
        self,
        *,
        project_id: str,
        enabled: bool | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id = ?"]
        params: list[Any] = [project_id]
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(1 if enabled else 0)
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM experience_memories
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_memory_from_row(row) for row in rows]

    def update(
        self,
        experience_id: str,
        *,
        enabled: bool | None = None,
        confidence: float | None = None,
    ) -> bool:
        updates: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if confidence is not None:
            updates.append("confidence = ?")
            params.append(confidence)
        if not updates:
            return self.get(experience_id) is not None
        updates.append("updated_at = ?")
        params.extend([_utc_now(), experience_id])
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE experience_memories SET {', '.join(updates)} WHERE experience_id = ?",
                params,
            )
        updated = cursor.rowcount > 0
        if updated:
            self._sync_index_after_update(experience_id, enabled=enabled)
        return updated

    def rebuild_index(self, *, project_id: str | None = None) -> int:
        if not self.index_service:
            return 0
        memories = self.list(project_id=project_id or config.project_id, enabled=True, limit=1000)
        try:
            return int(self.index_service.rebuild(memories))
        except Exception as exc:
            logger.warning(f"experience index rebuild failed: {exc}")
            return 0

    def _create_memory(
        self,
        *,
        project_id: str,
        environment: str,
        service_name: str,
        symptoms: str,
        root_cause: str,
        resolution: str,
        evidence_summary: str,
        source_type: str,
        source_session_id: str,
        source_feedback_id: str,
        source_event_ids: list[str],
        confidence: float,
    ) -> str:
        target = self._find_merge_target(
            project_id=project_id, symptoms=symptoms, root_cause=root_cause
        )
        if target is not None:
            return self._merge_into(
                target,
                root_cause=root_cause,
                resolution=resolution,
                evidence_summary=evidence_summary,
                source_type=source_type,
                source_event_ids=source_event_ids,
                confidence=confidence,
            )
        memory = {
            "experience_id": f"exp-{uuid.uuid4().hex}",
            "project_id": project_id,
            "environment": environment or "",
            "service_name": service_name or "",
            "symptoms": symptoms.strip(),
            "root_cause": redact_memory_text(root_cause.strip()),
            "resolution": redact_memory_text(resolution.strip()),
            "evidence_summary": redact_memory_text(evidence_summary.strip()),
            "source_type": source_type,
            "source_session_id": source_session_id,
            "source_feedback_id": source_feedback_id,
            "source_event_ids": source_event_ids,
            "confidence": float(confidence),
            "milvus_pk": "",
        }
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO experience_memories (
                    experience_id, project_id, environment, service_name,
                    symptoms, root_cause, resolution, evidence_summary,
                    source_type, source_session_id, source_feedback_id,
                    source_event_ids_json, confidence, hit_count, success_count,
                    enabled, milvus_pk, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 1, ?, ?, ?)
                """,
                (
                    memory["experience_id"],
                    memory["project_id"],
                    memory["environment"],
                    memory["service_name"],
                    memory["symptoms"],
                    memory["root_cause"],
                    memory["resolution"],
                    memory["evidence_summary"],
                    memory["source_type"],
                    memory["source_session_id"],
                    memory["source_feedback_id"],
                    _json_dumps(memory["source_event_ids"]),
                    memory["confidence"],
                    memory["milvus_pk"],
                    now,
                    now,
                ),
            )
        self._upsert_index(memory)
        return str(memory["experience_id"])

    def _find_merge_target(
        self, *, project_id: str, symptoms: str, root_cause: str
    ) -> dict[str, Any] | None:
        """Return an existing card to merge into when symptoms match and the root
        cause is compatible; ``None`` means insert a fresh card (incl. same-symptom
        but conflicting-root-cause cases, which are kept separate by design)."""

        threshold = float(config.experience_memory_similarity_threshold)
        if threshold > 1.0:  # threshold above 1 disables merging entirely
            return None
        new_symptoms = _normalize(symptoms)
        if not new_symptoms:
            return None
        best: dict[str, Any] | None = None
        best_score = 0.0
        for candidate in self.list(project_id=project_id, enabled=True, limit=1000):
            score = _text_similarity(new_symptoms, _normalize(candidate["symptoms"]))
            if score >= threshold and score > best_score:
                best, best_score = candidate, score
        if best is None:
            return None
        if _root_cause_compatible(root_cause, best["root_cause"], threshold):
            return best
        return None

    def _merge_into(
        self,
        target: dict[str, Any],
        *,
        root_cause: str,
        resolution: str,
        evidence_summary: str,
        source_type: str,
        source_event_ids: list[str],
        confidence: float,
    ) -> str:
        merged_event_ids = list(dict.fromkeys([*target["source_event_ids"], *source_event_ids]))
        merged_evidence = _merge_text(
            target["evidence_summary"], redact_memory_text(evidence_summary.strip())
        )
        new_root_cause = target["root_cause"]
        if _is_placeholder_root_cause(target["root_cause"]) and root_cause.strip():
            new_root_cause = redact_memory_text(root_cause.strip())
        new_resolution = target["resolution"] or redact_memory_text(resolution.strip())
        new_confidence = max(float(target["confidence"]), float(confidence))
        success_bump = 1 if source_type in {"feedback", "weak_acceptance"} else 0
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE experience_memories
                SET root_cause = ?, resolution = ?, evidence_summary = ?,
                    source_event_ids_json = ?, confidence = ?,
                    success_count = success_count + ?, updated_at = ?
                WHERE experience_id = ?
                """,
                (
                    new_root_cause,
                    new_resolution,
                    merged_evidence,
                    _json_dumps(merged_event_ids),
                    new_confidence,
                    success_bump,
                    now,
                    target["experience_id"],
                ),
            )
        updated = self.get(target["experience_id"])
        if updated:
            self._upsert_index(updated)
        return str(target["experience_id"])

    def _upsert_index(self, memory: dict[str, Any]) -> None:
        if not self.index_service:
            return
        try:
            self.index_service.upsert(memory)
        except Exception as exc:
            logger.warning(f"experience index upsert failed: {exc}")

    def _sync_index_after_update(self, experience_id: str, *, enabled: bool | None = None) -> None:
        if not self.index_service:
            return
        try:
            if enabled is False and hasattr(self.index_service, "disable"):
                self.index_service.disable(experience_id)
                return
            memory = self.get(experience_id)
            if memory:
                self.index_service.upsert(memory)
        except Exception as exc:
            logger.warning(f"experience index update sync failed: {exc}")

    def _increment_hit(self, experience_id: str, *, session_id: str = "") -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE experience_memories
                SET hit_count = hit_count + 1, updated_at = ?
                WHERE experience_id = ?
                """,
                (_utc_now(), experience_id),
            )

    def _recall_from_sqlite(
        self,
        *,
        query: str,
        project_id: str,
        top_k: int,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        normalized_query = _normalize(query)
        if not normalized_query:
            return []
        memories = []
        for memory in self.list(project_id=project_id, enabled=True, limit=1000):
            similarity = _text_similarity(normalized_query, _normalize(memory["symptoms"]))
            if similarity <= 0:
                continue
            memory["similarity"] = similarity
            memory["conflict_count"] = self._count_conflicts(memory)
            memories.append(memory)
        memories.sort(key=lambda item: (item.get("similarity", 0), item.get("confidence", 0)), reverse=True)
        selected = memories[:top_k]
        for memory in selected:
            self._increment_hit(memory["experience_id"], session_id=session_id)
        return selected

    def _count_conflicts(self, memory: dict[str, Any]) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM experience_memories
                WHERE project_id = ?
                  AND enabled = 1
                  AND symptoms = ?
                  AND root_cause != ?
                """,
                (memory["project_id"], memory["symptoms"], memory["root_cause"]),
            ).fetchone()
        return int(row["count"] if row else 0)

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
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experience_memories (
                    experience_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    environment TEXT NOT NULL DEFAULT '',
                    service_name TEXT NOT NULL DEFAULT '',
                    symptoms TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    evidence_summary TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_session_id TEXT NOT NULL DEFAULT '',
                    source_feedback_id TEXT NOT NULL DEFAULT '',
                    source_event_ids_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    milvus_pk TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experience_project_enabled
                ON experience_memories(project_id, enabled)
                """
            )
            connection.commit()
            self._initialized = True
        finally:
            connection.close()


_PLACEHOLDER_ROOT_CAUSE = "用户反馈确认但未填写根因"


def _is_placeholder_root_cause(root_cause: str) -> bool:
    normalized = _normalize(root_cause)
    return not normalized or normalized == _normalize(_PLACEHOLDER_ROOT_CAUSE)


def _root_cause_compatible(new_root_cause: str, existing_root_cause: str, threshold: float) -> bool:
    """Same symptoms with a *different* confirmed root cause must stay separate
    (conflicting experience). Empty/placeholder root causes never conflict."""

    left = _normalize(new_root_cause)
    right = _normalize(existing_root_cause)
    if _is_placeholder_root_cause(new_root_cause) or _is_placeholder_root_cause(existing_root_cause):
        return True
    if _text_similarity(left, right) >= threshold:
        return True
    # Containment handles abbreviation vs. full phrasing (e.g. "db pool exhausted"
    # vs "database connection pool exhausted"); guarded by a min length.
    return min(len(left), len(right)) >= 6 and (left in right or right in left)


def _merge_text(existing: str, incoming: str) -> str:
    if not incoming:
        return existing
    if not existing:
        return incoming
    existing_lines = existing.split("\n")
    seen = set(existing_lines)
    merged = list(existing_lines)
    for line in incoming.split("\n"):
        if line and line not in seen:
            merged.append(line)
            seen.add(line)
    return "\n".join(merged)


def _build_symptoms(user_message: str, assistant_answer: str) -> str:
    parts = [user_message.strip()]
    answer = assistant_answer.strip()
    if answer:
        parts.append(answer[:500])
    return "\n".join(part for part in parts if part)


def _fallback_root_cause(answer: str) -> str:
    return answer.strip()[:300] or _PLACEHOLDER_ROOT_CAUSE


def _distill_events(events: list[dict[str, Any]]) -> str:
    lines = []
    for event in events:
        if event.get("type") != "tool_event":
            continue
        tool = event.get("tool") or event.get("agent") or "tool"
        evidence_id = event.get("evidence_id") or ""
        summary = event.get("summary") or ""
        lines.append(f"{tool} {evidence_id}: {summary}".strip())
    return "\n".join(lines) or "无结构化工具证据，来自用户反馈确认。"


def _event_ids(events: list[dict[str, Any]]) -> list[str]:
    ids = []
    for event in events:
        evidence_id = event.get("evidence_id")
        if evidence_id:
            ids.append(str(evidence_id))
    return ids


def _memory_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "experience_id": row["experience_id"],
        "project_id": row["project_id"],
        "environment": row["environment"],
        "service_name": row["service_name"],
        "symptoms": row["symptoms"],
        "root_cause": row["root_cause"],
        "resolution": row["resolution"],
        "evidence_summary": row["evidence_summary"],
        "source_type": row["source_type"],
        "source_session_id": row["source_session_id"],
        "source_feedback_id": row["source_feedback_id"],
        "source_event_ids": _json_loads(row["source_event_ids_json"], []),
        "confidence": float(row["confidence"]),
        "hit_count": int(row["hit_count"]),
        "success_count": int(row["success_count"]),
        "enabled": bool(row["enabled"]),
        "milvus_pk": row["milvus_pk"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


from app.services.experience_memory_index_service import experience_memory_index_service


experience_memory_service = ExperienceMemoryService(index_service=experience_memory_index_service)
