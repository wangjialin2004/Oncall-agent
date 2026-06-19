# Long-Term Memory System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reintroduce long-term memory for the Router + flat Expert architecture, covering L1 diagnosis experience recall, L2 service knowledge and baselines, and L3 explicit user preferences without returning to the old OnCall Planner pipeline.

**Architecture:** SQLite is the governed source of truth for all memory layers. Milvus is used only for L1 semantic recall, with graceful degradation when vector search is unavailable. L1 and L2 are consumed through RuntimeTool-compatible tools or tool-result enrichment; L3 is injected into Expert context from `owner_key`-scoped preferences.

**Tech Stack:** FastAPI, Pydantic, SQLite, existing `RuntimeTool` tool loop, existing Expert router, Milvus through `app/core/milvus_client.py`, embeddings through `app/services/vector_embedding_service.py`, pytest.

---

## Scope

This plan implements the PRD in four independently shippable phases:

- Phase 1: L1 experience memory, feedback timeline distillation, manual cold-start entry, weak acceptance entry, and `recall_experience`.
- Phase 2: L2 service knowledge, manual baselines, seed import from monitor MCP, `lookup_service_knowledge`, and metric/log result enrichment.
- Phase 3: L3 explicit user preferences, owner isolation, and prompt injection.
- Phase 4: Governance, safety filtering, conflict labels, hit-count dedupe, and regression verification.

This plan does not implement automatic baseline learning, automatic preference extraction from dialogue, autonomous remediation, or the old fixed OnCall Planner/Executor/Reporter pipeline.

## File Structure

- Create `app/models/memory.py`
  - Pydantic models for L1 feedback, experience records, service knowledge, baselines, preferences, and governance requests.

- Create `app/services/memory_safety.py`
  - Small deterministic redaction helpers used before writes.

- Create `app/services/experience_memory_service.py`
  - SQLite authority for `experience_memories`.
  - Distills events timeline into `evidence_summary`.
  - Creates explicit feedback memories, weak-acceptance memories, and manual cold-start memories.
  - Merges close duplicates when index recall finds same-root-cause matches.

- Create `app/services/experience_memory_index_service.py`
  - Owns L1 Milvus collection.
  - Embeds symptoms, upserts memory vectors, recalls similar memories, disables records, and rebuilds index.
  - Fails closed for writes and returns empty recall results when Milvus is unavailable.

- Create `app/tools/recall_experience.py`
  - RuntimeTool wrapper for Expert-facing historical experience recall.

- Create `app/services/service_knowledge_service.py`
  - SQLite authority for L2 `services`, `service_relations`, and `service_baselines`.
  - Supports manual baseline CRUD and seed import from monitor MCP metadata.

- Create `app/tools/lookup_service_knowledge.py`
  - RuntimeTool wrapper for exact service knowledge lookup.

- Create `app/services/user_preference_service.py`
  - SQLite authority for `user_preferences`, keyed by hashed `owner_key`.

- Create `app/api/memory.py`
  - L1 feedback and governance endpoints.
  - L2 service knowledge endpoints.
  - L3 explicit preference endpoints.

- Modify `app/config.py`
  - Add memory DB path, project ID, collection names, thresholds, and feature flags.

- Modify `app/tools/__init__.py`
  - Register `recall_experience` with diagnosis/metric/log tools.
  - Register `lookup_service_knowledge` with diagnosis/change and optionally knowledge.

- Modify `app/agent/experts/base.py`
  - Add optional `context` parameter to `run()` and include it in the system/user message stack.
  - Change `get_tools` to `get_tools(self, *, session_id: str = "")` and have `run()` call
    `await self.get_tools(session_id=session_id)`, so experts can bind the real scoped session
    into session-aware tools (recall). All overriding experts widen their `get_tools` signature.

- Modify `app/services/router_service.py`
  - Accept `owner_key`.
  - Load L3 preferences and pass formatted context to Expert runs.
  - Keep `case_id` empty in normal Router + Expert completion payloads.

- Modify `app/api/assistant.py`
  - Pass `owner_key` into `router_service.stream`.

- Modify `app/main.py`
  - Register `app.api.memory`.

- Modify `app/agent/experts/metric.py` and `app/agent/experts/log.py`
  - Use `transform_tool_result` to append service baseline comparison when a service name can be identified.

- Add tests:
  - `tests/test_memory_safety.py`
  - `tests/test_experience_memory_service.py`
  - `tests/test_experience_memory_index_service.py`
  - `tests/test_recall_experience_tool.py`
  - `tests/test_memory_api.py`
  - `tests/test_service_knowledge_service.py`
  - `tests/test_lookup_service_knowledge_tool.py`
  - `tests/test_user_preference_service.py`
  - Update `tests/test_experts.py`
  - Update `tests/test_router_service.py`
  - Update `tests/test_assistant_api.py`

---

## Task 1: Config, Models, And Safety Helpers

**Files:**
- Modify: `app/config.py`
- Create: `app/models/memory.py`
- Create: `app/services/memory_safety.py`
- Test: `tests/test_memory_safety.py`

- [ ] **Step 1: Write tests for secret and PII redaction**

Create `tests/test_memory_safety.py`:

```python
from app.services.memory_safety import redact_memory_text


def test_redact_memory_text_removes_common_secrets():
    text = "root cause token=abc123 api_key: sk-secret password=passw0rd"

    redacted = redact_memory_text(text)

    assert "abc123" not in redacted
    assert "sk-secret" not in redacted
    assert "passw0rd" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_memory_text_masks_email_and_phone():
    text = "contact ops@example.com or 13800138000"

    redacted = redact_memory_text(text)

    assert "ops@example.com" not in redacted
    assert "13800138000" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m pytest tests/test_memory_safety.py -q
```

Expected: FAIL because `app.services.memory_safety` does not exist.

- [ ] **Step 3: Add config fields**

In `app/config.py`, add these fields to `Settings` near the checkpoint and vector configuration:

```python
    # Long-term memory
    memory_db_path: str = "volumes/long_term_memory.db"
    project_id: str = "super_biz_agent"
    long_term_memory_enabled: bool = True
    experience_memory_collection: str = "experience_memory"
    experience_memory_top_k: int = 3
    experience_memory_similarity_threshold: float = 0.78
    experience_memory_high_confidence: float = 0.8
    experience_memory_weak_confidence: float = 0.4
    service_knowledge_enabled: bool = True
    user_preferences_enabled: bool = True
```

> ⚠️ **These flags MUST be enforced at runtime, not just declared.** Tools are registered
> through module-level tuples (`app/tools/__init__.py`) that are evaluated at import and
> cannot honor a flag toggle. Therefore memory tools are exposed through **flag-gated
> helpers called inside each expert's `get_tools()`** (Task 3 Step 5, Task 6 Step 4), and
> enrichment hooks early-return when their flag is off (Task 6 Step 5). The 06-17
> simplification requires long-term memory to be an explicit, switchable feature — do not
> bake the tools unconditionally into the static tuples. Consider defaulting
> `long_term_memory_enabled` to `False` if you want opt-in rollout.

- [ ] **Step 4: Add memory models**

Create `app/models/memory.py`:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MemoryFeedbackRequest(BaseModel):
    session_id: str = Field(default="")
    user_message: str
    assistant_answer: str = Field(default="")
    user_accepted: bool = False
    # "strong": explicit accept/correct (confidence high). "weak": implicit dismiss /
    # moved on without correcting (confidence low, promoted later by hit_count). See Task 4b.
    acceptance_level: Literal["strong", "weak"] = "strong"
    actual_root_cause: str = Field(default="")
    final_resolution: str = Field(default="")
    comment: str = Field(default="")
    environment: str = Field(default="")
    service_name: str = Field(default="")
    # Frontend MUST resend the `complete` event timeline so the backend can distill
    # evidence_summary; without it L1 evidence is empty. See Task 4b frontend step.
    events: list[dict[str, Any]] = Field(default_factory=list)


class ManualExperienceCreateRequest(BaseModel):
    symptoms: str
    root_cause: str
    resolution: str
    evidence_summary: str = Field(default="")
    environment: str = Field(default="")
    service_name: str = Field(default="")
    confidence: float = 0.8


class ExperienceMemoryUpdateRequest(BaseModel):
    enabled: bool | None = None
    confidence: float | None = None


class ServiceBaselineRequest(BaseModel):
    service_name: str
    environment: str = "prod"
    metric_name: Literal["cpu", "memory", "qps", "p95"] | str
    min_value: float
    max_value: float
    unit: str = ""
    sample_window: str = ""


class UserPreferenceRequest(BaseModel):
    default_environment: str = ""
    language: str = "zh-CN"
    detail_level: Literal["brief", "normal", "detailed"] = "normal"
    focused_services: list[str] = Field(default_factory=list)
    notes: str = ""
```

- [ ] **Step 5: Add redaction helper**

Create `app/services/memory_safety.py`:

```python
from __future__ import annotations

import re

SECRET_PATTERNS = [
    re.compile(r"(?i)(token|api[_-]?key|secret|password)\s*[:=]\s*([^\s,;]+)"),
]
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")


def redact_memory_text(text: str) -> str:
    value = text or ""
    for pattern in SECRET_PATTERNS:
        value = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    value = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
    value = PHONE_PATTERN.sub("[REDACTED_PHONE]", value)
    return value
```

- [ ] **Step 6: Verify**

Run:

```powershell
python -m pytest tests/test_memory_safety.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add app/config.py app/models/memory.py app/services/memory_safety.py tests/test_memory_safety.py
git commit -m "feat: add long-term memory config and safety models"
```

---

## Task 2: L1 Experience Memory SQLite Store

**Files:**
- Create: `app/services/experience_memory_service.py`
- Test: `tests/test_experience_memory_service.py`

- [ ] **Step 1: Write failing tests for feedback distillation and manual entry**

Create `tests/test_experience_memory_service.py`:

```python
from app.services.experience_memory_service import ExperienceMemoryService


class FakeIndex:
    def __init__(self):
        self.upserts = []
        self.search_results = []

    def recall(self, *, query, project_id, top_k):
        return self.search_results

    def upsert(self, memory):
        self.upserts.append(memory)
        return memory["experience_id"]


def test_create_from_feedback_distills_timeline(tmp_path):
    index = FakeIndex()
    service = ExperienceMemoryService(db_path=tmp_path / "memory.db", index_service=index)

    memory_id = service.create_from_feedback(
        project_id="p1",
        session_id="s1",
        user_message="checkout api latency high",
        assistant_answer="Likely database saturation",
        user_accepted=True,
        actual_root_cause="database connection pool exhausted",
        final_resolution="increase pool size",
        environment="prod",
        service_name="checkout-api",
        events=[
            {
                "type": "tool_event",
                "tool": "query_prometheus_alerts",
                "evidence_id": "metric-1",
                "summary": "p95 latency above baseline",
            },
            {
                "type": "tool_event",
                "tool": "search_logs",
                "evidence_id": "log-1",
                "summary": "connection timeout repeated",
            },
        ],
    )

    memory = service.get(memory_id)

    assert memory["project_id"] == "p1"
    assert memory["confidence"] == 0.8
    assert memory["root_cause"] == "database connection pool exhausted"
    assert "metric-1" in memory["evidence_summary"]
    assert "log-1" in memory["evidence_summary"]
    assert index.upserts[0]["experience_id"] == memory_id


def test_manual_create_redacts_sensitive_text(tmp_path):
    service = ExperienceMemoryService(db_path=tmp_path / "memory.db", index_service=FakeIndex())

    memory_id = service.create_manual(
        project_id="p1",
        symptoms="api error",
        root_cause="password=passw0rd leaked",
        resolution="rotate token=abc123",
        evidence_summary="manual confirmed",
        environment="prod",
        service_name="api",
        confidence=0.9,
    )

    memory = service.get(memory_id)

    assert "passw0rd" not in memory["root_cause"]
    assert "abc123" not in memory["resolution"]
    assert memory["source_type"] == "manual"


def test_feedback_merges_into_similar_same_root_cause(tmp_path):
    index = FakeIndex()
    service = ExperienceMemoryService(db_path=tmp_path / "memory.db", index_service=index)

    first = service.create_manual(
        project_id="p1",
        symptoms="checkout latency high",
        root_cause="db pool exhausted",
        resolution="increase pool size",
        evidence_summary="metric-1",
        environment="prod",
        service_name="checkout-api",
        confidence=0.8,
    )
    # Index recall returns the existing memory with high similarity + same root cause.
    index.search_results = [{"experience_id": first, "similarity": 0.92}]

    merged = service.create_from_feedback(
        project_id="p1",
        session_id="s2",
        user_message="checkout slow again",
        assistant_answer="db pool",
        user_accepted=True,
        actual_root_cause="db pool exhausted",
        final_resolution="increase pool size",
        environment="prod",
        service_name="checkout-api",
        events=[{"type": "tool_event", "evidence_id": "metric-2", "summary": "p95 high"}],
    )

    # Same experience id (merged, not a new row); sources appended; evidence widened.
    assert merged == first
    record = service.get(first)
    assert "s2" in record["source_session_id"]
    assert "metric-2" in record["evidence_summary"]
    assert len(service.list(project_id="p1")) == 1


def test_feedback_keeps_separate_on_different_root_cause(tmp_path):
    index = FakeIndex()
    service = ExperienceMemoryService(db_path=tmp_path / "memory.db", index_service=index)
    first = service.create_manual(
        project_id="p1", symptoms="checkout latency high", root_cause="db pool exhausted",
        resolution="increase pool", evidence_summary="metric-1", confidence=0.8,
    )
    index.search_results = [{"experience_id": first, "similarity": 0.92}]

    second = service.create_from_feedback(
        project_id="p1", session_id="s3", user_message="checkout slow",
        assistant_answer="gc pause", user_accepted=True,
        actual_root_cause="full GC pauses",  # different root cause
        final_resolution="tune heap", events=[],
    )

    assert second != first
    assert len(service.list(project_id="p1")) == 2
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
python -m pytest tests/test_experience_memory_service.py -q
```

Expected: FAIL because `ExperienceMemoryService` does not exist.

- [ ] **Step 3: Implement the service**

Create `app/services/experience_memory_service.py` with this public interface:

```python
class ExperienceMemoryService:
    def __init__(self, db_path: str | Path | None = None, index_service: Any | None = None): ...
    def create_from_feedback(...): ...
    def create_weak_acceptance(...): ...
    def create_manual(...): ...
    def recall(self, *, query: str, project_id: str, top_k: int = 3, session_id: str = "") -> list[dict[str, Any]]: ...
    def get(self, experience_id: str) -> dict[str, Any] | None: ...
    def list(self, *, project_id: str, enabled: bool | None = None, limit: int = 50) -> list[dict[str, Any]]: ...
    def update(self, experience_id: str, *, enabled: bool | None = None, confidence: float | None = None) -> bool: ...
    def rebuild_index(self, *, project_id: str | None = None) -> int: ...
```

Use this SQLite schema:

```sql
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
);
```

Implementation requirements:

- `create_from_feedback` returns `""` and writes nothing when `user_accepted` is false.
- Feedback initial confidence is `config.experience_memory_high_confidence`.
- Weak acceptance initial confidence is `config.experience_memory_weak_confidence`.
- Evidence summary is built from `tool_event` entries as `tool evidence_id: summary`.
- `root_cause`, `resolution`, and `evidence_summary` pass through `redact_memory_text`.
- Index write failures are logged and do not fail SQLite writes.
- **Deduplication / merge (PRD §五·L1):** before inserting, call `index.recall(...)` for the
  new symptoms. If the top hit's `similarity >= config.experience_memory_similarity_threshold`
  **and** its stored `root_cause` is close to the new one (case/space-insensitive equality is
  enough for v1), **merge** instead of inserting: append the new `source_session_id` /
  `source_feedback_id`, union the `evidence_summary`, bump `updated_at`, keep or raise
  `confidence`, and re-upsert the index. Return the existing `experience_id`. If symptoms are
  similar but root causes differ, insert a **separate** record (covered by the two new tests).
- **`recall` increments `hit_count`** for each returned memory, deduped by `session_id` within
  a single call (same session recalling twice must not double-count). When the recalled set
  contains ≥2 records with similar symptoms but different `root_cause`, attach
  `conflict_count` to each returned dict so the recall tool can warn (Task 3). Sort returned
  memories by `confidence` desc, then `similarity` desc.

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest tests/test_experience_memory_service.py tests/test_memory_safety.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add app/services/experience_memory_service.py tests/test_experience_memory_service.py
git commit -m "feat: add l1 experience memory store"
```

---

## Task 3: L1 Milvus Index And Recall Tool

**Files:**
- Create: `app/services/experience_memory_index_service.py`
- Create: `app/tools/recall_experience.py`
- Modify: `app/tools/__init__.py`
- Test: `tests/test_experience_memory_index_service.py`
- Test: `tests/test_recall_experience_tool.py`
- Update: `tests/test_experts.py`

- [ ] **Step 1: Write failing tool tests**

Create `tests/test_recall_experience_tool.py`:

```python
import pytest

from app.tools import experience_tools
from app.tools.recall_experience import _recall_experience


class FakeService:
    def recall(self, *, query, project_id, top_k, session_id=""):
        return [
            {
                "experience_id": "exp-1",
                "confidence": 0.8,
                "similarity": 0.91,
                "symptoms": "checkout latency high",
                "root_cause": "db pool exhausted",
                "resolution": "increase pool size",
                "evidence_summary": "metric-1 p95 high",
                "conflict_count": 2,
            }
        ]


def test_recall_experience_formats_verify_first_notice(monkeypatch):
    monkeypatch.setattr("app.tools.recall_experience.experience_memory_service", FakeService())
    monkeypatch.setattr("app.tools.recall_experience.config.project_id", "p1")

    text = _recall_experience("checkout latency", session_id="s1")

    assert "历史经验仅供参考" in text
    assert "exp-1" in text
    assert "db pool exhausted" in text
    assert "存在 2 条冲突经验" in text


def test_experience_tools_respects_feature_flag(monkeypatch):
    # Flag-gated at runtime — NOT baked into the static *_LOCAL_TOOLS tuples,
    # so the 06-17 "explicit feature decision" toggle is honored.
    monkeypatch.setattr("app.tools.config.long_term_memory_enabled", True)
    assert "recall_experience" in {tool.name for tool in experience_tools()}

    monkeypatch.setattr("app.tools.config.long_term_memory_enabled", False)
    assert experience_tools() == ()
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_recall_experience_tool.py -q
```

Expected: FAIL because the tool does not exist.

- [ ] **Step 3: Implement index adapter**

Create `app/services/experience_memory_index_service.py` with methods:

```python
class ExperienceMemoryIndexService:
    def recall(self, *, query: str, project_id: str, top_k: int) -> list[dict[str, Any]]: ...
    def upsert(self, memory: dict[str, Any]) -> str: ...
    def disable(self, experience_id: str) -> None: ...
    def rebuild(self, memories: list[dict[str, Any]]) -> int: ...
```

Use existing `vector_embedding_service.embed_query` to embed symptoms. Use `config.experience_memory_collection`. Catch Milvus exceptions and return `[]` from `recall`.

- [ ] **Step 4: Implement recall tool**

Create `app/tools/recall_experience.py`:

```python
from pydantic import BaseModel, Field

from app.config import config
from app.core.runtime_tools import RuntimeTool, make_runtime_tool
from app.services.experience_memory_service import experience_memory_service


class RecallExperienceArgs(BaseModel):
    # NOTE (#7 fix): NO session_id here. The LLM must not supply it — the real scoped
    # session id is bound by the per-run factory below so the model only sees `query`.
    query: str = Field(description="Incident symptom or diagnostic question")


def _recall_experience(query: str, session_id: str = "") -> str:
    memories = experience_memory_service.recall(
        query=query,
        project_id=config.project_id,
        top_k=config.experience_memory_top_k,
        session_id=session_id,
    )
    if not memories:
        return "未命中可复用的历史诊断经验。"
    lines = ["历史经验仅供参考，必须先用当前证据验证后再采信。"]
    for item in memories:
        conflict = item.get("conflict_count", 0)
        lines.extend(
            [
                f"- experience_id: {item['experience_id']}",
                f"  confidence: {item.get('confidence', 0):.2f}",
                f"  similarity: {item.get('similarity', 0):.2f}",
                f"  symptoms: {item['symptoms']}",
                f"  verified_root_cause: {item['root_cause']}",
                f"  effective_resolution: {item['resolution']}",
                f"  evidence_summary: {item['evidence_summary']}",
            ]
        )
        if conflict:
            lines.append(f"  注意：存在 {conflict} 条冲突经验，请谨慎采信。")
    return "\n".join(lines)


def build_recall_experience_tool(session_id: str = "") -> RuntimeTool:
    """Build a recall tool with the REAL scoped session_id bound by closure (#7 fix).

    The LLM only supplies `query`; `session_id` is injected here so the service can
    dedupe hit_count per conversation without trusting model-supplied values.
    """
    return make_runtime_tool(
        name="recall_experience",
        description="Recall verified historical diagnosis experience. Results are reference evidence only and must be verified.",
        func=lambda query: _recall_experience(query, session_id=session_id),
        args_schema=RecallExperienceArgs,
    )
```

- [ ] **Step 5: Register tool behind a runtime flag, session-bound (do NOT add to static tuples)**

Modify `app/tools/__init__.py` — add a flag-gated, **session-bound** helper instead of mutating
the `*_LOCAL_TOOLS` tuples (those are import-time constants that can't honor a toggle):

```python
from app.config import config
from app.tools.recall_experience import build_recall_experience_tool


def experience_tools(session_id: str = "") -> tuple:
    """L1 recall tool bound to the real scoped session, gated by the feature flag (#7 fix)."""
    if not config.long_term_memory_enabled:
        return ()
    return (build_recall_experience_tool(session_id),)
```

Add `experience_tools` to `__all__`.

**Thread the real `session_id` from `run()` into `get_tools()` (#7 fix).** In
`app/agent/experts/base.py`, `ToolCallingExpert.get_tools` currently takes no args and `run()`
calls `await self.get_tools()`. Change to:

```python
# base.py
async def get_tools(self, *, session_id: str = "") -> list[RuntimeTool]:
    return []
...
# inside run(), where tools are loaded:
tools = await self.get_tools(session_id=session_id)
```

Then each expert that exposes recall accepts and forwards `session_id`:

- `diagnosis.py`:
  `async def get_tools(self, *, session_id: str = ""):`
  `return await collect_tools((*DIAGNOSIS_LOCAL_TOOLS, *experience_tools(session_id)), mcp_server=("monitor", "cls"))`
  (Task 6 Step 4 later splices `*service_knowledge_tools()` into this same call — keep ordering in mind.)
- `metric.py`:
  `async def get_tools(self, *, session_id: str = ""):`
  `return await collect_tools((*METRIC_LOCAL_TOOLS, *experience_tools(session_id)), mcp_server="monitor")`
- `log.py`:
  `async def get_tools(self, *, session_id: str = ""):`
  `return await collect_tools((*LOG_LOCAL_TOOLS, *experience_tools(session_id)), mcp_server="cls")`
- `knowledge.py` / `change.py`: just widen the signature to `get_tools(self, *, session_id: str = "")`
  so the overridden method matches the base (even if they ignore `session_id`).

The `ExpertAgent` Protocol only declares `run`, so it needs no change for `get_tools`. The
`session_id` passed into `run()` is already the scoped `owner:{owner_key}:{session_id}` from
[assistant.py:21](../../../app/api/assistant.py) → a stable per-conversation key, exactly what
hit-count dedup needs.

This keeps the static tuples (and `test_diagnosis_expert_has_broad_local_tool_set`) unchanged,
makes the tool appear/disappear with `config.long_term_memory_enabled`, and gives the recall
service the true session id without exposing it to the LLM.

- [ ] **Step 6: Verify**

Run:

```powershell
python -m pytest tests/test_recall_experience_tool.py tests/test_experts.py -q
```

Expected: PASS. (The existing `test_diagnosis_expert_has_broad_local_tool_set` still asserts on
the unchanged static tuple, so it must NOT be modified to expect `recall_experience`.)

- [ ] **Step 7: Commit**

Run:

```powershell
git add app/services/experience_memory_index_service.py app/tools/recall_experience.py app/tools/__init__.py tests/test_recall_experience_tool.py tests/test_experience_memory_index_service.py tests/test_experts.py
git commit -m "feat: add experience recall tool"
```

---

## Task 4: Memory API For L1 Feedback And Governance

**Files:**
- Create: `app/api/memory.py`
- Modify: `app/main.py`
- Test: `tests/test_memory_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_memory_api.py`:

```python
import pytest

from app.api import memory as memory_api


class FakeExperienceService:
    def __init__(self):
        self.feedback_calls = []
        self.manual_calls = []

    def create_from_feedback(self, **kwargs):
        self.feedback_calls.append(kwargs)
        return "exp-feedback"

    def create_manual(self, **kwargs):
        self.manual_calls.append(kwargs)
        return "exp-manual"

    def list(self, *, project_id, enabled=None, limit=50):
        return [{"experience_id": "exp-1", "project_id": project_id, "enabled": True}]

    def get(self, experience_id):
        return {"experience_id": experience_id, "project_id": "p1"}

    def update(self, experience_id, *, enabled=None, confidence=None):
        return True

    def rebuild_index(self, *, project_id=None):
        return 1


@pytest.mark.asyncio
async def test_memory_feedback_creates_experience(monkeypatch, api_client):
    fake = FakeExperienceService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake)

    response = await api_client.post(
        "/api/memory/feedback",
        headers={"X-Session-Owner": "owner-a"},
        json={
            "session_id": "s1",
            "user_message": "api slow",
            "assistant_answer": "db pool",
            "user_accepted": True,
            "actual_root_cause": "db pool exhausted",
            "final_resolution": "increase pool",
            "events": [{"type": "tool_event", "evidence_id": "metric-1", "summary": "p95 high"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["experience_id"] == "exp-feedback"
    assert fake.feedback_calls[0]["project_id"] == "super_biz_agent"


@pytest.mark.asyncio
async def test_manual_experience_endpoint(monkeypatch, api_client):
    fake = FakeExperienceService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake)

    response = await api_client.post(
        "/api/memory/experiences",
        json={"symptoms": "api slow", "root_cause": "db", "resolution": "fix db"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["experience_id"] == "exp-manual"
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_memory_api.py -q
```

Expected: FAIL because `app.api.memory` is missing or not registered.

- [ ] **Step 3: Implement API**

Create `app/api/memory.py` with routes:

```python
POST /api/memory/feedback
POST /api/memory/experiences
GET /api/memory/experiences
GET /api/memory/experiences/{experience_id}
PATCH /api/memory/experiences/{experience_id}
POST /api/memory/experiences/rebuild-index
```

Use `MemoryFeedbackRequest`, `ManualExperienceCreateRequest`, and `ExperienceMemoryUpdateRequest`. Require `X-Session-Owner` only for `/memory/feedback`, so governance scripts can call manual endpoints without user-session ownership in local development.

Return shape:

```python
{"code": 200, "message": "success", "data": {...}}
```

- [ ] **Step 4: Register route**

Modify `app/main.py`:

```python
from app.api import assistant, chat, file, health, memory
app.include_router(memory.router, prefix="/api", tags=["long-term-memory"])
```

- [ ] **Step 5: Verify**

Run:

```powershell
python -m pytest tests/test_memory_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add app/api/memory.py app/main.py tests/test_memory_api.py
git commit -m "feat: add memory feedback and governance api"
```

---

## Task 4b: Feedback Capture Wiring (evidence timeline + weak acceptance)

> **Why this task exists:** without it, Task 2's `create_weak_acceptance` is dead code and
> `evidence_summary` is always empty — defeating PRD v2 #1 (evidence chain) and #2 (anti-starvation).
> The feedback endpoint must dispatch by acceptance level, and the frontend must resend the timeline.

**Files:**
- Modify: `app/api/memory.py`
- Modify: `frontend/src/components/ChatWorkspace.tsx`
- Modify: `frontend/src/api/agentStream.ts`
- Test: `tests/test_memory_api.py`

- [ ] **Step 1: Backend dispatch test (strong vs weak)**

Append to `tests/test_memory_api.py`:

```python
@pytest.mark.asyncio
async def test_feedback_dispatches_weak_vs_strong(monkeypatch, api_client):
    calls = {"strong": 0, "weak": 0}

    class FakeExp:
        def create_from_feedback(self, **kw):
            calls["strong"] += 1
            return "exp-strong"

        def create_weak_acceptance(self, **kw):
            calls["weak"] += 1
            return "exp-weak"

    monkeypatch.setattr(memory_api, "experience_memory_service", FakeExp())

    base = {"user_message": "api slow", "events": [], "user_accepted": True}
    await api_client.post("/api/memory/feedback", headers={"X-Session-Owner": "o"},
                          json={**base, "acceptance_level": "strong"})
    await api_client.post("/api/memory/feedback", headers={"X-Session-Owner": "o"},
                          json={**base, "acceptance_level": "weak"})

    assert calls == {"strong": 1, "weak": 1}
```

- [ ] **Step 2: Wire dispatch in `/api/memory/feedback`**

In `app/api/memory.py`, the feedback handler decides which write path to take:

```python
if not body.user_accepted:
    experience_id = ""               # record nothing to experience memory
elif body.acceptance_level == "weak":
    experience_id = experience_memory_service.create_weak_acceptance(...)  # confidence=weak
else:
    experience_id = experience_memory_service.create_from_feedback(...)    # confidence=high
```

Both write paths pass `events=body.events` so the service distills `evidence_summary` from the
resent timeline (Task 2).

- [ ] **Step 3: Frontend — retain and resend the `complete` event timeline**

The router's `complete` event already carries `events` (see [router_service.py:275](../../../app/services/router_service.py)).
In `frontend/src/api/agentStream.ts` / `ChatWorkspace.tsx`:
- Keep the `events` array from the `complete` event in the message state.
- Add an "采纳 / 纠正根因" affordance on a completed diagnosis answer that POSTs
  `/api/memory/feedback` with `{ user_message, assistant_answer, user_accepted: true,
  acceptance_level: "strong", actual_root_cause?, final_resolution?, events }` and the
  `X-Session-Owner` header.

- [ ] **Step 4: Weak-acceptance trigger (explicit client signal, not server inference)**

Fully-automatic "user didn't follow up ⇒ weak accept" inference is **deferred** (needs reliable
end-of-session / no-correction detection). For this version the weak signal is an **explicit but
low-friction client action**: when the user dismisses the answer or starts a new topic without
correcting, the frontend fires one `/api/memory/feedback` with `acceptance_level: "weak"`,
`user_accepted: true`, and the retained `events`. Document this clearly; do not silently infer.

- [ ] **Step 5: Verify**

```powershell
python -m pytest tests/test_memory_api.py -q
```

Expected: PASS. (Frontend change verified manually via the assistant UI in Task 10.)

- [ ] **Step 6: Commit**

```powershell
git add app/api/memory.py frontend/src/components/ChatWorkspace.tsx frontend/src/api/agentStream.ts tests/test_memory_api.py
git commit -m "feat: wire feedback evidence timeline and weak acceptance"
```

---

## Task 5: L2 Service Knowledge Store

**Files:**
- Create: `app/services/service_knowledge_service.py`
- Test: `tests/test_service_knowledge_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_service_knowledge_service.py`:

```python
from app.services.service_knowledge_service import ServiceKnowledgeService


def test_upsert_service_and_baseline_lookup(tmp_path):
    service = ServiceKnowledgeService(db_path=tmp_path / "memory.db")

    service.upsert_service(
        project_id="p1",
        service_name="checkout-api",
        environment="prod",
        owner_team="payments",
        description="checkout service",
    )
    service.upsert_baseline(
        project_id="p1",
        service_name="checkout-api",
        environment="prod",
        metric_name="cpu",
        min_value=10,
        max_value=70,
        unit="percent",
        sample_window="7d",
    )

    result = service.lookup(project_id="p1", service_name="checkout-api", environment="prod")

    assert result["service_name"] == "checkout-api"
    assert result["owner_team"] == "payments"
    assert result["baselines"][0]["metric_name"] == "cpu"
    assert result["baselines"][0]["max_value"] == 70


def test_project_isolation(tmp_path):
    service = ServiceKnowledgeService(db_path=tmp_path / "memory.db")
    service.upsert_service(project_id="p1", service_name="api", environment="prod")

    assert service.lookup(project_id="p2", service_name="api", environment="prod") is None
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_service_knowledge_service.py -q
```

Expected: FAIL because service does not exist.

- [ ] **Step 3: Implement service**

Create `app/services/service_knowledge_service.py` with tables:

```sql
CREATE TABLE IF NOT EXISTS services (
    project_id TEXT NOT NULL,
    service_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    owner_team TEXT NOT NULL DEFAULT '',
    owner_user TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, service_name, environment)
);

CREATE TABLE IF NOT EXISTS service_relations (
    project_id TEXT NOT NULL,
    source_service TEXT NOT NULL,
    target_service TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    environment TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_baselines (
    project_id TEXT NOT NULL,
    service_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    min_value REAL NOT NULL,
    max_value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    sample_window TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, service_name, environment, metric_name)
);
```

Expose:

```python
upsert_service(...)
upsert_relation(...)
upsert_baseline(...)
lookup(project_id, service_name, environment="")
compare_metric(project_id, service_name, environment, metric_name, value)
```

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest tests/test_service_knowledge_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add app/services/service_knowledge_service.py tests/test_service_knowledge_service.py
git commit -m "feat: add service knowledge store"
```

---

## Task 6: L2 Lookup Tool And Metric/Log Enrichment

**Files:**
- Create: `app/tools/lookup_service_knowledge.py`
- Modify: `app/tools/__init__.py`
- Modify: `app/agent/experts/metric.py`
- Modify: `app/agent/experts/log.py`
- Test: `tests/test_lookup_service_knowledge_tool.py`
- Update: `tests/test_experts.py`

- [ ] **Step 1: Write failing tool tests**

Create `tests/test_lookup_service_knowledge_tool.py`:

```python
from app.tools.lookup_service_knowledge import _lookup_service_knowledge


class FakeServiceKnowledge:
    def lookup(self, *, project_id, service_name, environment=""):
        return {
            "service_name": service_name,
            "environment": environment or "prod",
            "owner_team": "payments",
            "baselines": [{"metric_name": "cpu", "min_value": 10, "max_value": 70, "unit": "%"}],
            "relations": [{"target_service": "mysql", "relation_type": "depends_on"}],
        }


def test_lookup_service_knowledge_formats_structured_result(monkeypatch):
    monkeypatch.setattr("app.tools.lookup_service_knowledge.service_knowledge_service", FakeServiceKnowledge())
    monkeypatch.setattr("app.tools.lookup_service_knowledge.config.project_id", "p1")

    text = _lookup_service_knowledge("checkout-api", "prod")

    assert "checkout-api" in text
    assert "payments" in text
    assert "cpu" in text
    assert "mysql" in text
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest tests/test_lookup_service_knowledge_tool.py -q
```

Expected: FAIL because the tool does not exist.

- [ ] **Step 3: Implement lookup tool**

Create `app/tools/lookup_service_knowledge.py` as a `RuntimeTool` named `lookup_service_knowledge`. Return a compact text block containing owner, relations, and baselines. If no service exists, return `未找到服务知识`.

- [ ] **Step 4: Register lookup tool behind a runtime flag**

Modify `app/tools/__init__.py` — add a flag-gated helper (same pattern as `experience_tools`):

```python
from app.tools.lookup_service_knowledge import lookup_service_knowledge


def service_knowledge_tools() -> tuple:
    return (lookup_service_knowledge,) if config.service_knowledge_enabled else ()
```

- In `app/agent/experts/diagnosis.py` `get_tools()`, splice in `*service_knowledge_tools()`
  alongside `*experience_tools()` (Task 3 Step 5).
- Optionally add to the change expert's `get_tools()` if topology context helps.
- Do NOT add it to metric/log — those use the enrichment hook (Step 5) instead, so the LLM
  isn't forced to pick yet another tool.

- [ ] **Step 5: Add enrichment hook (must NOT clobber the existing log pipeline)**

⚠️ **`log.py` already overrides `transform_tool_result`** ([app/agent/experts/log.py:43](../../../app/agent/experts/log.py)) to run the large-log clustering/summarization pipeline (`analyze_logs`). `metric.py` has **no** override. So:

First add one **shared helper** (e.g. in `app/tools/__init__.py` or a small `service_enrichment.py`) that is a pure no-op when the flag is off or no service is found:

```python
def _extract_service_name(text: str) -> str:
    for marker in ("service=", "service_name=", "服务="):
        if marker in text:
            return text.split(marker, 1)[1].split()[0].strip(",;")
    return ""


def append_service_baseline(content: str) -> str:
    """Append service baseline/owner block. No-op if flag off or service not found."""
    if not config.service_knowledge_enabled:
        return content
    name = _extract_service_name(content)
    if not name:
        return content
    info = service_knowledge_service.lookup(project_id=config.project_id, service_name=name)
    if not info:
        return content
    # ... format and append "--- 服务知识增强 ---" block (service / baselines / owner_team) ...
    return content + enrichment_block
```

- **`metric.py`**: add a NEW `transform_tool_result` override that returns
  `append_service_baseline(content)` (skip `get_current_time`).
- **`log.py`**: do NOT replace the override — **extend it**. Call the existing pipeline first,
  then enrich its output:

  ```python
  async def transform_tool_result(self, *, tool_name, content, raw, events_sink, trace_id, llm_client):
      processed = await super().transform_tool_result(  # preserve large-log pipeline
          tool_name=tool_name, content=content, raw=raw,
          events_sink=events_sink, trace_id=trace_id, llm_client=llm_client,
      )
      return append_service_baseline(processed)
  ```

  (Keep `log.py`'s current pipeline logic by moving it into `ToolCallingExpert` is overkill;
  simplest is to leave log.py's body as the pipeline and wrap it — but since log.py's override
  *is* the pipeline, instead inline: run the existing `analyze_logs` branch, then
  `append_service_baseline(...)` on the result. Do not drop the pipeline.)

> ⚠️ Note: the `_extract_service_name` marker heuristic is fragile — real Prometheus/log
> summaries rarely contain `service=`. Treat enrichment as best-effort; it must always fall
> back to the original content. Reliable extraction is a follow-up.

- [ ] **Step 6: Verify**

Run:

```powershell
python -m pytest tests/test_lookup_service_knowledge_tool.py tests/test_experts.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add app/tools/lookup_service_knowledge.py app/tools/__init__.py app/agent/experts/metric.py app/agent/experts/log.py tests/test_lookup_service_knowledge_tool.py tests/test_experts.py
git commit -m "feat: add service knowledge lookup and enrichment"
```

---

## Task 7: L2 Memory API And Seed Import

**Files:**
- Modify: `app/api/memory.py`
- Modify: `app/services/service_knowledge_service.py`
- Test: `tests/test_memory_api.py`

- [ ] **Step 1: Add API tests**

Append to `tests/test_memory_api.py`:

```python
@pytest.mark.asyncio
async def test_service_baseline_api(monkeypatch, api_client):
    calls = []

    class FakeServiceKnowledge:
        def upsert_baseline(self, **kwargs):
            calls.append(kwargs)
            return kwargs

    monkeypatch.setattr(memory_api, "service_knowledge_service", FakeServiceKnowledge())

    response = await api_client.put(
        "/api/memory/services/checkout-api/baselines",
        json={
            "service_name": "checkout-api",
            "environment": "prod",
            "metric_name": "cpu",
            "min_value": 10,
            "max_value": 70,
            "unit": "%",
        },
    )

    assert response.status_code == 200
    assert calls[0]["project_id"] == "super_biz_agent"
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest tests/test_memory_api.py -q
```

Expected: FAIL because L2 routes are missing.

- [ ] **Step 3: Add L2 routes**

Add to `app/api/memory.py`:

```python
GET /api/memory/services/{service_name}
PUT /api/memory/services/{service_name}/baselines
POST /api/memory/services/import-seed
```

`import-seed` should call `ServiceKnowledgeService.import_from_monitor_mcp()` and return `{"imported": count}`.

- [ ] **Step 4: Implement seed import**

In `ServiceKnowledgeService`, implement:

```python
async def import_from_monitor_mcp(self, *, project_id: str) -> int:
    from app.agent.mcp_client import get_mcp_client_with_retry

    client = await get_mcp_client_with_retry()
    # NOTE: real signature is call_tool(server_name, tool_name, arguments) — see app/agent/mcp_client.py:87.
    services = await client.call_tool("monitor", "list_all_services", {})
    count = 0
    for item in services if isinstance(services, list) else []:
        self.upsert_service(
            project_id=project_id,
            service_name=str(item.get("service_name") or item.get("name") or ""),
            environment=str(item.get("environment") or "prod"),
            owner_team=str(item.get("owner_team") or item.get("team") or ""),
            description=str(item.get("description") or ""),
        )
        count += 1
    return count
```

- [ ] **Step 5: Verify**

Run:

```powershell
python -m pytest tests/test_memory_api.py tests/test_service_knowledge_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add app/api/memory.py app/services/service_knowledge_service.py tests/test_memory_api.py
git commit -m "feat: add service knowledge api"
```

---

## Task 8: L3 User Preference Store And Prompt Injection

**Files:**
- Create: `app/services/user_preference_service.py`
- Modify: `app/agent/experts/base.py`
- Modify: `app/services/router_service.py`
- Modify: `app/api/assistant.py`
- Test: `tests/test_user_preference_service.py`
- Update: `tests/test_router_service.py`
- Update: `tests/test_assistant_api.py`

- [ ] **Step 1: Write preference service tests**

Create `tests/test_user_preference_service.py`:

```python
from app.services.user_preference_service import UserPreferenceService


def test_user_preferences_are_owner_isolated(tmp_path):
    service = UserPreferenceService(db_path=tmp_path / "memory.db")

    service.upsert(
        owner_key="owner-a",
        default_environment="prod",
        language="zh-CN",
        detail_level="brief",
        focused_services=["checkout-api"],
        notes="先看指标",
    )

    assert service.get("owner-a")["default_environment"] == "prod"
    assert service.get("owner-a")["focused_services"] == ["checkout-api"]
    assert service.get("owner-b") is None


def test_format_preferences_for_prompt(tmp_path):
    service = UserPreferenceService(db_path=tmp_path / "memory.db")
    service.upsert(owner_key="owner-a", default_environment="prod", language="zh-CN")

    text = service.format_for_prompt("owner-a")

    assert "用户偏好" in text
    assert "prod" in text
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_user_preference_service.py -q
```

Expected: FAIL because service does not exist.

- [ ] **Step 3: Implement preference service**

Create `app/services/user_preference_service.py` with table:

```sql
CREATE TABLE IF NOT EXISTS user_preferences (
    owner_key TEXT PRIMARY KEY,
    default_environment TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'zh-CN',
    detail_level TEXT NOT NULL DEFAULT 'normal',
    focused_services_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
```

Expose:

```python
upsert(owner_key, default_environment="", language="zh-CN", detail_level="normal", focused_services=None, notes="")
get(owner_key)
format_for_prompt(owner_key)
```

- [ ] **Step 4: Add Expert context support**

Modify `ToolCallingExpert.run` in `app/agent/experts/base.py`:

```python
def run(self, *, message: str, session_id: str, trace_id: str, context: str = ""):
```

When building messages, include context in the system prompt:

```python
system_content = self.system_prompt
if context:
    system_content = f"{system_content}\n\n{context}"
```

Update the `ExpertAgent` protocol signature to match.

- [ ] **Step 5: Inject preferences from router**

Modify `RouterService.stream`:

```python
async def stream(self, message: str, session_id: str, owner_key: str = ""):
```

Before `_iter_expert`, load:

```python
preference_context = user_preference_service.format_for_prompt(owner_key) if owner_key else ""
```

Modify `_iter_expert` to pass:

```python
expert.run(message=message, session_id=session_id, trace_id=session_id, context=preference_context)
```

Modify `RouterService.answer` to accept `owner_key: str = ""` and pass it through.

- [ ] **Step 6: Pass owner key from API**

Modify `app/api/assistant.py`:

```python
async for event in router_service.stream(
    request.question, session_id=scoped_session_id, owner_key=owner_key
):
```

- [ ] **Step 7: Verify**

Run:

```powershell
python -m pytest tests/test_user_preference_service.py tests/test_router_service.py tests/test_assistant_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add app/services/user_preference_service.py app/agent/experts/base.py app/services/router_service.py app/api/assistant.py tests/test_user_preference_service.py tests/test_router_service.py tests/test_assistant_api.py
git commit -m "feat: inject user preferences into experts"
```

---

## Task 9: L3 Preference API

**Files:**
- Modify: `app/api/memory.py`
- Test: `tests/test_memory_api.py`

- [ ] **Step 1: Add API tests**

Append to `tests/test_memory_api.py`:

```python
@pytest.mark.asyncio
async def test_preference_api_uses_owner_key(monkeypatch, api_client):
    calls = []

    class FakePreferenceService:
        def upsert(self, **kwargs):
            calls.append(kwargs)
            return kwargs

        def get(self, owner_key):
            return {"owner_key": owner_key, "default_environment": "prod"}

    monkeypatch.setattr(memory_api, "user_preference_service", FakePreferenceService())

    response = await api_client.put(
        "/api/memory/preferences",
        headers={"X-Session-Owner": "owner-a"},
        json={"default_environment": "prod", "language": "zh-CN"},
    )

    assert response.status_code == 200
    assert calls[0]["owner_key"]
    assert calls[0]["default_environment"] == "prod"
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest tests/test_memory_api.py -q
```

Expected: FAIL because preference routes do not exist.

- [ ] **Step 3: Add preference routes**

Add to `app/api/memory.py`:

```python
GET /api/memory/preferences
PUT /api/memory/preferences
```

Both routes must require `X-Session-Owner` via `require_session_owner`.

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest tests/test_memory_api.py tests/test_user_preference_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add app/api/memory.py tests/test_memory_api.py
git commit -m "feat: add explicit user preference api"
```

---

## Task 10: Governance Semantics And Regression Verification

**Files:**
- Modify as needed based on failures from earlier tasks.
- Test: targeted suite plus relevant regression tests.

- [ ] **Step 1: Verify L1**

Run:

```powershell
python -m pytest tests/test_memory_safety.py tests/test_experience_memory_service.py tests/test_experience_memory_index_service.py tests/test_recall_experience_tool.py tests/test_memory_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Verify L2**

Run:

```powershell
python -m pytest tests/test_service_knowledge_service.py tests/test_lookup_service_knowledge_tool.py tests/test_experts.py -q
```

Expected: PASS.

- [ ] **Step 3: Verify L3 and router integration**

Run:

```powershell
python -m pytest tests/test_user_preference_service.py tests/test_router_service.py tests/test_assistant_api.py -q
```

Expected: PASS.

- [ ] **Step 4: Verify existing core routes**

Run:

```powershell
python -m pytest tests/test_backend_agent_gateway_stream.py tests/test_backend_agent_router.py tests/test_vector_search_and_knowledge_tool.py tests/test_tool_calling.py -q
```

Expected: PASS.

- [ ] **Step 5: Run broad suite**

Run:

```powershell
python -m pytest -q
```

Expected: PASS, except for tests that require unavailable external Milvus/MCP services. If any fail due to local service availability, record the exact test names and error messages before handoff.

- [ ] **Step 6: Review diff**

Run:

```powershell
git diff -- app/config.py app/models/memory.py app/services/memory_safety.py app/services/experience_memory_service.py app/services/experience_memory_index_service.py app/services/service_knowledge_service.py app/services/user_preference_service.py app/tools/recall_experience.py app/tools/lookup_service_knowledge.py app/tools/__init__.py app/agent/experts/base.py app/agent/experts/metric.py app/agent/experts/log.py app/services/router_service.py app/api/assistant.py app/api/memory.py app/main.py
```

Expected: Diff only contains long-term memory implementation and necessary integration changes.

- [ ] **Step 7: Commit final verification fixes**

If Step 1 through Step 6 required fixes, commit them:

```powershell
git add app tests
git commit -m "test: verify long-term memory integration"
```

If no files changed during verification, do not create an empty commit.

---

## Self-Review Notes

Spec coverage:

- L1 experience cards, merge/dedup, recall tool, project isolation, and graceful Milvus degradation: Tasks 2–3. **Evidence-timeline distillation and weak-acceptance/strong dispatch are wired in Task 4b** (the endpoint + frontend resend), not just declared.
- L2 service list, topology/baseline store, manual baseline maintenance, seed import, lookup tool, and metric/log result enrichment: Tasks 5–7. **Enrichment in `log.py` extends the existing large-log pipeline (does not replace it); `metric.py` adds a fresh override (Task 6 Step 5).**
- L3 explicit settings, owner isolation, and Expert prompt injection: Tasks 8–9.
- Governance API, disable/update, rebuild index, conflict labels, hit-count support, redaction: Tasks 1, 4, 10.
- **Feature flags (`long_term_memory_enabled` / `service_knowledge_enabled`) are enforced at runtime** via flag-gated `experience_tools()` / `service_knowledge_tools()` helpers inside each expert's `get_tools()` (Task 3 Step 5, Task 6 Step 4) — NOT baked into the static `*_LOCAL_TOOLS` tuples — so memory stays a switchable feature per the 06-17 simplification.

Known exclusions / limitations:

- Automatic baseline learning is intentionally excluded.
- Dialogue-based preference extraction is intentionally excluded.
- Old OnCall Planner memory injection is intentionally excluded.
- Complex same-symptom conflict graph/versioning is excluded; recall only labels conflicts.
- **Weak acceptance is an explicit client signal (Task 4b Step 4), not server-side inference** of "user didn't follow up". Automatic inference is deferred.

Resolved design points:

- **Recall now sees the real scoped `session_id`** (was #7). RuntimeTool handlers only receive
  LLM-supplied arguments (`make_runtime_tool` → `func(**arguments)`, [runtime_tools.py:49](../../../app/core/runtime_tools.py)),
  so instead of exposing `session_id` to the model, the recall tool is built **per run** via
  `build_recall_experience_tool(session_id)` and wired through `get_tools(session_id=...)` ←
  `run(session_id=...)` (Task 3 Steps 4–5). Hit-count session-dedup uses the true
  `owner:{owner_key}:{session_id}` key.

Execution note:

- The current worktree already contains many unrelated Router + Expert simplification changes. Each task should stage only the files listed in that task.
- A full `pytest -q` (Task 10 Step 5) may show pre-existing failures from already-deleted legacy aiops modules in this worktree; record them but do not attribute them to this plan.
