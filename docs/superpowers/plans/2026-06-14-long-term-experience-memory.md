# Long-Term Experience Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a governed long-term experience memory that writes accepted diagnosis feedback into SQLite and Milvus, then lets the AIOps Planner recall similar historical incidents.

**Architecture:** Add a focused `ExperienceMemoryService` as the SQLite authority and a small Milvus index adapter for semantic recall. Feedback writes call the service after `DiagnosisMemoryService.record_feedback`, Planner reads relevant memories before LLM planning, and a new memory API exposes list/detail/disable/rebuild operations.

**Tech Stack:** FastAPI, Pydantic Settings, SQLite, pymilvus, DashScope embeddings through the existing `vector_embedding_service`, pytest, monkeypatch-based unit tests.

---

## File Structure

- Create `app/services/experience_memory_service.py`
  - Owns SQLite schema for `experience_memories`.
  - Generates rule-based experience cards from diagnosis case/evidence/feedback.
  - Creates or merges memories.
  - Reads/list/details/enables/disables memories.
  - Calls the index adapter but treats SQLite as authoritative.

- Create `app/services/experience_memory_index_service.py`
  - Owns Milvus `experience_memory` collection access.
  - Embeds `symptoms`.
  - Searches with `project_id`, `enabled`, and `memory_type` filters.
  - Upserts, disables, and rebuilds index records.
  - Is small and easy to fake in tests.

- Modify `app/services/diagnosis_memory_service.py`
  - Return a feedback identifier from `record_feedback`.
  - Preserve current feedback API response behavior.

- Modify `app/config.py`
  - Add `project_id`, `experience_memory_collection`, thresholds, and top-k config.

- Modify `app/api/aiops.py`
  - After accepted feedback is saved, call long-term memory creation.
  - Log and suppress memory errors so feedback persistence remains primary.

- Modify `app/agent/aiops/planner.py`
  - Retrieve relevant experiences before planning.
  - Inject formatted experience context.
  - Instruct Planner to verify high-confidence historical root causes first.

- Create `app/api/memory.py`
  - List, detail, enable/disable, and rebuild memory index.

- Modify `app/main.py`
  - Register `memory.router`.

- Create `app/models/memory.py`
  - Pydantic response/update models for governance API.

- Add and update tests:
  - Create `tests/test_experience_memory_service.py`.
  - Create `tests/test_experience_memory_api.py`.
  - Update `tests/test_aiops_feedback_api.py`.
  - Update or create `tests/test_aiops_experience_planner.py`.

## Task 1: Configuration And SQLite Memory Store

**Files:**
- Modify: `app/config.py`
- Create: `app/services/experience_memory_service.py`
- Test: `tests/test_experience_memory_service.py`

- [ ] **Step 1: Write failing tests for schema, create, get, list, disable, and hit count**

Add this file:

```python
from app.services.experience_memory_service import ExperienceMemoryService


def test_experience_memory_service_creates_and_reads_memory(tmp_path):
    service = ExperienceMemoryService(db_path=tmp_path / "experience.sqlite3")

    memory_id = service.create_memory(
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
        symptoms="FastAPI latency increased with Milvus timeout logs",
        root_cause="Milvus connection pool exhausted",
        resolution="Restart Milvus and reuse client connections",
        evidence_summary="tool=search_app_logs evidence_id=cls-1 timeout observed",
        source_case_id="case-1",
        source_feedback_id="feedback-1",
        confidence=0.8,
    )

    memory = service.get_memory(memory_id)

    assert memory is not None
    assert memory["experience_id"] == memory_id
    assert memory["project_id"] == "super_biz_agent"
    assert memory["enabled"] is True
    assert memory["source_case_ids"] == ["case-1"]
    assert memory["source_feedback_ids"] == ["feedback-1"]
    assert memory["hit_count"] == 0
    assert memory["success_count"] == 0


def test_experience_memory_service_lists_filters_and_disables(tmp_path):
    service = ExperienceMemoryService(db_path=tmp_path / "experience.sqlite3")
    enabled_id = service.create_memory(
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
        symptoms="Milvus timeout",
        root_cause="connection pool exhausted",
        resolution="reuse client",
        evidence_summary="evidence cls-1",
        source_case_id="case-1",
        source_feedback_id="feedback-1",
        confidence=0.8,
    )
    disabled_id = service.create_memory(
        project_id="other_project",
        environment="prod",
        service_name="api",
        symptoms="API disk full",
        root_cause="disk saturation",
        resolution="expand disk",
        evidence_summary="evidence metric-2",
        source_case_id="case-2",
        source_feedback_id="feedback-2",
        confidence=0.8,
    )

    service.set_enabled(disabled_id, enabled=False)
    service.increment_hit_count(enabled_id)

    results = service.list_memories(project_id="super_biz_agent", enabled=True)

    assert [item["experience_id"] for item in results] == [enabled_id]
    assert results[0]["hit_count"] == 1
    assert service.get_memory(disabled_id)["enabled"] is False
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_experience_memory_service.py -v
```

Expected: FAIL with `ModuleNotFoundError` or missing `ExperienceMemoryService`.

- [ ] **Step 3: Add config fields**

In `app/config.py`, add these fields to `Settings` after the diagnosis memory settings:

```python
    # Long-term experience memory
    project_id: str = "super_biz_agent"
    experience_memory_collection: str = "experience_memory"
    experience_memory_top_k: int = 3
    experience_memory_similarity_threshold: float = 0.78
    experience_memory_high_confidence_threshold: float = 0.75
    experience_memory_initial_confidence: float = 0.8
```

- [ ] **Step 4: Implement the SQLite service core**

Create `app/services/experience_memory_service.py` with:

```python
"""SQLite-backed long-term diagnosis experience memory."""

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
    """Persist governed long-term diagnosis experience records."""

    def __init__(self, db_path: str | Path | None = None, index_service: Any | None = None):
        self.db_path = Path(db_path or config.diagnosis_memory_db_path)
        self.index_service = index_service
        self._initialized = False

    def create_memory(
        self,
        *,
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
        experience_id: str | None = None,
        milvus_pk: str | None = None,
    ) -> str:
        experience_id = experience_id or f"exp-{uuid.uuid4().hex}"
        now = _utc_now()
        source_case_ids = [source_case_id]
        source_feedback_ids = [source_feedback_id]
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO experience_memories (
                    experience_id, project_id, environment, service_name,
                    symptoms, root_cause, resolution, evidence_summary,
                    source_case_ids_json, source_feedback_ids_json,
                    confidence, hit_count, success_count, enabled,
                    milvus_pk, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 1, ?, ?, ?)
                """,
                (
                    experience_id,
                    project_id,
                    environment,
                    service_name,
                    symptoms,
                    root_cause,
                    resolution,
                    evidence_summary,
                    _json_dumps(source_case_ids),
                    _json_dumps(source_feedback_ids),
                    confidence,
                    milvus_pk or experience_id,
                    now,
                    now,
                ),
            )
        return experience_id

    def get_memory(self, experience_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
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
        *,
        project_id: str | None = None,
        enabled: bool | None = None,
        service_name: str | None = None,
        min_confidence: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(1 if enabled else 0)
        if service_name is not None:
            clauses.append("service_name = ?")
            params.append(service_name)
        if min_confidence is not None:
            clauses.append("confidence >= ?")
            params.append(min_confidence)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM experience_memories
                {where_sql}
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [_memory_from_row(row) for row in rows]

    def set_enabled(self, experience_id: str, *, enabled: bool) -> bool:
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
                    environment TEXT,
                    service_name TEXT,
                    symptoms TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    evidence_summary TEXT NOT NULL,
                    source_case_ids_json TEXT NOT NULL,
                    source_feedback_ids_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    milvus_pk TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experience_memories_project_enabled
                ON experience_memories(project_id, enabled)
                """
            )
            connection.commit()
            self._initialized = True
        finally:
            connection.close()


def _memory_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "experience_id": row["experience_id"],
        "project_id": row["project_id"],
        "environment": row["environment"] or "",
        "service_name": row["service_name"] or "",
        "symptoms": row["symptoms"],
        "root_cause": row["root_cause"],
        "resolution": row["resolution"],
        "evidence_summary": row["evidence_summary"],
        "source_case_ids": _json_loads(row["source_case_ids_json"], []),
        "source_feedback_ids": _json_loads(row["source_feedback_ids_json"], []),
        "confidence": float(row["confidence"]),
        "hit_count": int(row["hit_count"]),
        "success_count": int(row["success_count"]),
        "enabled": bool(row["enabled"]),
        "milvus_pk": row["milvus_pk"] or "",
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
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_experience_memory_service.py -v
```

Expected: PASS for both tests.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add app/config.py app/services/experience_memory_service.py tests/test_experience_memory_service.py
git commit -m "feat: add experience memory sqlite store"
```

## Task 2: Feedback ID And Experience Card Creation

**Files:**
- Modify: `app/services/diagnosis_memory_service.py`
- Modify: `app/services/experience_memory_service.py`
- Test: `tests/test_diagnosis_memory_service.py`
- Test: `tests/test_experience_memory_service.py`

- [ ] **Step 1: Write failing tests for feedback ID and card generation**

Add to `tests/test_diagnosis_memory_service.py`:

```python
def test_diagnosis_memory_service_record_feedback_returns_feedback_id(tmp_path):
    db_path = tmp_path / "diagnosis-memory.sqlite3"
    service = DiagnosisMemoryService(db_path)
    service.create_case(session_id="session-1", user_input="diagnose", case_id="case-1")

    feedback_id = service.record_feedback(
        case_id="case-1",
        session_id="session-1",
        user_accepted=True,
        actual_root_cause="Milvus connection exhausted",
        final_resolution="Restarted Milvus",
    )

    assert isinstance(feedback_id, str)
    assert feedback_id.startswith("feedback-")
```

Add to `tests/test_experience_memory_service.py`:

```python
from app.services.diagnosis_memory_service import DiagnosisMemoryService


class _NoopIndex:
    def __init__(self):
        self.upserts = []

    def find_similar(self, *, query, project_id, top_k):
        return []

    def upsert_memory(self, memory):
        self.upserts.append(memory)
        return memory["experience_id"]


def test_create_or_merge_from_feedback_builds_rule_based_card(tmp_path):
    diagnosis = DiagnosisMemoryService(tmp_path / "diagnosis.sqlite3")
    case_id = diagnosis.create_case(
        session_id="session-1",
        user_input="diagnose Milvus timeout and slow API",
        case_id="case-1",
    )
    diagnosis.record_tool_evidence(
        case_id=case_id,
        session_id="session-1",
        evidence_records=[
            {
                "tool_name": "search_app_logs",
                "tool_call_id": "call-1",
                "evidence_id": "cls-1",
                "source": "local_logs",
                "success": True,
                "duration_ms": 5,
                "summary": "Milvus connection timeout repeated",
                "arguments": {"keyword": "timeout"},
                "raw_result": "timeout",
            }
        ],
    )
    diagnosis.complete_case(
        case_id,
        executed_steps=[("inspect logs", "timeout found")],
        final_report="Root cause: Milvus connection exhausted. Resolution: restart Milvus.",
    )
    feedback_id = diagnosis.record_feedback(
        case_id=case_id,
        session_id="session-1",
        user_accepted=True,
        actual_root_cause="Milvus connection exhausted",
        final_resolution="Restarted Milvus and reused client connections",
    )
    index = _NoopIndex()
    service = ExperienceMemoryService(
        db_path=tmp_path / "experience.sqlite3",
        diagnosis_memory_service=diagnosis,
        index_service=index,
    )

    experience_id = service.create_or_merge_from_feedback(
        case_id=case_id,
        feedback_id=feedback_id,
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
    )

    memory = service.get_memory(experience_id)
    assert memory["symptoms"].startswith("diagnose Milvus timeout")
    assert memory["root_cause"] == "Milvus connection exhausted"
    assert memory["resolution"] == "Restarted Milvus and reused client connections"
    assert "search_app_logs" in memory["evidence_summary"]
    assert "cls-1" in memory["evidence_summary"]
    assert index.upserts[0]["experience_id"] == experience_id
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_diagnosis_memory_service.py::test_diagnosis_memory_service_record_feedback_returns_feedback_id tests/test_experience_memory_service.py::test_create_or_merge_from_feedback_builds_rule_based_card -v
```

Expected: FAIL because `record_feedback` returns `None` and `create_or_merge_from_feedback` is missing.

- [ ] **Step 3: Return feedback IDs from diagnosis feedback**

In `app/services/diagnosis_memory_service.py`, update `record_feedback` to create and return a stable ID. Use the existing table by adding a nullable `feedback_id` column with a migration-compatible `ALTER TABLE` guarded by current schema inspection.

Add helper inside `_ensure_database` after creating `diagnosis_feedback`:

```python
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(diagnosis_feedback)").fetchall()
            }
            if "feedback_id" not in columns:
                connection.execute("ALTER TABLE diagnosis_feedback ADD COLUMN feedback_id TEXT")
```

Update `record_feedback`:

```python
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
                    feedback_id, case_id, session_id, user_accepted,
                    actual_root_cause, final_resolution, comment, created_at
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
```

Keep `list_feedback` response fields unchanged so current API tests continue to pass.

- [ ] **Step 4: Extend `ExperienceMemoryService` constructor and card creation**

Modify `ExperienceMemoryService.__init__`:

```python
    def __init__(
        self,
        db_path: str | Path | None = None,
        diagnosis_memory_service: Any | None = None,
        index_service: Any | None = None,
    ):
        from app.services.diagnosis_memory_service import diagnosis_memory_service as default_diagnosis

        self.db_path = Path(db_path or config.diagnosis_memory_db_path)
        self.diagnosis_memory_service = diagnosis_memory_service or default_diagnosis
        self.index_service = index_service
        self._initialized = False
```

Add card and write method:

```python
    def create_or_merge_from_feedback(
        self,
        *,
        case_id: str,
        feedback_id: str,
        project_id: str,
        environment: str = "",
        service_name: str = "",
    ) -> str:
        case = self.diagnosis_memory_service.get_case(case_id)
        if case is None:
            raise ValueError(f"Diagnosis case not found: {case_id}")
        feedback_items = self.diagnosis_memory_service.list_feedback(case_id)
        feedback = next(
            (
                item
                for item in reversed(feedback_items)
                if item["user_accepted"]
                and (item.get("actual_root_cause") or item.get("final_resolution"))
            ),
            feedback_items[-1] if feedback_items else None,
        )
        if feedback is None:
            raise ValueError(f"Diagnosis feedback not found for case: {case_id}")

        evidence = self.diagnosis_memory_service.list_tool_evidence(case_id)
        card = self._build_card(case=case, feedback=feedback, evidence=evidence)
        similar = []
        if self.index_service is not None:
            similar = self.index_service.find_similar(
                query=card["symptoms"],
                project_id=project_id,
                top_k=config.experience_memory_top_k,
            )
        merge_target = self._select_merge_target(similar, card["root_cause"])
        if merge_target is not None:
            experience_id = self.merge_memory(
                merge_target["experience_id"],
                source_case_id=case_id,
                source_feedback_id=feedback_id,
                evidence_summary=card["evidence_summary"],
            )
        else:
            experience_id = self.create_memory(
                project_id=project_id,
                environment=environment,
                service_name=service_name,
                symptoms=card["symptoms"],
                root_cause=card["root_cause"],
                resolution=card["resolution"],
                evidence_summary=card["evidence_summary"],
                source_case_id=case_id,
                source_feedback_id=feedback_id,
                confidence=config.experience_memory_initial_confidence,
            )
        memory = self.get_memory(experience_id)
        if self.index_service is not None and memory is not None:
            self.index_service.upsert_memory(memory)
        return experience_id

    def _build_card(
        self,
        *,
        case: dict[str, Any],
        feedback: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> dict[str, str]:
        evidence_lines = [
            f"tool={item['tool_name']} evidence_id={item['evidence_id']} {item['summary']}"
            for item in evidence
        ]
        final_report = case.get("final_report", "")
        root_cause = feedback.get("actual_root_cause") or _extract_after_label(
            final_report,
            labels=("Root cause:", "根因:", "根因分析:"),
        )
        resolution = feedback.get("final_resolution") or _extract_after_label(
            final_report,
            labels=("Resolution:", "处理方案:", "处置方案:"),
        )
        symptoms_parts = [case.get("user_input", ""), final_report, *evidence_lines]
        return {
            "symptoms": _compact_text(" ".join(part for part in symptoms_parts if part)),
            "root_cause": root_cause or "Accepted diagnosis root cause was not specified",
            "resolution": resolution or "Accepted diagnosis resolution was not specified",
            "evidence_summary": _compact_text("; ".join(evidence_lines)),
        }
```

Add helpers:

```python
def _compact_text(value: str, *, max_length: int = 1600) -> str:
    normalized = " ".join(value.split())
    return normalized[:max_length]


def _extract_after_label(value: str, *, labels: tuple[str, ...]) -> str:
    for label in labels:
        if label in value:
            return value.split(label, 1)[1].splitlines()[0].strip()
    return ""
```

- [ ] **Step 5: Add merge method**

In `ExperienceMemoryService`, add:

```python
    def merge_memory(
        self,
        experience_id: str,
        *,
        source_case_id: str,
        source_feedback_id: str,
        evidence_summary: str,
    ) -> str:
        memory = self.get_memory(experience_id)
        if memory is None:
            raise ValueError(f"Experience memory not found: {experience_id}")
        source_case_ids = list(dict.fromkeys([*memory["source_case_ids"], source_case_id]))
        source_feedback_ids = list(
            dict.fromkeys([*memory["source_feedback_ids"], source_feedback_id])
        )
        merged_evidence = _compact_text(
            "; ".join(part for part in [memory["evidence_summary"], evidence_summary] if part)
        )
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE experience_memories
                SET source_case_ids_json = ?,
                    source_feedback_ids_json = ?,
                    evidence_summary = ?,
                    confidence = MAX(confidence, ?),
                    updated_at = ?
                WHERE experience_id = ?
                """,
                (
                    _json_dumps(source_case_ids),
                    _json_dumps(source_feedback_ids),
                    merged_evidence,
                    config.experience_memory_initial_confidence,
                    _utc_now(),
                    experience_id,
                ),
            )
        return experience_id

    def _select_merge_target(
        self,
        candidates: list[dict[str, Any]],
        root_cause: str,
    ) -> dict[str, Any] | None:
        normalized_root = root_cause.strip().lower()
        for candidate in candidates:
            if candidate.get("similarity", 0) < config.experience_memory_similarity_threshold:
                continue
            candidate_memory = self.get_memory(candidate["experience_id"])
            if candidate_memory is None:
                continue
            candidate_root = candidate_memory["root_cause"].strip().lower()
            if normalized_root and (
                normalized_root in candidate_root or candidate_root in normalized_root
            ):
                return candidate
        return None
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
pytest tests/test_diagnosis_memory_service.py::test_diagnosis_memory_service_record_feedback_returns_feedback_id tests/test_experience_memory_service.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

Run:

```bash
git add app/services/diagnosis_memory_service.py app/services/experience_memory_service.py tests/test_diagnosis_memory_service.py tests/test_experience_memory_service.py
git commit -m "feat: build experience memories from accepted feedback"
```

## Task 3: Milvus Experience Index Adapter

**Files:**
- Create: `app/services/experience_memory_index_service.py`
- Modify: `app/services/experience_memory_service.py`
- Test: `tests/test_experience_memory_service.py`

- [ ] **Step 1: Write failing tests for search filtering and rebuild behavior with a fake index**

Add to `tests/test_experience_memory_service.py`:

```python
class _SearchIndex:
    def __init__(self):
        self.search_calls = []
        self.disabled = []
        self.rebuilt = []

    def find_similar(self, *, query, project_id, top_k):
        self.search_calls.append({"query": query, "project_id": project_id, "top_k": top_k})
        return []

    def upsert_memory(self, memory):
        return memory["experience_id"]

    def disable_memory(self, experience_id):
        self.disabled.append(experience_id)

    def rebuild(self, memories):
        self.rebuilt.append(list(memories))
        return len(self.rebuilt[-1])


def test_search_relevant_experiences_uses_project_filter_and_increments_hits(tmp_path):
    index = _SearchIndex()
    service = ExperienceMemoryService(db_path=tmp_path / "experience.sqlite3", index_service=index)
    memory_id = service.create_memory(
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
        symptoms="Milvus timeout",
        root_cause="connection pool exhausted",
        resolution="reuse client",
        evidence_summary="evidence cls-1",
        source_case_id="case-1",
        source_feedback_id="feedback-1",
        confidence=0.8,
    )
    index.find_similar = lambda *, query, project_id, top_k: [
        {"experience_id": memory_id, "similarity": 0.9}
    ]

    results = service.search_relevant_experiences(
        query="API slow with Milvus timeout",
        project_id="super_biz_agent",
        top_k=3,
    )

    assert results[0]["experience_id"] == memory_id
    assert results[0]["similarity"] == 0.9
    assert service.get_memory(memory_id)["hit_count"] == 1


def test_disable_syncs_index_and_rebuild_indexes_enabled_memories(tmp_path):
    index = _SearchIndex()
    service = ExperienceMemoryService(db_path=tmp_path / "experience.sqlite3", index_service=index)
    enabled_id = service.create_memory(
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
        symptoms="Milvus timeout",
        root_cause="connection pool exhausted",
        resolution="reuse client",
        evidence_summary="evidence cls-1",
        source_case_id="case-1",
        source_feedback_id="feedback-1",
        confidence=0.8,
    )
    disabled_id = service.create_memory(
        project_id="super_biz_agent",
        environment="local",
        service_name="api",
        symptoms="API disk full",
        root_cause="disk saturation",
        resolution="expand disk",
        evidence_summary="evidence metric-1",
        source_case_id="case-2",
        source_feedback_id="feedback-2",
        confidence=0.8,
    )

    assert service.set_enabled(disabled_id, enabled=False) is True
    rebuilt_count = service.rebuild_index(project_id="super_biz_agent")

    assert index.disabled == [disabled_id]
    assert rebuilt_count == 1
    assert index.rebuilt[0][0]["experience_id"] == enabled_id
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_experience_memory_service.py::test_search_relevant_experiences_uses_project_filter_and_increments_hits tests/test_experience_memory_service.py::test_disable_syncs_index_and_rebuild_indexes_enabled_memories -v
```

Expected: FAIL because search and rebuild methods are missing.

- [ ] **Step 3: Implement service search, disable sync, and rebuild**

Update `ExperienceMemoryService.set_enabled` to call index on disable:

```python
    def set_enabled(self, experience_id: str, *, enabled: bool) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE experience_memories
                SET enabled = ?, updated_at = ?
                WHERE experience_id = ?
                """,
                (1 if enabled else 0, _utc_now(), experience_id),
            )
        changed = cursor.rowcount > 0
        if changed and self.index_service is not None and not enabled:
            self.index_service.disable_memory(experience_id)
        return changed
```

Add methods:

```python
    def search_relevant_experiences(
        self,
        *,
        query: str,
        project_id: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if self.index_service is None:
            return []
        candidates = self.index_service.find_similar(
            query=query,
            project_id=project_id,
            top_k=top_k,
        )
        memories: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate.get("similarity", 0) < config.experience_memory_similarity_threshold:
                continue
            memory = self.get_memory(candidate["experience_id"])
            if memory is None or not memory["enabled"]:
                continue
            if memory["confidence"] < config.experience_memory_high_confidence_threshold:
                continue
            memory["similarity"] = candidate["similarity"]
            self.increment_hit_count(memory["experience_id"])
            memories.append(memory)
        return memories

    def rebuild_index(self, *, project_id: str | None = None) -> int:
        if self.index_service is None:
            return 0
        memories = self.list_memories(project_id=project_id, enabled=True, limit=10000)
        return self.index_service.rebuild(memories)
```

- [ ] **Step 4: Implement Milvus adapter**

Create `app/services/experience_memory_index_service.py`:

```python
"""Milvus index adapter for long-term experience memory."""

from __future__ import annotations

from typing import Any

from loguru import logger
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

from app.config import config
from app.core.milvus_client import MilvusClientManager
from app.services.vector_embedding_service import vector_embedding_service

MEMORY_TYPE = "diagnosis_experience"
VECTOR_DIM = 1024
ID_MAX_LENGTH = 100
TEXT_MAX_LENGTH = 8000


class ExperienceMemoryIndexService:
    """Search and maintain the Milvus experience_memory collection."""

    def find_similar(self, *, query: str, project_id: str, top_k: int) -> list[dict[str, Any]]:
        try:
            query_vector = vector_embedding_service.embed_query(query)
            collection = self._collection()
            expr = (
                f'project_id == "{project_id}" '
                f'and enabled == true '
                f'and memory_type == "{MEMORY_TYPE}"'
            )
            results = collection.search(
                data=[query_vector],
                anns_field=config.rag_dense_vector_field,
                param={"metric_type": "L2", "params": {"nprobe": 10}},
                limit=top_k,
                expr=expr,
                output_fields=[
                    "experience_id",
                    "project_id",
                    "root_cause",
                    "resolution",
                    "confidence",
                    "enabled",
                ],
            )
            candidates: list[dict[str, Any]] = []
            for hits in results:
                for hit in hits:
                    candidates.append(
                        {
                            "experience_id": hit.entity.get("experience_id"),
                            "similarity": _distance_to_similarity(hit.distance),
                            "distance": hit.distance,
                        }
                    )
            return candidates
        except Exception as exc:
            logger.warning(f"experience memory search failed: {exc}")
            return []

    def upsert_memory(self, memory: dict[str, Any]) -> str:
        try:
            collection = self._collection()
            vector = vector_embedding_service.embed_query(memory["symptoms"])
            collection.upsert(
                [
                    [memory["experience_id"]],
                    [memory["experience_id"]],
                    [memory["project_id"]],
                    [memory["environment"]],
                    [memory["service_name"]],
                    [MEMORY_TYPE],
                    [memory["symptoms"]],
                    [memory["root_cause"]],
                    [memory["resolution"]],
                    [float(memory["confidence"])],
                    [bool(memory["enabled"])],
                    [_json_list(memory["source_case_ids"])],
                    [vector],
                ]
            )
            collection.flush()
        except Exception as exc:
            logger.warning(f"experience memory upsert failed: {exc}")
        return memory["experience_id"]

    def disable_memory(self, experience_id: str) -> None:
        try:
            collection = self._collection()
            collection.delete(expr=f'experience_id == "{experience_id}"')
            collection.flush()
        except Exception as exc:
            logger.warning(f"experience memory disable sync failed: {exc}")

    def rebuild(self, memories: list[dict[str, Any]]) -> int:
        count = 0
        for memory in memories:
            self.upsert_memory(memory)
            count += 1
        return count

    def _collection(self) -> Collection:
        MilvusClientManager().connect()
        collection_name = config.experience_memory_collection
        if not utility.has_collection(collection_name):
            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=ID_MAX_LENGTH, is_primary=True),
                FieldSchema(name="experience_id", dtype=DataType.VARCHAR, max_length=ID_MAX_LENGTH),
                FieldSchema(name="project_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="environment", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="service_name", dtype=DataType.VARCHAR, max_length=200),
                FieldSchema(name="memory_type", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="symptoms", dtype=DataType.VARCHAR, max_length=TEXT_MAX_LENGTH),
                FieldSchema(name="root_cause", dtype=DataType.VARCHAR, max_length=TEXT_MAX_LENGTH),
                FieldSchema(name="resolution", dtype=DataType.VARCHAR, max_length=TEXT_MAX_LENGTH),
                FieldSchema(name="confidence", dtype=DataType.FLOAT),
                FieldSchema(name="enabled", dtype=DataType.BOOL),
                FieldSchema(name="source_case_ids_json", dtype=DataType.VARCHAR, max_length=TEXT_MAX_LENGTH),
                FieldSchema(name=config.rag_dense_vector_field, dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
            ]
            collection = Collection(
                name=collection_name,
                schema=CollectionSchema(
                    fields=fields,
                    description="Long-term diagnosis experience memory",
                    enable_dynamic_field=False,
                ),
                num_shards=2,
            )
            collection.create_index(
                field_name=config.rag_dense_vector_field,
                index_params={
                    "metric_type": "L2",
                    "index_type": "IVF_FLAT",
                    "params": {"nlist": 128},
                },
            )
        collection = Collection(collection_name)
        collection.load()
        return collection


def _distance_to_similarity(distance: float) -> float:
    return 1.0 / (1.0 + max(float(distance), 0.0))


def _json_list(value: list[str]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


experience_memory_index_service = ExperienceMemoryIndexService()
```

- [ ] **Step 5: Wire the default index into the singleton**

At the bottom of `app/services/experience_memory_service.py`, replace the singleton with:

```python
from app.services.experience_memory_index_service import experience_memory_index_service

experience_memory_service = ExperienceMemoryService(index_service=experience_memory_index_service)
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
pytest tests/test_experience_memory_service.py -v
```

Expected: PASS. Tests should not require live Milvus because they use fake index services.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add app/services/experience_memory_index_service.py app/services/experience_memory_service.py app/core/milvus_client.py tests/test_experience_memory_service.py
git commit -m "feat: add experience memory milvus index adapter"
```

## Task 4: Feedback API Trigger

**Files:**
- Modify: `app/api/aiops.py`
- Test: `tests/test_aiops_feedback_api.py`

- [ ] **Step 1: Add failing tests for accepted-only memory creation and error suppression**

Add fake service and tests to `tests/test_aiops_feedback_api.py`:

```python
class _FakeExperienceMemoryService:
    def __init__(self):
        self.calls = []
        self.raise_on_create = False

    def create_or_merge_from_feedback(
        self,
        *,
        case_id,
        feedback_id,
        project_id,
        environment="",
        service_name="",
    ):
        if self.raise_on_create:
            raise RuntimeError("milvus unavailable")
        self.calls.append(
            {
                "case_id": case_id,
                "feedback_id": feedback_id,
                "project_id": project_id,
                "environment": environment,
                "service_name": service_name,
            }
        )
        return "exp-1"
```

Update `_FakeDiagnosisMemoryService.record_feedback` to return a feedback ID:

```python
        return f"feedback-{len(self.feedback)}"
```

Add tests:

```python
@pytest.mark.asyncio
async def test_record_diagnosis_feedback_creates_experience_for_accepted_feedback(
    monkeypatch, api_client
):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_experience = _FakeExperienceMemoryService()
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)
    monkeypatch.setattr(aiops_api, "experience_memory_service", fake_experience, raising=False)

    response = await api_client.post(
        "/api/aiops/feedback",
        headers=SESSION_HEADERS,
        json={
            "case_id": "case-1",
            "session_id": "session-1",
            "user_accepted": True,
            "actual_root_cause": "Milvus connection exhausted",
            "final_resolution": "Restarted Milvus",
        },
    )

    assert response.status_code == 200
    assert fake_experience.calls == [
        {
            "case_id": "case-1",
            "feedback_id": "feedback-1",
            "project_id": "super_biz_agent",
            "environment": "",
            "service_name": "",
        }
    ]


@pytest.mark.asyncio
async def test_record_diagnosis_feedback_skips_experience_for_rejected_feedback(
    monkeypatch, api_client
):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_experience = _FakeExperienceMemoryService()
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)
    monkeypatch.setattr(aiops_api, "experience_memory_service", fake_experience, raising=False)

    response = await api_client.post(
        "/api/aiops/feedback",
        headers=SESSION_HEADERS,
        json={"case_id": "case-1", "session_id": "session-1", "user_accepted": False},
    )

    assert response.status_code == 200
    assert fake_experience.calls == []


@pytest.mark.asyncio
async def test_record_diagnosis_feedback_ignores_experience_memory_errors(
    monkeypatch, api_client
):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_experience = _FakeExperienceMemoryService()
    fake_experience.raise_on_create = True
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)
    monkeypatch.setattr(aiops_api, "experience_memory_service", fake_experience, raising=False)

    response = await api_client.post(
        "/api/aiops/feedback",
        headers=SESSION_HEADERS,
        json={"case_id": "case-1", "session_id": "session-1", "user_accepted": True},
    )

    assert response.status_code == 200
    assert fake_memory.feedback[0]["case_id"] == "case-1"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_aiops_feedback_api.py -v
```

Expected: FAIL because `aiops_api` does not call `experience_memory_service`.

- [ ] **Step 3: Wire feedback API to experience memory**

In `app/api/aiops.py`, import:

```python
from app.config import config
from app.services.experience_memory_service import experience_memory_service
```

Update `record_diagnosis_feedback`:

```python
        feedback_id = diagnosis_memory_service.record_feedback(
            case_id=request.case_id,
            session_id=scoped_session_id,
            user_accepted=request.user_accepted,
            actual_root_cause=request.actual_root_cause,
            final_resolution=request.final_resolution,
            comment=request.comment,
        )
        if request.user_accepted:
            try:
                experience_memory_service.create_or_merge_from_feedback(
                    case_id=request.case_id,
                    feedback_id=feedback_id,
                    project_id=config.project_id,
                )
            except Exception as exc:
                logger.warning(f"long-term experience memory write failed: {exc}")
```

- [ ] **Step 4: Run feedback API tests**

Run:

```bash
pytest tests/test_aiops_feedback_api.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add app/api/aiops.py tests/test_aiops_feedback_api.py
git commit -m "feat: create experience memories from accepted feedback"
```

## Task 5: Planner Recall And Prompt Injection

**Files:**
- Modify: `app/agent/aiops/planner.py`
- Test: `tests/test_aiops_experience_planner.py`

- [ ] **Step 1: Write failing unit tests for formatting and retrieval**

Create `tests/test_aiops_experience_planner.py`:

```python
from app.agent.aiops import planner as planner_module


class _FakeExperienceMemoryService:
    def __init__(self):
        self.calls = []

    def search_relevant_experiences(self, *, query, project_id, top_k):
        self.calls.append({"query": query, "project_id": project_id, "top_k": top_k})
        return [
            {
                "experience_id": "exp-1",
                "similarity": 0.86,
                "confidence": 0.8,
                "symptoms": "API slow with Milvus timeout",
                "root_cause": "Milvus connection pool exhausted",
                "resolution": "Restart Milvus and reuse clients",
                "evidence_summary": "cls-1 timeout; metric-1 latency p95 high",
                "source_case_ids": ["case-1"],
            }
        ]


def test_format_experience_context_requires_verification_first():
    context = planner_module.format_experience_context(
        [
            {
                "experience_id": "exp-1",
                "similarity": 0.86,
                "confidence": 0.8,
                "symptoms": "API slow with Milvus timeout",
                "root_cause": "Milvus connection pool exhausted",
                "resolution": "Restart Milvus",
                "evidence_summary": "cls-1 timeout",
                "source_case_ids": ["case-1"],
            }
        ]
    )

    assert "exp-1" in context
    assert "Milvus connection pool exhausted" in context
    assert "first verify the historical root cause" in context


def test_load_experience_context_searches_by_project(monkeypatch):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(planner_module, "experience_memory_service", fake_service)
    monkeypatch.setattr(planner_module.config, "project_id", "super_biz_agent")
    monkeypatch.setattr(planner_module.config, "experience_memory_top_k", 3)

    context = planner_module.load_experience_context("diagnose API slow")

    assert fake_service.calls == [
        {"query": "diagnose API slow", "project_id": "super_biz_agent", "top_k": 3}
    ]
    assert "exp-1" in context
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_aiops_experience_planner.py -v
```

Expected: FAIL because helper functions do not exist.

- [ ] **Step 3: Add Planner helper imports**

In `app/agent/aiops/planner.py`, add:

```python
from app.services.experience_memory_service import experience_memory_service
```

- [ ] **Step 4: Add context helpers**

Add below the `Plan` model:

```python
def load_experience_context(input_text: str) -> str:
    try:
        experiences = experience_memory_service.search_relevant_experiences(
            query=input_text,
            project_id=config.project_id,
            top_k=config.experience_memory_top_k,
        )
    except Exception as exc:
        logger.warning(f"experience memory recall failed: {exc}")
        return ""
    return format_experience_context(experiences)


def format_experience_context(experiences: list[dict[str, Any]]) -> str:
    if not experiences:
        return ""
    sections = [
        "## Relevant historical experience",
        "",
        "Historical experience is not current fact.",
        "If similarity and confidence are high, first verify the historical root cause.",
        "If verification fails, continue normal investigation.",
        "",
    ]
    for item in experiences:
        sections.extend(
            [
                f"[{item['experience_id']}]",
                f"similarity: {item.get('similarity', 0):.2f}",
                f"confidence: {item.get('confidence', 0):.2f}",
                f"historical symptoms: {item['symptoms']}",
                f"verified root cause: {item['root_cause']}",
                f"effective resolution: {item['resolution']}",
                f"key evidence: {item['evidence_summary']}",
                f"source cases: {', '.join(item.get('source_case_ids', []))}",
                "",
            ]
        )
    return "\n".join(sections).strip()
```

- [ ] **Step 5: Replace current knowledge-only experience lookup with long-term memory plus existing RAG docs**

In `planner`, after `input_text` is defined, add:

```python
        memory_context = load_experience_context(input_text)
```

When building `experience_context`, prepend memory:

```python
        context_blocks = []
        if memory_context:
            context_blocks.append(memory_context)
        if experience_docs:
            context_blocks.append(dedent(f"""
                ## Relevant knowledge documents

                The following documents were retrieved from the knowledge base:

                {experience_docs}
            """).strip())
        experience_context = "\n\n---\n\n".join(context_blocks)
```

Remove the old `if experience_docs: ... else: ...` block so there is only one `experience_context` assignment.

- [ ] **Step 6: Run Planner helper tests**

Run:

```bash
pytest tests/test_aiops_experience_planner.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

Run:

```bash
git add app/agent/aiops/planner.py tests/test_aiops_experience_planner.py
git commit -m "feat: inject recalled experience memory into planner"
```

## Task 6: Governance API

**Files:**
- Create: `app/models/memory.py`
- Create: `app/api/memory.py`
- Modify: `app/main.py`
- Test: `tests/test_experience_memory_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_experience_memory_api.py`:

```python
import pytest

from app.api import memory as memory_api


class _FakeExperienceMemoryService:
    def __init__(self):
        self.enabled_updates = []
        self.rebuild_project_ids = []

    def list_memories(
        self,
        *,
        project_id=None,
        enabled=None,
        service_name=None,
        min_confidence=None,
        limit=50,
        offset=0,
    ):
        return [
            {
                "experience_id": "exp-1",
                "project_id": project_id or "super_biz_agent",
                "environment": "local",
                "service_name": service_name or "milvus",
                "symptoms": "Milvus timeout",
                "root_cause": "connection pool exhausted",
                "resolution": "reuse client",
                "evidence_summary": "cls-1 timeout",
                "source_case_ids": ["case-1"],
                "source_feedback_ids": ["feedback-1"],
                "confidence": 0.8,
                "hit_count": 2,
                "success_count": 1,
                "enabled": True if enabled is None else enabled,
                "milvus_pk": "exp-1",
                "created_at": "2026-06-14T10:30:00+00:00",
                "updated_at": "2026-06-14T10:30:00+00:00",
            }
        ]

    def get_memory(self, experience_id):
        if experience_id == "missing":
            return None
        return self.list_memories()[0]

    def set_enabled(self, experience_id, *, enabled):
        self.enabled_updates.append({"experience_id": experience_id, "enabled": enabled})
        return experience_id != "missing"

    def rebuild_index(self, *, project_id=None):
        self.rebuild_project_ids.append(project_id)
        return 1


@pytest.mark.asyncio
async def test_list_experience_memories(monkeypatch, api_client):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake_service)

    response = await api_client.get(
        "/api/memory/experiences?project_id=super_biz_agent&enabled=true"
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["experience_id"] == "exp-1"


@pytest.mark.asyncio
async def test_get_experience_memory_detail(monkeypatch, api_client):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake_service)

    response = await api_client.get("/api/memory/experiences/exp-1")

    assert response.status_code == 200
    assert response.json()["data"]["source_case_ids"] == ["case-1"]


@pytest.mark.asyncio
async def test_disable_experience_memory(monkeypatch, api_client):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake_service)

    response = await api_client.patch(
        "/api/memory/experiences/exp-1",
        json={"enabled": False},
    )

    assert response.status_code == 200
    assert fake_service.enabled_updates == [{"experience_id": "exp-1", "enabled": False}]


@pytest.mark.asyncio
async def test_rebuild_experience_memory_index(monkeypatch, api_client):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake_service)

    response = await api_client.post(
        "/api/memory/experiences/rebuild-index?project_id=super_biz_agent"
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"indexed": 1}
    assert fake_service.rebuild_project_ids == ["super_biz_agent"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_experience_memory_api.py -v
```

Expected: FAIL because `app.api.memory` does not exist.

- [ ] **Step 3: Add memory models**

Create `app/models/memory.py`:

```python
from pydantic import BaseModel


class ExperienceMemoryUpdateRequest(BaseModel):
    enabled: bool
```

- [ ] **Step 4: Add memory API router**

Create `app/api/memory.py`:

```python
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger

from app.models.memory import ExperienceMemoryUpdateRequest
from app.services.experience_memory_service import experience_memory_service

router = APIRouter()


@router.get("/memory/experiences")
async def list_experience_memories(
    project_id: str | None = None,
    enabled: bool | None = None,
    service_name: str | None = None,
    min_confidence: float | None = None,
    limit: int = 50,
    offset: int = 0,
):
    try:
        memories = experience_memory_service.list_memories(
            project_id=project_id,
            enabled=enabled,
            service_name=service_name,
            min_confidence=min_confidence,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.error(f"list experience memories failed: {exc}")
        return JSONResponse(status_code=500, content={"code": 500, "message": "error", "data": None})
    return {"code": 200, "message": "success", "data": memories}


@router.get("/memory/experiences/{experience_id}")
async def get_experience_memory(experience_id: str):
    try:
        memory = experience_memory_service.get_memory(experience_id)
    except Exception as exc:
        logger.error(f"get experience memory failed: {exc}")
        return JSONResponse(status_code=500, content={"code": 500, "message": "error", "data": None})
    if memory is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": f"Experience memory not found: {experience_id}", "data": None},
        )
    return {"code": 200, "message": "success", "data": memory}


@router.patch("/memory/experiences/{experience_id}")
async def update_experience_memory(experience_id: str, request: ExperienceMemoryUpdateRequest):
    try:
        changed = experience_memory_service.set_enabled(experience_id, enabled=request.enabled)
    except Exception as exc:
        logger.error(f"update experience memory failed: {exc}")
        return JSONResponse(status_code=500, content={"code": 500, "message": "error", "data": None})
    if not changed:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": f"Experience memory not found: {experience_id}", "data": None},
        )
    return {"code": 200, "message": "success", "data": {"experience_id": experience_id, "enabled": request.enabled}}


@router.post("/memory/experiences/rebuild-index")
async def rebuild_experience_memory_index(project_id: str | None = None):
    try:
        indexed = experience_memory_service.rebuild_index(project_id=project_id)
    except Exception as exc:
        logger.error(f"rebuild experience memory index failed: {exc}")
        return JSONResponse(status_code=500, content={"code": 500, "message": "error", "data": None})
    return {"code": 200, "message": "success", "data": {"indexed": indexed}}
```

- [ ] **Step 5: Register router**

In `app/main.py`, change:

```python
from app.api import aiops, assistant, chat, file, health
```

to:

```python
from app.api import aiops, assistant, chat, file, health, memory
```

Add:

```python
app.include_router(memory.router, prefix="/api", tags=["长期经验记忆"])
```

- [ ] **Step 6: Run API tests**

Run:

```bash
pytest tests/test_experience_memory_api.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

Run:

```bash
git add app/models/memory.py app/api/memory.py app/main.py tests/test_experience_memory_api.py
git commit -m "feat: add experience memory governance api"
```

## Task 7: Integration Verification And Regression Tests

**Files:**
- Modify as needed based on failures from previous tasks.
- Test: existing changed tests plus targeted regression suites.

- [ ] **Step 1: Run the experience memory test set**

Run:

```bash
pytest tests/test_experience_memory_service.py tests/test_experience_memory_api.py tests/test_aiops_feedback_api.py tests/test_aiops_experience_planner.py -v
```

Expected: PASS.

- [ ] **Step 2: Run adjacent persistence and AIOps tests**

Run:

```bash
pytest tests/test_diagnosis_memory_service.py tests/test_checkpoint_persistence.py tests/test_aiops_feedback_api.py -v
```

Expected: PASS.

- [ ] **Step 3: Run lint on touched files**

Run:

```bash
ruff check app/services/experience_memory_service.py app/services/experience_memory_index_service.py app/api/aiops.py app/api/memory.py app/agent/aiops/planner.py app/models/memory.py tests/test_experience_memory_service.py tests/test_experience_memory_api.py tests/test_aiops_feedback_api.py tests/test_aiops_experience_planner.py
```

Expected: PASS.

- [ ] **Step 4: Run full test suite if local services are not required**

Run:

```bash
pytest
```

Expected: PASS, or fail only on tests that require unavailable external services. Record any external-service failures with exact names and error messages in the final handoff.

- [ ] **Step 5: Review git diff**

Run:

```bash
git diff -- app/config.py app/services/diagnosis_memory_service.py app/services/experience_memory_service.py app/services/experience_memory_index_service.py app/api/aiops.py app/api/memory.py app/main.py app/models/memory.py app/agent/aiops/planner.py tests/test_diagnosis_memory_service.py tests/test_experience_memory_service.py tests/test_experience_memory_api.py tests/test_aiops_feedback_api.py tests/test_aiops_experience_planner.py
```

Expected: Diff only contains long-term experience memory implementation and tests.

- [ ] **Step 6: Commit final fixes**

If Task 7 produced code fixes, run:

```bash
git add app/config.py app/services/diagnosis_memory_service.py app/services/experience_memory_service.py app/services/experience_memory_index_service.py app/api/aiops.py app/api/memory.py app/main.py app/models/memory.py app/agent/aiops/planner.py tests/test_diagnosis_memory_service.py tests/test_experience_memory_service.py tests/test_experience_memory_api.py tests/test_aiops_feedback_api.py tests/test_aiops_experience_planner.py
git commit -m "test: verify experience memory integration"
```

If Task 7 produced no code fixes, do not create an empty commit.

## Self-Review Notes

- Spec coverage:
  - SQLite authority and fields: Task 1.
  - Accepted feedback trigger: Task 4.
  - Rule-based card generation: Task 2.
  - Milvus semantic index and project filtering: Task 3.
  - Planner recall and verify-first behavior: Task 5.
  - Governance API and rebuild: Task 6.
  - Failure handling and regression verification: Tasks 3, 4, and 7.

- Boundaries:
  - No conflict decay.
  - No unified Context Assembler.
  - No UI changes.
  - No automatic memory deletion.

- Execution note:
  - The current working tree may contain unrelated changes. Each task stages only the files listed in that task.
