# Intelligent AIOps Assistant Completion Design

## Context

The project already implements much of `docs/智能运维助手改造方案.md`: local MCP monitoring, local log tools, unified knowledge retrieval through `vector_search_service`, BM25/hybrid schema support, document extraction, and diagnosis case/evidence persistence.

This design closes the remaining practical gaps without replacing stable existing flows.

## Goals

- Add a unified assistant entry point that can route user input to RAG chat or AIOps diagnosis.
- Make upload/index responses expose explicit indexing status and errors.
- Persist user feedback for diagnosis cases.
- Make health data reflect the actual runtime configuration.
- Cover these behaviors with focused tests before implementation.

## Non-Goals

- Do not rewrite `Planner`, `Executor`, or `Replanner`.
- Do not introduce a background queue for indexing in this iteration.
- Do not remove existing `/api/chat`, `/api/chat_stream`, or `/api/aiops` endpoints.
- Do not require live Milvus, DashScope, or MCP servers for unit tests.

## Architecture

### Router Service

Create `app/services/router_service.py` with a small, deterministic router.

The router returns one of three route decisions:

- `aiops`: user intent mentions alerting, diagnosis, logs, CPU, memory, service unavailable, failures, or troubleshooting.
- `rag`: user asks for knowledge, documents, procedures, explanations, or normal conversation.
- `clarify`: input is empty or too ambiguous to route safely.

The service should expose:

- `route_message(message: str) -> RouteDecision`
- `answer(message: str, session_id: str) -> dict`

`answer` calls `aiops_service.execute(...)` for `aiops` and `rag_agent_service.query(...)` for `rag`. For `clarify`, it returns a short clarification response without calling either downstream service.

### Assistant API

Create `app/api/assistant.py` and register it in `app/main.py`.

Add:

- `POST /api/assistant`

The endpoint accepts the existing chat shape through `ChatRequest` and returns:

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "success": true,
    "route": "rag",
    "answer": "...",
    "errorMessage": null
  }
}
```

If the route is `aiops`, the answer is the final response collected from `aiops_service.execute`. Streaming can remain on the existing `/api/chat_stream` and `/api/aiops` endpoints for this iteration.

### Indexing Status

Keep upload indexing synchronous, but return explicit status fields.

Modify `VectorIndexService.index_single_file` to return a small result object or dictionary containing:

- `status`: `completed`, `failed`, or `skipped`
- `chunk_count`
- `error_message`

Modify `POST /api/upload` to include:

- `indexing_status`
- `indexing_error`
- `indexed_chunks`

When indexing fails, the file upload still succeeds, but `indexing_status` is `failed` and `indexing_error` contains the indexing error.

### Diagnosis Feedback Memory

Extend `DiagnosisMemoryService` with a `diagnosis_feedback` table.

Persist:

- `case_id`
- `session_id`
- `user_accepted`
- `actual_root_cause`
- `final_resolution`
- `comment`
- `created_at`

Expose:

- `record_feedback(...) -> None`
- `list_feedback(case_id: str) -> list[dict]`

This supports the business memory requirement for user approval, actual root cause, and final handling.

### Health Configuration

Extend `Settings` with:

- `monitor_target_mode: str = "self"`
- `log_provider: str = "local"`

Modify `build_health_data()` so:

- `rag.retrieval_mode` uses `config.rag_retrieval_mode`
- `rag.dense_weight` and `rag.bm25_weight` are present
- `monitor.target_mode` uses `config.monitor_target_mode`
- `logs.provider` uses `config.log_provider`

Milvus remains the only dependency that makes `/health` return HTTP 503 when unavailable. Missing LLM config and unreachable MCP servers are reported in detail but do not make the app unavailable.

## Testing Strategy

Use TDD for every behavior change.

Add focused tests:

- Router classification and assistant dispatch without live LLM calls.
- Upload response reports `completed` and `failed` indexing states.
- `DiagnosisMemoryService` persists and lists feedback.
- Health data reflects `rag_retrieval_mode`, retrieval weights, monitor mode, and log provider.

Run the full test suite after each implementation slice and at the end.

## Rollout

Existing API clients keep working because all existing endpoints remain unchanged. The new `/api/assistant` endpoint is opt-in. Upload response gains additive fields under `data`, so existing consumers can ignore them.

## Completion Evidence

Completion requires:

- New tests fail before implementation and pass after implementation.
- Full `python -m pytest` passes.
- Current code inspection confirms the four approved gaps are implemented.
- The final audit against `docs/智能运维助手改造方案.md` shows no remaining gap in the approved completion scope.
