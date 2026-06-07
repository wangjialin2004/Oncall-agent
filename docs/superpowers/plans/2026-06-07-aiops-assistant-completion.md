# AIOps Assistant Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the approved completion scope for the intelligent AIOps assistant: unified routing, explicit indexing status, diagnosis feedback memory, and accurate health configuration.

**Architecture:** Add small, deterministic services and additive API fields while preserving existing RAG and AIOps endpoints. Use synchronous indexing and SQLite-backed memory to match the current project style and avoid infrastructure changes.

**Tech Stack:** Python 3.13, FastAPI, Pydantic Settings, pytest, SQLite, existing LangChain/LangGraph service boundaries.

---

## File Structure

- Create `app/services/router_service.py`: deterministic route classification and unified answer orchestration.
- Create `app/api/assistant.py`: `POST /api/assistant` endpoint.
- Modify `app/main.py`: register assistant router.
- Modify `app/services/vector_index_service.py`: return indexing result metadata from `index_single_file`.
- Modify `app/api/file.py`: include indexing status fields in upload response.
- Modify `app/services/diagnosis_memory_service.py`: add feedback persistence methods and table.
- Modify `app/config.py`: add `monitor_target_mode` and `log_provider`.
- Modify `app/api/health.py`: report actual RAG, monitor, and log configuration.
- Add `tests/test_router_service.py`: route classification and dispatch tests.
- Add `tests/test_assistant_api.py`: unified API tests with monkeypatched services.
- Add or modify `tests/test_file_api.py`: upload indexing status tests.
- Modify `tests/test_diagnosis_memory_service.py`: feedback memory tests.
- Modify `tests/test_health_api.py`: health configuration assertions.

---

### Task 1: Router Service and Assistant API

**Files:**
- Create: `app/services/router_service.py`
- Create: `app/api/assistant.py`
- Modify: `app/main.py`
- Test: `tests/test_router_service.py`
- Test: `tests/test_assistant_api.py`

- [ ] **Step 1: Write failing router service tests**

Create `tests/test_router_service.py`:

```python
import pytest

from app.services.router_service import RouterService


def test_route_message_detects_aiops_intent():
    service = RouterService()

    decision = service.route_message("CPU 告警了，帮我诊断服务日志")

    assert decision.route == "aiops"
    assert decision.reason == "matched_aiops_keyword"


def test_route_message_detects_rag_intent():
    service = RouterService()

    decision = service.route_message("请说明知识库里的慢响应排查步骤")

    assert decision.route == "rag"
    assert decision.reason == "matched_rag_keyword"


def test_route_message_clarifies_empty_input():
    service = RouterService()

    decision = service.route_message("   ")

    assert decision.route == "clarify"
    assert decision.reason == "empty_message"


@pytest.mark.asyncio
async def test_answer_dispatches_to_rag(monkeypatch):
    service = RouterService()

    async def fake_query(message, session_id):
        return f"rag:{session_id}:{message}"

    monkeypatch.setattr("app.services.router_service.rag_agent_service.query", fake_query)

    result = await service.answer("怎么处理慢响应", session_id="s1")

    assert result == {
        "success": True,
        "route": "rag",
        "answer": "rag:s1:怎么处理慢响应",
        "errorMessage": None,
    }


@pytest.mark.asyncio
async def test_answer_collects_aiops_final_response(monkeypatch):
    service = RouterService()

    async def fake_execute(message, session_id):
        yield {"type": "status", "message": "running"}
        yield {"type": "complete", "response": "# report"}

    monkeypatch.setattr("app.services.router_service.aiops_service.execute", fake_execute)

    result = await service.answer("帮我诊断 CPU 告警", session_id="s1")

    assert result == {
        "success": True,
        "route": "aiops",
        "answer": "# report",
        "errorMessage": None,
    }
```

- [ ] **Step 2: Run router tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_router_service.py -q
```

Expected: FAIL because `app.services.router_service` does not exist.

- [ ] **Step 3: Implement router service**

Create `app/services/router_service.py`:

```python
"""Unified assistant routing service."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.aiops_service import aiops_service
from app.services.rag_agent_service import rag_agent_service


@dataclass(slots=True)
class RouteDecision:
    route: str
    reason: str


class RouterService:
    """Route user messages to RAG chat, AIOps diagnosis, or clarification."""

    AIOPS_KEYWORDS = (
        "告警",
        "故障",
        "异常",
        "诊断",
        "日志",
        "cpu",
        "内存",
        "不可用",
        "失败",
        "排查",
        "报警",
        "error",
        "failed",
    )
    RAG_KEYWORDS = (
        "文档",
        "知识库",
        "说明",
        "怎么",
        "步骤",
        "处理",
        "解释",
        "是什么",
    )

    def route_message(self, message: str) -> RouteDecision:
        normalized = message.strip().lower()
        if not normalized:
            return RouteDecision(route="clarify", reason="empty_message")

        if any(keyword in normalized for keyword in self.AIOPS_KEYWORDS):
            return RouteDecision(route="aiops", reason="matched_aiops_keyword")

        if any(keyword in normalized for keyword in self.RAG_KEYWORDS):
            return RouteDecision(route="rag", reason="matched_rag_keyword")

        return RouteDecision(route="rag", reason="default_rag")

    async def answer(self, message: str, session_id: str) -> dict[str, object]:
        decision = self.route_message(message)
        if decision.route == "clarify":
            return {
                "success": True,
                "route": "clarify",
                "answer": "请补充你想咨询的问题，或说明需要诊断的服务、告警、日志现象。",
                "errorMessage": None,
            }

        if decision.route == "aiops":
            final_answer = ""
            async for event in aiops_service.execute(message, session_id=session_id):
                if event.get("type") == "complete":
                    final_answer = str(event.get("response") or event.get("message") or "")
            return {
                "success": True,
                "route": "aiops",
                "answer": final_answer,
                "errorMessage": None,
            }

        answer = await rag_agent_service.query(message, session_id=session_id)
        return {
            "success": True,
            "route": "rag",
            "answer": answer,
            "errorMessage": None,
        }


router_service = RouterService()
```

- [ ] **Step 4: Run router tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_router_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Write failing assistant API test**

Create `tests/test_assistant_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_assistant_endpoint_returns_router_result(monkeypatch):
    async def fake_answer(question, session_id):
        return {
            "success": True,
            "route": "rag",
            "answer": f"{session_id}:{question}",
            "errorMessage": None,
        }

    monkeypatch.setattr("app.api.assistant.router_service.answer", fake_answer)

    client = TestClient(app)
    response = client.post("/api/assistant", json={"Id": "s1", "Question": "怎么排查慢响应"})

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "data": {
            "success": True,
            "route": "rag",
            "answer": "s1:怎么排查慢响应",
            "errorMessage": None,
        },
    }
```

- [ ] **Step 6: Run assistant API test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_assistant_api.py -q
```

Expected: FAIL because `app.api.assistant` does not exist or `/api/assistant` is not registered.

- [ ] **Step 7: Implement assistant API**

Create `app/api/assistant.py`:

```python
"""Unified assistant API."""

from fastapi import APIRouter
from loguru import logger

from app.models.request import ChatRequest
from app.services.router_service import router_service

router = APIRouter()


@router.post("/assistant")
async def assistant(request: ChatRequest):
    try:
        logger.info(f"[会话 {request.id}] 收到统一助手请求: {request.question}")
        data = await router_service.answer(request.question, session_id=request.id)
        return {
            "code": 200,
            "message": "success",
            "data": data,
        }
    except Exception as exc:
        logger.error(f"统一助手接口错误: {exc}")
        return {
            "code": 500,
            "message": "error",
            "data": {
                "success": False,
                "route": "error",
                "answer": None,
                "errorMessage": str(exc),
            },
        }
```

Modify `app/main.py` imports and router registration:

```python
from app.api import aiops, assistant, chat, file, health
```

```python
app.include_router(assistant.router, prefix="/api", tags=["统一助手"])
```

- [ ] **Step 8: Run assistant API test to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_assistant_api.py tests\test_router_service.py -q
```

Expected: PASS.

---

### Task 2: Explicit Upload Indexing Status

**Files:**
- Modify: `app/services/vector_index_service.py`
- Modify: `app/api/file.py`
- Test: `tests/test_file_api.py`

- [ ] **Step 1: Write failing upload indexing tests**

Create `tests/test_file_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_upload_reports_completed_indexing(monkeypatch, tmp_path):
    import app.api.file as file_api

    monkeypatch.setattr(file_api, "UPLOAD_DIR", tmp_path)

    def fake_index_single_file(file_path):
        return {"status": "completed", "chunk_count": 2, "error_message": ""}

    monkeypatch.setattr(file_api.vector_index_service, "index_single_file", fake_index_single_file)

    client = TestClient(app)
    response = client.post(
        "/api/upload",
        files={"file": ("note.md", b"# hello", "text/markdown")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["filename"] == "note.md"
    assert data["indexing_status"] == "completed"
    assert data["indexed_chunks"] == 2
    assert data["indexing_error"] == ""


def test_upload_reports_failed_indexing_without_failing_upload(monkeypatch, tmp_path):
    import app.api.file as file_api

    monkeypatch.setattr(file_api, "UPLOAD_DIR", tmp_path)

    def fake_index_single_file(file_path):
        raise RuntimeError("Milvus unavailable")

    monkeypatch.setattr(file_api.vector_index_service, "index_single_file", fake_index_single_file)

    client = TestClient(app)
    response = client.post(
        "/api/upload",
        files={"file": ("note.md", b"# hello", "text/markdown")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["indexing_status"] == "failed"
    assert data["indexed_chunks"] == 0
    assert data["indexing_error"] == "Milvus unavailable"
```

- [ ] **Step 2: Run upload tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_file_api.py -q
```

Expected: FAIL because upload response lacks indexing status fields.

- [ ] **Step 3: Implement indexing result return**

Modify `app/services/vector_index_service.py`.

Add class after `IndexingResult`:

```python
class SingleFileIndexingResult:
    """单文件索引结果。"""

    def __init__(self, status: str, chunk_count: int = 0, error_message: str = ""):
        self.status = status
        self.chunk_count = chunk_count
        self.error_message = error_message

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "chunk_count": self.chunk_count,
            "error_message": self.error_message,
        }
```

Update `index_single_file` to return `SingleFileIndexingResult`:

```python
    def index_single_file(self, file_path: str) -> SingleFileIndexingResult:
```

Return `SingleFileIndexingResult(status="completed", chunk_count=len(documents))` after adding documents.

Return `SingleFileIndexingResult(status="skipped", chunk_count=0)` when no documents are generated.

Keep raising `RuntimeError` on failure so directory indexing and upload failure reporting keep their existing semantics.

- [ ] **Step 4: Implement upload response status fields**

Modify `app/api/file.py` indexing block:

```python
        indexing_status = "pending"
        indexing_error = ""
        indexed_chunks = 0

        try:
            logger.info(f"开始为上传文件创建向量索引: {file_path}")
            indexing_result = vector_index_service.index_single_file(str(file_path))
            result_data = (
                indexing_result.to_dict()
                if hasattr(indexing_result, "to_dict")
                else indexing_result
            )
            indexing_status = str(result_data.get("status", "completed"))
            indexed_chunks = int(result_data.get("chunk_count", 0))
            indexing_error = str(result_data.get("error_message", ""))
            logger.info(f"向量索引创建成功: {file_path}")
        except Exception as e:
            indexing_status = "failed"
            indexing_error = str(e)
            indexed_chunks = 0
            logger.error(f"向量索引创建失败: {file_path}, 错误: {e}")
```

Add response fields under `data`:

```python
                    "indexing_status": indexing_status,
                    "indexing_error": indexing_error,
                    "indexed_chunks": indexed_chunks,
```

- [ ] **Step 5: Run upload tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_file_api.py -q
```

Expected: PASS.

---

### Task 3: Diagnosis Feedback Memory

**Files:**
- Modify: `app/services/diagnosis_memory_service.py`
- Test: `tests/test_diagnosis_memory_service.py`

- [ ] **Step 1: Write failing feedback memory test**

Append to `tests/test_diagnosis_memory_service.py`:

```python
def test_diagnosis_memory_service_persists_feedback(tmp_path):
    db_path = tmp_path / "diagnosis-memory.sqlite3"
    service = DiagnosisMemoryService(db_path)
    service.create_case(
        session_id="session-1",
        user_input="diagnose current alerts",
        case_id="case-1",
    )

    service.record_feedback(
        case_id="case-1",
        session_id="session-1",
        user_accepted=True,
        actual_root_cause="Milvus connection exhausted",
        final_resolution="Restarted Milvus and reduced connection churn",
        comment="诊断结论准确",
    )

    feedback = service.list_feedback("case-1")

    assert feedback == [
        {
            "case_id": "case-1",
            "session_id": "session-1",
            "user_accepted": True,
            "actual_root_cause": "Milvus connection exhausted",
            "final_resolution": "Restarted Milvus and reduced connection churn",
            "comment": "诊断结论准确",
        }
    ]
```

- [ ] **Step 2: Run feedback test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_diagnosis_memory_service.py::test_diagnosis_memory_service_persists_feedback -q
```

Expected: FAIL because `record_feedback` does not exist.

- [ ] **Step 3: Implement feedback persistence**

Modify `app/services/diagnosis_memory_service.py`.

Add methods after `list_tool_evidence`:

```python
    def record_feedback(
        self,
        case_id: str,
        session_id: str,
        user_accepted: bool,
        actual_root_cause: str = "",
        final_resolution: str = "",
        comment: str = "",
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO diagnosis_feedback (
                    case_id, session_id, user_accepted, actual_root_cause,
                    final_resolution, comment, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    session_id,
                    1 if user_accepted else 0,
                    actual_root_cause,
                    final_resolution,
                    comment,
                    _utc_now(),
                ),
            )

    def list_feedback(self, case_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
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
```

Add table creation in `_ensure_database`:

```python
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnosis_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_diagnosis_feedback_case_id
                ON diagnosis_feedback(case_id)
                """
            )
```

- [ ] **Step 4: Run feedback tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_diagnosis_memory_service.py -q
```

Expected: PASS.

---

### Task 4: Health Configuration Accuracy

**Files:**
- Modify: `app/config.py`
- Modify: `app/api/health.py`
- Modify: `tests/test_health_api.py`

- [ ] **Step 1: Write failing health config assertions**

Modify `tests/test_health_api.py`:

```python
from app.api import health


def test_build_health_data_includes_core_dependencies(monkeypatch):
    monkeypatch.setattr(health.milvus_manager, "health_check", lambda: True)
    monkeypatch.setattr(health, "_port_reachable", lambda _url: False)
    monkeypatch.setattr(health.config, "dashscope_api_key", "test-api-key")
    monkeypatch.setattr(health.config, "rag_retrieval_mode", "hybrid")
    monkeypatch.setattr(health.config, "rag_dense_weight", 0.65)
    monkeypatch.setattr(health.config, "rag_bm25_weight", 0.35)
    monkeypatch.setattr(health.config, "monitor_target_mode", "self")
    monkeypatch.setattr(health.config, "log_provider", "local")

    data = health.build_health_data()

    assert data["milvus"]["status"] == "connected"
    assert data["mcp"]["cls"]["status"] == "unreachable"
    assert data["mcp"]["monitor"]["url"] == health.config.mcp_monitor_url
    assert data["llm"]["status"] == "configured"
    assert data["rag"]["collection_name"] == health.milvus_manager.COLLECTION_NAME
    assert data["rag"]["retrieval_mode"] == "hybrid"
    assert data["rag"]["dense_weight"] == 0.65
    assert data["rag"]["bm25_weight"] == 0.35
    assert data["monitor"]["target_mode"] == "self"
    assert data["logs"]["provider"] == "local"
    assert data["status"] == "healthy"
```

- [ ] **Step 2: Run health test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_health_api.py -q
```

Expected: FAIL because `rag.retrieval_mode` is hard-coded or `monitor/logs` sections are missing.

- [ ] **Step 3: Add settings and health data fields**

Modify `app/config.py`:

```python
    monitor_target_mode: str = "self"
    log_provider: str = "local"
```

Modify `app/api/health.py` `build_health_data()`:

```python
    health_data["rag"] = {
        "collection_name": milvus_manager.COLLECTION_NAME,
        "collection_status": "available" if health_data["milvus"]["status"] == "connected" else "unavailable",
        "retrieval_mode": config.rag_retrieval_mode,
        "top_k": config.rag_top_k,
        "dense_weight": config.rag_dense_weight,
        "bm25_weight": config.rag_bm25_weight,
    }
    health_data["monitor"] = {
        "target_mode": config.monitor_target_mode,
    }
    health_data["logs"] = {
        "provider": config.log_provider,
    }
```

- [ ] **Step 4: Run health tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_health_api.py tests\test_config.py -q
```

Expected: PASS.

---

### Task 5: Final Verification and Completion Audit

**Files:**
- Inspect: `docs/智能运维助手改造方案.md`
- Inspect: `docs/superpowers/specs/2026-06-07-aiops-assistant-completion-design.md`
- Run: full test suite

- [ ] **Step 1: Run full pytest**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Inspect git diff**

Run:

```powershell
git diff --stat
git diff -- app tests docs
```

Expected: diff only contains approved scope and tests.

- [ ] **Step 3: Commit implementation**

Run:

```powershell
git add app tests docs/superpowers/plans/2026-06-07-aiops-assistant-completion.md
git commit -m "feat: complete assistant routing and operational memory"
```

Expected: commit succeeds.

- [ ] **Step 4: Final audit**

Verify:

- `/api/assistant` is registered and tested.
- Upload responses expose indexing status and errors.
- Diagnosis feedback is persisted and listed.
- Health data reflects actual RAG, monitor, and log config.
- Full pytest output shows all tests passing.

If all items are proven by current code and command output, report completion evidence. If any item is not proven, continue working on the missing item.

---

## Self-Review Notes

- Spec coverage: all approved goals are covered by Tasks 1-4.
- No placeholders: every task has explicit files, test code, implementation target, and commands.
- Type consistency: route result keys, upload fields, and feedback fields match the design spec.
