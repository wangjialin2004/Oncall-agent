# Remove Legacy Context Snapshot Service Progress

## Status

Implemented in the worktree. `ContextRepository` is now the default runtime
owner for context loading, inflight recovery, and atomic turn/projection
commit. The standalone `app/services/context_snapshot_service.py` and its
service-only test file were removed. Canonical `conversation_turns` and the
existing compact projection columns were retained.

## Behavioural Record

- `harness_unified_context_repository_enabled` now defaults to `true`.
- Production code has no import or reference to `ContextSnapshotService` or
  `context_snapshot_service`.
- The retired `ContextStateStore` remains callback-injected test compatibility
  code only; it no longer creates a database snapshot service itself.
- A checkpoint ref using the `context-inflight:` prefix restores from the
  repository's Redis inflight record. A legacy ref continues from committed
  projection/turns and emits the existing degraded rehydrate status.
- No SSE event `type`, authentication behaviour, SQLite schema, or stored
  conversation turn was changed or deleted.

## Verification Evidence

| Check | Command | Result |
|---|---|---|
| Focused context, migration, checkpoint, harness regression | `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py tests/test_context_repository.py tests/test_context_store.py tests/test_context_integration.py tests/test_audit_context_storage.py tests/test_context_projection_migration.py tests/test_m1_w4_latency_exit.py tests/test_harness_stateful_context.py --no-cov` | 69 passed in 1.09s on 2026-08-01 rerun |
| Automated API persistence/reload e2e | `tests/test_context_unified_wiring.py::test_assistant_two_turn_unified_e2e_persists_compact_projection` | passed; API -> harness -> atomic commit -> second-turn reload |
| Legacy checkpoint degradation | `tests/test_context_unified_wiring.py::test_legacy_checkpoint_ref_uses_committed_context_with_warning` | passed |
| Retired service import guard | `tests/test_context_unified_wiring.py::test_production_modules_do_not_import_retired_snapshot_service` | passed |
| Compile | `PYTHONPATH=. python -m compileall -q app tests` | passed |
| Ruff | `.venv/bin/ruff check` over all touched Python modules/tests | passed |
| Live schema status | `.venv/bin/python scripts/migrate_database.py status --db volumes/long_term_memory.db` | version 3 current, checksums/schema OK |
| Live data dry-run | `.venv/bin/python scripts/migrate_context_projection.py dry-run --db volumes/long_term_memory.db` | `status=ok`, `mutated=false`; planned rebuild `41`, planned clear `17`; no data was written |

## Operator Decision (2026-08-01)

The read-only projection audit reported historical drift (`41` projections
planned for rebuild from canonical turns and `17` untrusted snapshots planned
for clearing). The operator explicitly chose **not** to rebuild, compact, or
clear these records in this rollout. The database was left unchanged; the
canonical `conversation_turns` source and repository fallback/rebuild behavior
remain the recovery path when a session is loaded. This is an intentional
operational decision, not a failed dry-run.

## Live Runtime Result

The current local FastAPI process is listening on port 9900 and the read-only
readiness check returns HTTP 200 with `status=ready` and no issues. The focused
suite was rerun after this decision with `69 passed`. A real authenticated
two-turn request was not issued in this check, so the scripted API e2e remains
the semantic persistence evidence for this rollout.

## Remaining Risk and Rollback

Old checkpoint refs no longer retrieve an independent legacy snapshot. They
degrade to the last committed projection/turn history, which is intentional
and covered by the focused test. Restore the deleted service and revert the
unified default only if an operator requires the retired independent snapshot
semantics; no SQLite data recovery is needed.
