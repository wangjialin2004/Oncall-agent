# Router Experts Simplification Design

Date: 2026-06-17

## Goal

Make the Router + Expert Agents architecture the primary operations assistant path.

The system should route each request to a focused expert instead of sending operational questions through the old fixed OnCall pipeline. It should also remove long-term memory from the active product path while preserving short-term session ownership and per-request streaming.

## Existing State

The repository currently has two overlapping architectures:

- the new flat expert router in `app/services/router_service.py`, with `knowledge`, `metric`, `log`, `change`, and `diagnosis` routes,
- the old fixed OnCall pipeline in `app/services/aiops_service.py`, built from `triage`, `planner`, `executor`, `diagnosis`, and `reporter`.

The new `diagnosis` expert still wraps the old pipeline, so complex requests can fall back into the same planner/executor loop the new architecture is meant to avoid.

The repository also has persistent memory systems:

- diagnosis case, tool evidence, and feedback storage in `diagnosis_memory_service.py`,
- long-term experience memory and vector indexing in `experience_memory_service.py` and `experience_memory_index_service.py`,
- feedback APIs that turn diagnosis feedback into reusable experience cards.

These persistence layers add state and product complexity that no longer fits the desired simpler expert-router direction.

## Non-Goals

- Do not remove short-term session identity or session scoping.
- Do not remove vector search, embeddings, document indexing, or knowledge retrieval.
- Do not add autonomous remediation or machine-changing actions.
- Do not physically delete every legacy file in the first implementation pass.
- Do not redesign the frontend visual shell.

## Scope

### Keep

- `/api/assistant` as the primary streamed assistant endpoint.
- `RouterService` as the main backend coordinator.
- `knowledge_expert`, `metric_expert`, `log_expert`, `change_expert`, and `diagnosis_expert`.
- `session_scope_service.py` and `session_id` propagation for short-term request/session isolation.
- RAG document retrieval and vector search as knowledge tools.
- Normalized stream events: `route_event`, `agent_event`, `tool_event`, `decision_event`, `content`, `complete`, and `error`.

### Remove From Active Path

- The old fixed OnCall pipeline from the new diagnosis route.
- `AIOpsService` as a dependency of the expert router and backend agent gateway.
- Diagnosis case creation, case completion, evidence persistence, and feedback persistence.
- Long-term experience memory recall from planning or diagnosis.
- `/api/memory/*` and diagnosis feedback APIs from the product path.

### Soft Removal Strategy

This change should first remove legacy systems from runtime paths and tests without physically deleting every old module.

Legacy files may remain temporarily if they are not imported by the active router, expert, or gateway paths. A later cleanup can delete old modules and historical tests once the simplified architecture is stable.

## Architecture

The target request path is:

```text
/api/assistant
  -> RouterService
      -> knowledge_expert
      -> metric_expert
      -> log_expert
      -> change_expert
      -> diagnosis_expert
  -> streamed answer
```

`diagnosis_expert` becomes a normal expert. It should use the shared tool-calling loop or a small dedicated orchestration function, but it must not call `aiops_service.execute()`.

For cross-domain incidents, the router should still choose `diagnosis`. The diagnosis expert can use a broad but explicit tool set:

- knowledge retrieval,
- current time,
- alert and metric tools,
- log tools via the `cls` MCP server where available,
- change tools.

The diagnosis expert should produce a concise, evidence-based answer. It may recommend follow-up expert routes when evidence is missing, but it should not internally run a fixed planner/executor/reporter pipeline.

## Data Flow

1. The API receives a user message and scoped session ID.
2. `RouterService` emits a `route_event`.
3. The selected expert emits start/completion agent events and tool events.
4. Content chunks are streamed as the expert answer.
5. The final `complete` event includes route, answer, case ID as an empty string, and the collected event timeline.

No diagnosis case or experience memory record is created. Tool evidence remains in the streamed timeline only.

## Error Handling

- Router semantic classification failures fall back to `diagnosis`.
- Expert failures emit a degraded `agent_event` and a user-visible content fallback.
- Expert timeouts keep the existing router timeout fallback behavior.
- Missing MCP servers degrade to local tools only.
- Missing change data continues to be explicit in the change expert response.

## API Changes

`/api/assistant` remains the recommended endpoint.

The following endpoints should be removed from active frontend/backend usage:

- `/api/aiops/feedback`
- `/api/aiops/cases/{case_id}/feedback`
- `/api/memory/experiences`
- `/api/memory/experiences/{experience_id}`
- `/api/memory/experiences/rebuild-index`

If compatibility is needed for one release, these endpoints can return a clear disabled response instead of silently persisting data.

`case_id` remains in event payloads only as an optional compatibility field and should default to an empty string in new responses.

## Testing

Update or add tests around the simplified active path:

- router routes each intent to exactly one expert,
- diagnosis expert does not import or call `aiops_service`,
- `/api/assistant` streams route, expert events, content, and complete events,
- memory and feedback APIs are no longer active or return disabled responses,
- short-term session scoping still works,
- vector search and knowledge retrieval continue to work.

Legacy tests for `AIOpsService`, diagnosis memory, and experience memory should either be removed from the required suite or moved into legacy-only coverage until the files are physically deleted.

## Migration Notes

This is intentionally a simplification, not a data migration.

Existing SQLite memory databases can be left untouched on disk, but the application should no longer read or write them on the active path. If a later product direction needs long-term memory again, it should be reintroduced behind an explicit feature decision rather than remaining as hidden behavior.

## Success Criteria

- Operational questions no longer enter the old fixed OnCall pipeline through the new router.
- `diagnosis_expert` answers through the expert architecture, not through `AIOpsService`.
- No active request creates diagnosis cases, evidence rows, feedback rows, or experience memories.
- Short-term session identity still works for streamed requests.
- Existing knowledge retrieval remains available to experts.
