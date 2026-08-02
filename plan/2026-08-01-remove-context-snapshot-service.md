# Remove Legacy Context Snapshot Service Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with verification after each runtime boundary change.

**Goal:** Remove `ContextSnapshotService` as a runtime storage owner and make `ContextRepository` the only context load/commit path.

**Architecture:** `conversation_turns` remains the canonical immutable audit source. `ContextRepository` owns compact projection persistence, committed Redis cache, inflight recovery, and the atomic completed-turn commit. The legacy `ContextStateStore`/snapshot callback path is removed from production wiring; existing `context_state_json` and checkpoint metadata remain readable for migration and recovery.

**Tech Stack:** Python 3, FastAPI/SSE harness, SQLite migration v3, Redis, pytest.

---

## Problem and Decisions

- The current default-off unified flag leaves two persistence paths: the legacy Redis -> `ContextSnapshotService` -> turn rebuild ladder and the newer `ContextRepository` path.
- Confirmed decision: unified repository is the only runtime path after this change. `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED` defaults to `true` for compatibility with the approved live canary; the flag remains as a temporary compatibility read in tests only and is no longer a supported false production mode.
- `conversation_turns` and `context_state_json` are retained. This task does not delete or rewrite live data and does not remove schema columns.
- Legacy checkpoint references are handled by unified load: a recognized inflight ref rehydrates from Redis inflight; an old/non-unified ref falls back to committed projection/turns and emits a degraded rehydrate event instead of calling the deleted service.
- No new dependency or external repository is needed. The repository already contains the maintained replacement (`ContextRepository`) and its v3 migration/projection tests; external adoption research is therefore not applicable.

## Scope / Non-goals

In scope:

- Delete `app/services/context_snapshot_service.py` and all production imports.
- Remove the legacy snapshot-backed `ContextStateStore` integration from harness runtime wiring.
- Route stateful context build, runtime flush, completion commit, and resume through `ContextRepository`.
- Update flags, checkpoint comments/contracts, focused tests, e2e coverage, and handoff/progress documentation.

Out of scope:

- Deleting SQLite `context_state_json`, `context_state_version`, or `conversation_turns`.
- Changing public SSE event `type` semantics or authentication/authorization.
- Removing `AgentContextState`, projection reducers, or Redis inflight/committed caches.

## Files and Responsibilities

- Delete: `app/services/context_snapshot_service.py` — obsolete standalone snapshot owner.
- Modify: `app/services/context_repository.py` — remain sole projection/cache/atomic commit owner; expose any narrowly required compatibility read for old checkpoint diagnostics without importing the deleted module.
- Modify: `app/agent/context/unified.py` — make unified build/flush the canonical context API and remove legacy integration fallback.
- Modify: `app/agent/context/integration.py`, `app/agent/context/store.py`, `app/agent/harness/stream_inner.py`, `app/agent/harness/tools_runtime.py`, `app/agent/harness/close_path.py`, `app/agent/harness/loop.py` — remove snapshot-service wiring and route calls through unified APIs.
- Modify: `app/agent/harness/events_emit.py`, `app/services/harness_checkpoint.py` — remove direct snapshot rehydrate and describe unified refs/fallback.
- Modify: `app/config.py`, `.env.example` — make unified mode the default and document the retired legacy switch.
- Modify: `tests/test_context_unified_wiring.py`, `tests/test_context_repository.py`, `tests/test_context_integration.py`, `tests/test_context_store.py`, `tests/test_context_snapshot_service.py`, `tests/test_context_projection_migration.py`, `tests/test_m1_w4_latency_exit.py`, plus relevant harness/API regressions — replace service-specific assertions with repository behavior and assert no production import remains.
- Modify: `plan/2026-07-19-unified-context-repository-progress.md` and the current pilot handoff — record removal, default change, automated e2e, and live/degraded evidence.

## Verification and Exit Criteria

- `rg` finds no production import/reference to `app.services.context_snapshot_service`, `ContextSnapshotService`, or legacy snapshot callback construction.
- Focused context/repository/checkpoint tests pass.
- Harness soft-path regression passes with the default unified setting.
- Automated API two-turn e2e proves turn persistence, projection reload, and no legacy append after unified commit.
- Resume test proves an inflight checkpoint rehydrates through unified Redis and an old ref degrades to committed context without crashing.
- Readiness/live dry-run (or documented degraded result if Redis/LLM is unavailable) records status and durable SQLite evidence in the progress document.
- `python -m compileall app tests` and the project lint/type checks pass for touched Python files.

## Risks and Rollback

- Risk: old checkpoints may contain only legacy snapshot refs. Mitigation: treat them as committed-context fallback, preserve ref/version fields, and test degraded resume.
- Risk: a hidden caller imports the deleted module. Mitigation: repository-wide search, import smoke test, and focused regression suite before deletion is considered complete.
- Risk: unified schema is unavailable in a stale local DB. Mitigation: keep migration enforcement explicit; do not auto-migrate or mutate live data from application startup.
- Rollback: restore the deleted module and revert the default flag/documentation changes; SQLite data remains intact because this plan performs no destructive data migration.

## Tasks

### Task 1: Lock the default and add regression expectations

**Files:** `app/config.py`, `.env.example`, `tests/test_context_unified_wiring.py`, new/updated no-legacy-import test.

- [x] Set `harness_unified_context_repository_enabled` default to `True` and update environment documentation.
- [x] Add a static assertion that production Python files do not import the deleted snapshot service.
- [x] Run focused unified wiring tests before and after the old-path removal.

### Task 2: Remove snapshot-backed runtime wiring

**Files:** `app/agent/context/integration.py`, `app/agent/context/store.py`, `app/agent/context/unified.py`, `app/agent/harness/stream_inner.py`, `app/agent/harness/tools_runtime.py`, `app/agent/harness/close_path.py`, `app/agent/harness/loop.py`.

- [x] Keep unified build/flush as the production runtime path; legacy compatibility store construction now requires injected test callbacks.
- [x] Keep tool evidence and recent-turn pure state helpers that do not own persistence.
- [x] Verify runtime-stage inflight persistence and terminal atomic commit through focused wiring/e2e tests.

### Task 3: Delete the service and repair resume diagnostics

**Files:** delete `app/services/context_snapshot_service.py`; modify `app/agent/harness/events_emit.py`, `app/services/harness_checkpoint.py`, and any repository helper needed for committed projection reads.

- [x] Remove direct `ContextSnapshotService` imports and the post-miss DB snapshot pull.
- [x] Emit the existing degraded `context_rehydrate_failed` event for old refs while continuing with committed context.
- [x] Update checkpoint documentation/comments to refer to `ContextRepository`, not `ContextStateStore`.

### Task 4: Update focused tests and migration/audit fixtures

**Files:** all context snapshot/store/integration/projection tests listed above.

- [x] Replace service fixtures with callback-injected or direct-SQL legacy-data fixtures and repository load/commit assertions.
- [x] Delete service-only tests; preserve compact projection, migration audit, and rebuild coverage at repository boundaries.
- [x] Add two-turn API persistence/reload and old-ref degraded-resume coverage.

### Task 5: Validate and record evidence

**Files:** `plan/2026-08-01-remove-context-snapshot-service-progress.md`, `plan/2026-07-19-unified-context-repository-progress.md`, current pilot handoff, `AGENTS.md` index status.

- [x] Run focused tests, harness soft-path regression, compile/lint checks, and automated e2e.
- [x] Run live readiness plus the documented dry-run/degraded path when the server was unavailable.
- [x] Record commands, non-secret session identifiers, pass/fail output, and residual risks in the progress record.
