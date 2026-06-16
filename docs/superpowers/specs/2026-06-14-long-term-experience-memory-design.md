# Long-Term Experience Memory Design

Date: 2026-06-14

## Goal

Build a governed long-term experience memory for the AIOps agent.

The system should learn only from user-approved diagnosis feedback, store the resulting experience as a reusable diagnosis asset, and let the Planner recall similar historical incidents before creating a new plan.

This design covers the recommended version: a manageable experience library backed by SQLite and Milvus, with deduplication, project isolation, Planner consumption, and basic governance APIs.

## Existing Context

The project already has three relevant persistence surfaces:

- LangGraph checkpoints are persisted through SQLite, using `volumes/checkpoints.db`.
- Diagnosis cases, tool evidence, and feedback are persisted by `DiagnosisMemoryService` in `data/diagnosis_memory.sqlite3`.
- RAG documents are indexed in Milvus through the existing vector store and search services.

The missing layer is a long-term diagnosis experience memory that the agent can actively reuse. The existing diagnosis tables are an archive of what happened. The new experience memory should represent what has been verified and is worth using again.

## Memory Layers

The memory system has three layers:

- Short-term memory: LangGraph checkpoint state for active RAG and AIOps conversations.
- Medium-term memory: diagnosis case, tool evidence, and feedback records in SQLite.
- Long-term memory: verified diagnosis experience cards generated from accepted feedback.

Only the long-term memory layer is added in this design. It should not replace the diagnosis archive or the checkpoint state.

The long-term flow is:

```text
user submits accepted feedback
  -> read case, evidence, and feedback
  -> generate an experience card
  -> save authoritative record in SQLite
  -> upsert searchable symptoms vector into Milvus
  -> Planner recalls similar experience in future diagnosis
  -> high-quality hits make the first step verify the historical root cause
```

## Feedback Semantics

`user_accepted=true` is the admission gate for long-term memory.

For this version, it has a deliberate dual meaning:

- the user accepts the diagnosis result for this case;
- the user allows this diagnosis experience to be persisted into long-term memory and reused for similar future incidents.

If `user_accepted=false`, the feedback is still stored in the diagnosis feedback table, but no long-term experience is generated.

A future API version may split this into two fields:

- `user_accepted`: whether the user accepts the diagnosis result;
- `persist_to_memory`: whether the user allows long-term memory persistence.

## Storage Strategy

Use SQLite plus Milvus:

- SQLite is the source of truth for experience records, governance state, statistics, and traceability.
- Milvus is the semantic recall index for symptoms similarity search.

This keeps memory both useful and governable. Milvus helps the agent "remember" similar incidents. SQLite keeps the memory auditable and editable.

## Project Isolation

Long-term experience memory must not mix with existing RAG document chunks.

Use collection-level isolation:

```text
existing RAG collection
  -> operational documents, knowledge articles, FAQ chunks

experience_memory
  -> long-term diagnosis experience cards
```

Experience recall only queries the `experience_memory` collection. Normal RAG document retrieval continues to query the existing RAG collection.

Also add project-level isolation inside `experience_memory`:

- `project_id`
- `environment`
- `service_name`
- `memory_type`

`project_id` is required. The default for this project should be `super_biz_agent`. `memory_type` should be `diagnosis_experience`.

Every Milvus search for experience memory must filter by:

```text
project_id == current project
enabled == true
memory_type == "diagnosis_experience"
```

This prevents one project's historical incidents from being recalled for another project.

## SQLite Data Model

Create a new SQLite table, `experience_memories`.

Suggested fields:

```text
experience_id TEXT PRIMARY KEY
project_id TEXT NOT NULL
environment TEXT
service_name TEXT
symptoms TEXT NOT NULL
root_cause TEXT NOT NULL
resolution TEXT NOT NULL
evidence_summary TEXT NOT NULL
source_case_ids_json TEXT NOT NULL
source_feedback_ids_json TEXT NOT NULL
confidence REAL NOT NULL
hit_count INTEGER NOT NULL DEFAULT 0
success_count INTEGER NOT NULL DEFAULT 0
enabled INTEGER NOT NULL DEFAULT 1
milvus_pk TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

Field meanings:

- `experience_id`: stable identifier for the long-term experience.
- `project_id`: project or tenant scope. Required for recall isolation.
- `environment`: optional environment label such as `prod`, `staging`, or `local`.
- `service_name`: optional affected service name.
- `symptoms`: core searchable symptom description. This is the main embedding text.
- `root_cause`: verified historical root cause.
- `resolution`: effective historical remediation.
- `evidence_summary`: compact supporting evidence with tool names and evidence IDs, not raw large logs.
- `source_case_ids_json`: JSON array of diagnosis cases that contributed to this experience.
- `source_feedback_ids_json`: JSON array of accepted feedback records that support this experience.
- `confidence`: trust score used by Planner filtering.
- `hit_count`: number of times the experience was recalled and considered by Planner.
- `success_count`: number of later accepted cases that confirmed this experience was useful.
- `enabled`: governance flag. Disabled memories are not recalled.
- `milvus_pk`: corresponding Milvus primary key, preferably the same as `experience_id`.
- `created_at`: first creation timestamp.
- `updated_at`: last update timestamp.

Example SQLite record:

```json
{
  "experience_id": "exp-20260614-001",
  "project_id": "super_biz_agent",
  "environment": "local",
  "service_name": "milvus",
  "symptoms": "用户反馈智能运维助手响应变慢，日志多次出现 Milvus connection timeout，监控显示 FastAPI 服务 CPU 正常但请求耗时升高，Milvus 连接数持续上升。",
  "root_cause": "Milvus 连接池耗尽，导致向量检索请求排队和超时。",
  "resolution": "重启 Milvus standalone，降低连接创建频率，并复用 Milvus client 连接。",
  "evidence_summary": "tool=query_metrics_alerts evidence_id=metric-001 显示请求耗时 P95 升高；tool=search_app_logs evidence_id=cls-001 多次出现 Milvus connection timeout。",
  "source_case_ids_json": "[\"case-20260614-001\"]",
  "source_feedback_ids_json": "[\"feedback-20260614-001\"]",
  "confidence": 0.8,
  "hit_count": 0,
  "success_count": 0,
  "enabled": 1,
  "milvus_pk": "exp-20260614-001",
  "created_at": "2026-06-14T10:30:00+00:00",
  "updated_at": "2026-06-14T10:30:00+00:00"
}
```

## Milvus Data Model

Create a separate Milvus collection named `experience_memory`.

Suggested fields:

```text
id / pk
experience_id
project_id
environment
service_name
memory_type
symptoms
root_cause
resolution
confidence
enabled
source_case_ids_json
vector
```

Field meanings:

- `id / pk`: Milvus primary key. Prefer using `experience_id` to simplify synchronization.
- `experience_id`: foreign key back to SQLite.
- `project_id`: required project scope filter.
- `environment`: optional environment filter.
- `service_name`: optional service filter.
- `memory_type`: fixed value `diagnosis_experience`.
- `symptoms`: source text used to generate the embedding.
- `root_cause`: compact root cause summary for returned candidates.
- `resolution`: compact remediation summary for returned candidates.
- `confidence`: confidence snapshot for early filtering.
- `enabled`: metadata filter to exclude disabled memories.
- `source_case_ids_json`: source case snapshot for diagnostics and quick display.
- `vector`: embedding of `symptoms`.

Example Milvus record:

```json
{
  "id": "exp-20260614-001",
  "experience_id": "exp-20260614-001",
  "project_id": "super_biz_agent",
  "environment": "local",
  "service_name": "milvus",
  "memory_type": "diagnosis_experience",
  "symptoms": "用户反馈智能运维助手响应变慢，日志多次出现 Milvus connection timeout，监控显示 FastAPI 服务 CPU 正常但请求耗时升高，Milvus 连接数持续上升。",
  "root_cause": "Milvus 连接池耗尽，导致向量检索请求排队和超时。",
  "resolution": "重启 Milvus standalone，降低连接创建频率，并复用 Milvus client 连接。",
  "confidence": 0.8,
  "enabled": true,
  "source_case_ids_json": "[\"case-20260614-001\"]",
  "vector": [0.021, -0.034, 0.118, "... 1024 dims ..."]
}
```

SQLite remains authoritative. If Milvus metadata and SQLite disagree, SQLite wins.

## Experience Generation

Add `ExperienceMemoryService`.

The first version should use rule-based generation with optional LLM enhancement.

Rule-based card generation:

- `symptoms`: combine `case.user_input`, useful final report symptom text, and evidence summaries.
- `root_cause`: prefer `feedback.actual_root_cause`; otherwise extract coarsely from the final report.
- `resolution`: prefer `feedback.final_resolution`; otherwise extract coarsely from the final report.
- `evidence_summary`: combine `tool_evidence.tool_name`, `evidence_id`, and `summary`.

Optional LLM enhancement can improve:

- symptoms summary;
- evidence summary;
- service tags;
- environment tags;
- remediation wording.

LLM failure must not fail feedback submission. The rule-based card is the fallback.

## Write And Merge Flow

Trigger write after `/api/aiops/feedback` successfully stores feedback.

Flow:

```text
POST /api/aiops/feedback
  -> DiagnosisMemoryService.record_feedback(...)
  -> if user_accepted is false: stop
  -> ExperienceMemoryService.create_or_merge_from_feedback(case_id, feedback)
  -> read diagnosis case, tool evidence, and feedback
  -> generate base experience card
  -> optionally enhance card with LLM
  -> search Milvus experience_memory by symptoms
  -> if similar experience has close root cause: merge
  -> otherwise create new experience
```

Deduplication rules:

- Search top 3 similar experiences by `symptoms`.
- Apply filters for `project_id`, `enabled`, and `memory_type`.
- Merge only when similarity is above threshold and `root_cause` is close enough.
- On merge, append source case IDs and feedback IDs, update evidence summary, update `updated_at`, and keep or increase confidence.
- If symptoms are similar but root causes differ, create a separate experience. Conflict handling is out of scope for this version.

Milvus write failure should not fail the feedback API. The SQLite record should remain and the failure should be logged so a later rebuild can restore the index.

## Recall And Planner Consumption

Add a search method such as:

```python
experience_memory_service.search_relevant_experiences(
    query=input_text,
    project_id=config.project_id,
    top_k=3,
)
```

Recall flow:

```text
current user input or alert description
  -> embed as query vector
  -> search Milvus experience_memory
  -> filter by project_id, enabled, and memory_type
  -> get candidate experience_id and similarity
  -> load full records from SQLite
  -> filter disabled, low confidence, and low similarity
  -> increment hit_count for injected experiences
  -> format experience_context for Planner
```

Planner behavior:

- No hit: keep current planning behavior.
- Low-confidence hit: include as weak reference only.
- High-similarity and high-confidence hit: first plan step must verify the historical root cause before normal investigation.

The Planner prompt should make the distinction explicit:

```text
Historical experience is not current fact.
If similarity and confidence are high, first verify the historical root cause.
If verification fails, continue normal investigation.
```

Example Planner context:

```text
## 相关历史经验

[exp-20260614-001]
相似度: 0.86
置信度: 0.80
历史症状: 智能运维助手响应变慢，Milvus timeout 增多。
已验证根因: Milvus 连接池耗尽。
有效处置: 重启 Milvus standalone，降低连接创建频率，并复用 client 连接。
关键证据: metric-001 显示 P95 升高；cls-001 出现 Milvus timeout。
来源 case: case-20260614-001

要求：
- 不要把历史经验直接当成本次事实。
- 高相似且高置信时，第一步优先验证历史根因。
- 验证失败后继续常规排查。
```

## Governance API

Add `app/api/memory.py`.

Suggested endpoints:

```text
GET /api/memory/experiences
```

List memories. Support filters:

- `project_id`
- `enabled`
- `service_name`
- `min_confidence`
- `limit`
- `offset`

Return summary fields:

- `experience_id`
- `symptoms`
- `root_cause`
- `confidence`
- `hit_count`
- `success_count`
- `enabled`
- `updated_at`

```text
GET /api/memory/experiences/{experience_id}
```

Return full detail, including:

- `resolution`
- `evidence_summary`
- source case IDs
- source feedback IDs
- Milvus synchronization state

```text
PATCH /api/memory/experiences/{experience_id}
```

First version supports enabling or disabling:

```json
{
  "enabled": false
}
```

Disabling updates SQLite first and then syncs Milvus metadata. Search still checks SQLite after recall, so disabled memory must not be consumed even if Milvus sync lags.

```text
POST /api/memory/experiences/rebuild-index
```

Rebuild Milvus `experience_memory` from all enabled SQLite experience records. This repairs Milvus data loss, schema changes, or previous synchronization failures.

## Configuration

Add configuration values:

```text
project_id = "super_biz_agent"
experience_memory_collection = "experience_memory"
experience_memory_top_k = 3
experience_memory_similarity_threshold = 0.78
experience_memory_high_confidence_threshold = 0.75
experience_memory_initial_confidence = 0.8
```

Exact thresholds can be adjusted after evaluation.

## Error Handling

- Feedback persistence remains the primary operation.
- Long-term memory generation failure should be logged but should not make `/api/aiops/feedback` fail.
- Milvus search failure should make Planner continue without experience memory.
- Milvus upsert failure should keep the SQLite record and rely on rebuild-index for repair.
- SQLite write failure for experience memory should be reported in logs and metrics, but the original feedback record should remain valid.

## Testing

Required tests:

1. Feedback-triggered memory write:
   - `user_accepted=true` writes feedback and creates or merges an experience.
   - `user_accepted=false` writes feedback only.

2. Experience card generation:
   - `symptoms` is non-empty.
   - `root_cause` prefers `actual_root_cause`.
   - `resolution` prefers `final_resolution`.
   - `evidence_summary` includes tool names and evidence IDs.

3. Deduplication and merge:
   - Similar symptoms and close root cause merge into one experience.
   - Source case and feedback IDs are appended.
   - Similar symptoms with different root cause create a separate experience.

4. Planner recall:
   - Search uses `project_id`, `enabled`, and `memory_type` filters.
   - High-quality hits are injected into Planner context.
   - The first plan step verifies the historical root cause.
   - No hit preserves current Planner behavior.

5. Governance API:
   - List supports project and enabled filters.
   - Detail shows source case and evidence summary.
   - Disabled memory is not recalled.
   - Rebuild-index restores Milvus records from SQLite.

6. Failure handling:
   - Milvus unavailable does not fail feedback submission.
   - Planner continues without memory when Milvus search fails.
   - Different `project_id` values do not recall each other's memories.

## Acceptance Criteria

- Accepted feedback creates a long-term experience record.
- The corresponding symptoms vector is written to Milvus.
- A similar second diagnosis recalls the experience.
- A high-confidence recall makes Planner first verify the historical root cause.
- Disabled memories are not used.
- Milvus failures do not break feedback submission.
- Experiences are isolated by `project_id`.

## Out Of Scope

- Conflict decay for similar symptoms with different root causes.
- Complex version graph between memories.
- Automatic memory deletion.
- Human approval UI changes beyond the existing feedback path.
- A unified Context Assembler across RAG and AIOps.
