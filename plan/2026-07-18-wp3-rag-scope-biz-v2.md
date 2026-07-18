# WP-3 RAG tenant scope and `biz_v2`

> Status: implementation complete; live Milvus dry-run blocked by current connectivity

## Problem

The knowledge collection currently has no scalar tenant scope and every vector
search is global. Uploaded private chunks can therefore be recalled by another
authenticated user. The existing Milvus connector also contains a destructive
dimension-mismatch path that can drop the legacy collection.

## Decisions and defaults

- Add `scope_type`/`scope_id` scalar fields to the new schema (`system`,
  `project`, `user`).
- Build filters only from the authenticated `RequestContext`; missing context
  fails closed when `RAG_TENANT_SCOPE_ENABLED=true`.
- Keep `RAG_COLLECTION_NAME=biz` until an operator completes the explicit
  `biz_v2` migration and switch. `RAG_TENANT_SCOPE_ENABLED` defaults to true;
  `RAG_ALLOW_LEGACY_UNSCOPED` defaults to false.
- `scripts/migrate_rag_scope.py` is read-only by default. `--dry-run` reports
  source/target schema and entity gaps. Apply mode requires `--apply` and never
  drops `biz`; rows without a trustworthy scope are reported for manual review.
- The old collection remains available for rollback and observation.

## Scope

- Request-context binding for the assistant task.
- Milvus schema, collection selection, scoped writes and filtered dense/BM25/
  hybrid reads.
- Safe migration planner/CLI and focused tests.

## Non-goals

- No live migration, collection switch, rebuild, or deletion in this change.
- No change to SSE event types, read-only tool policy, or memory collection.

## Affected files

- `app/config.py`, `.env.example`
- `app/core/request_context.py`, `app/core/milvus_client.py`
- `app/services/vector_store_manager.py`, `app/services/vector_index_service.py`
- `app/services/vector_search_service.py`, `app/tools/knowledge_tool.py`
- `app/api/assistant.py`
- `scripts/migrate_rag_scope.py`
- `tests/test_rag_tenant_scope.py`

## Verification

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_rag_tenant_scope.py -q --no-cov
PYTHONPATH=. .venv/bin/python scripts/migrate_rag_scope.py --dry-run --target biz_v2
PYTHONPATH=. .venv/bin/python -m compileall -q app scripts/migrate_rag_scope.py
git diff --check
```

## Exit criteria

- Scope filters include only system, current project, and current owner.
- Missing context and unscoped legacy rows do not return results by default.
- New writes always carry a validated scope.
- Dry-run never creates, alters, rebuilds, or drops a collection and reports
  connection/schema/entity status.
- Existing targeted regressions remain green.

## Risks and rollback

- Existing `biz` rows lack scalar scope and will be invisible until migration;
  this is an intentional fail-closed degradation.
- Roll back by setting `RAG_TENANT_SCOPE_ENABLED=false` only in an isolated
  local diagnostic environment, or by reverting the application change; keep
  `biz` untouched and do not drop `biz_v2`.

## Progress and verification (2026-07-18)

- Added request-task context binding and filters for `system`, current project,
  stable owner, and the authenticated legacy storage owner during migration.
- Added scoped schema/write paths. System documents default to `system/system`;
  uploaded files write `user/<storage owner>`.
- Removed the connector's automatic collection drop on vector-dimension
  mismatch; it now requires the explicit migration/rebuild path.
- Added a read-only-default migration CLI. The local dry-run returned
  `status=blocked`, `mutated=false` because Milvus at `localhost:19530` is not
  reachable from this sandbox.
- `tests/test_rag_tenant_scope.py` and the handoff regression set: **45 passed**.
- Ruff, compileall, and `git diff --check`: passed.

Deviation: the approved target model uses the new stable owner, but existing
uploaded-file metadata still uses the legacy storage owner until WP-6. The
filter temporarily accepts both values from the same authenticated principal;
no request-body value is trusted.
