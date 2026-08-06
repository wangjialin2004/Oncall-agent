# Retire Legacy Context Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ContextRepository` and its rendered envelope the only runtime context path for `/api/assistant`, removing `ContextBuilder.abuild` and legacy terminal persistence fallback without deleting canonical historical data.

**Architecture:** A request will always load `ContextEnvelope` through `ContextRepository`, render it into `HarnessContext`, and atomically commit the immutable turn plus compact projection through `build_completion_committer`. `conversation_turns` remains canonical and rebuildable; the compact projection and Redis inflight state remain recovery accelerators. The retired false feature-flag branch, including legacy `ContextBuilder.abuild` and `conversation_service.append_turn` fallback, is removed rather than silently retained.

**Tech Stack:** Python 3, FastAPI/SSE, SQLite, Redis-compatible inflight cache, pytest/httpx.

---

## Problem and Decisions

- `harness_unified_context_repository_enabled` defaults to true, but `HarnessStreamInnerMixin` still selects `ContextBuilder.abuild` when false and `app/api/assistant.py` still owns a legacy `_persist_turn` fallback. That leaves an untested production-shaped dual path after the unified repository became the approved owner.
- The accepted default is the unified context path only. The environment variable is retired, not treated as a supported runtime rollback. Rollback is a source rollback that leaves SQLite data intact.
- No existing data is deleted or migrated by application code. `conversation_turns`, `conversations.context_state_json`, projection rebuild, and Redis inflight recovery stay supported.
- API-level E2E is the acceptance surface: it must exercise `POST /api/assistant` streaming, atomic persistence, a second same-session request, and an API/history read. Browser validation is not required for this backend-only path.
- No new dependency is introduced. The maintained in-repository replacement is `ContextRepository`; external dependency/adoption research is not applicable to removal of an internal compatibility branch.

## Scope and Non-goals

In scope:

- Remove the `ContextBuilder.abuild` / stateful legacy context branches from `app/agent/harness/stream_inner.py`.
- Remove the legacy `_persist_turn` / `conversation_service` fallback from `app/api/assistant.py`.
- Retire the compatibility feature flag and tests that force it false.
- Add or update API E2E proving unified context persistence and two-turn reload, plus regression guards that old entry points are no longer reachable.
- Record validation evidence and update the Current Plan Index.

Out of scope:

- Deleting or rewriting historical conversations/projections.
- Changing `agent_event`, `tool_event`, or `decision_event` SSE type semantics.
- Moving route/planner responsibilities out of `HarnessService`; that is a separate scheduler architecture change.
- Automatically repairing historical projections found by the existing read-only audit.

## Affected Files

- `app/agent/harness/stream_inner.py`: use the unified envelope unconditionally and remove legacy state/context assembly.
- `app/api/assistant.py`: always install the completion committer; remove legacy turn append helper/import.
- `app/config.py`: remove the retired unified-context compatibility setting.
- `app/agent/context/unified.py`, `app/services/context_repository.py`: remove false-path compatibility checks only where they make the old runtime reachable; keep unified persistence/recovery implementation.
- `tests/test_context_unified_wiring.py`: preserve/extend the API two-turn persistence/reload E2E and add static reachability checks.
- `tests/test_harness_stateful_context.py` and affected harness/API tests: migrate false-flag legacy fixtures to unified repository fixtures or test doubles.
- `plan/2026-08-02-retire-legacy-context-builder-progress.md`, `AGENTS.md`: verification record and index status.

## Flags

- Retire `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED`; it currently defaults to `true` and will no longer select a legacy runtime path.
- Preserve `HARNESS_CHECKPOINT_ENABLED` as the degradation switch for inflight recovery. With it disabled, completed-turn persistence continues; only mid-turn recovery is skipped.
- Preserve existing stateful context and context-tool flags; they alter rendered/view behavior, not the storage owner.

## Risks and Rollback

- **Risk:** tests or local tooling still set the retired flag. **Mitigation:** static repository search, focused test migration, and config construction test.
- **Risk:** a failed atomic commit prevents terminal persistence. **Mitigation:** preserve current commit failure signaling; do not fall through to a split legacy append that could duplicate or hide the failure.
- **Risk:** stale historical projections. **Mitigation:** retain canonical turns and repository rebuild-on-load behavior; do not mutate the audited database.
- **Rollback:** restore the removed code/flag from source control and redeploy. No data rollback is needed because this plan performs no destructive DB operation.

## Tasks

### Task 1: Define the removal contract with failing tests

**Files:** `tests/test_context_unified_wiring.py`, `tests/test_harness_stateful_context.py`

- [x] **Step 1: Add a static production-path guard.**

```python
def test_production_context_path_has_no_legacy_builder_or_flag_branch() -> None:
    source = Path("app/agent/harness/stream_inner.py").read_text(encoding="utf-8")
    assert ".abuild(" not in source
    assert "unified_context_repository_enabled" not in source
```

- [x] **Step 2: Extend the API two-turn E2E to use the real unified committer.**

```python
events = [event async for event in assistant(request, principal)]
assert _complete(events)["answer"] == "first answer"
events = [event async for event in assistant(follow_up, principal)]
assert "first answer" in captured_second_turn_context
assert len(repository.load_envelope("owner", "session").turn_window) == 2
```

- [x] **Step 3: Run the focused tests and confirm the new guard fails before implementation.**

Run: `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py tests/test_harness_stateful_context.py --no-cov`

Expected: FAIL because the legacy `abuild` branch and compatibility flag still exist.

### Task 2: Remove legacy context loading

**Files:** `app/agent/harness/stream_inner.py`, `app/agent/context/unified.py`, `app/services/context_repository.py`

- [x] **Step 1: Replace the three-way context branch with unconditional unified envelope load/render.**

```python
loaded = await prepare_unified_context(...)
stateful_ctx = StatefulContext(...)
context = HarnessContext(
    system_prompt=loaded.rendered.system_prompt,
    history_messages=list(loaded.rendered.history_messages),
)
await persist_stateful_context(stateful_ctx.state, store=self.context_store, persist_snapshot=False)
```

- [x] **Step 2: Delete only false-path compatibility guards that route to legacy persistence.**

```python
async def persist_runtime_state(...):
    # Persist inflight state when checkpoints are enabled; no legacy store fallback.
```

- [x] **Step 3: Run focused context/harness tests.**

Run: `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py tests/test_context_repository.py tests/test_harness_stateful_context.py --no-cov`

Expected: PASS.

### Task 3: Remove legacy terminal persistence and flag

**Files:** `app/api/assistant.py`, `app/config.py`, `app/agent/context/unified.py`, `app/services/context_repository.py`, affected tests

- [x] **Step 1: Make the API always pass `build_completion_committer`.**

```python
stream_kwargs["completion_committer"] = build_completion_committer(
    owner_key=owner_key,
    session_id=request.id,
    commit_id=str(context.trace_id or request.id),
    user_message=request.question,
    user_context=attachment_payload.persistent_context,
    attachment_refs=attachment_payload.attachment_refs,
    run_id=str(context.trace_id or request.id),
)
```

- [x] **Step 2: Delete `_persist_turn` and the false flag setting.** The API retains a read-only `conversation_service` lookup to resolve a historical attachment reference; it is not a persistence fallback.

```python
if event_type in {"complete", "error"}:
    break
```

- [x] **Step 3: Update test fixtures that force the retired flag false.**

```python
repository = ContextRepository(db_path=initialize_context_db(tmp_path / "context.db"), ...)
service = HarnessService(context_repository=repository, ...)
```

- [x] **Step 4: Run the removal-focused test suite.**

Run: `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py tests/test_context_repository.py tests/test_harness_stateful_context.py tests/test_harness_service.py tests/test_public_progress_e2e.py tests/test_tool_failure_contract.py --no-cov`

Expected: PASS.

### Task 4: API E2E and runtime verification

**Files:** `tests/test_context_unified_wiring.py`, `plan/2026-08-02-retire-legacy-context-builder-progress.md`

- [x] **Step 1: Run the automated API E2E with two turns and durable reload.**

Run: `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py::test_assistant_two_turn_unified_e2e_persists_compact_projection --no-cov`

Expected: PASS; first `POST /api/assistant` commits the turn/projection, the second same-session request receives rehydrated context, and the repository exposes two canonical turns.

- [x] **Step 2: Run the existing checkpoint degraded/recovery E2E.**

Run: `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py -k 'inflight or checkpoint or two_turn' --no-cov`

Expected: PASS; incomplete state uses unified inflight recovery and no old context builder.

- [x] **Step 3: Attempt the local API readiness check and run the documented automated dry-run API flow.** The local port was not listening; the automated handler/SSE path remains the recorded dry-run substitute.

Run: `curl -fsS http://127.0.0.1:9900/health/readiness`

Expected: HTTP 200 with a non-secret readiness payload. Record the request/session identifier and whether a healthy upstream or documented degraded response was observed.

- [x] **Step 4: Run quality gates and record results.**

Run: `PYTHONPATH=. .venv/bin/python -m compileall -q app tests`

Run: `.venv/bin/ruff check app/agent/context app/agent/harness/stream_inner.py app/api/assistant.py app/services/context_repository.py tests/test_context_unified_wiring.py tests/test_harness_stateful_context.py`

Expected: both commands exit 0.

## Exit Criteria

- No production request can reach `ContextBuilder.abuild` or `conversation_service.append_turn` as a context persistence fallback.
- Every `/api/assistant` completion uses the unified completion committer.
- The API automated E2E proves first-turn commit, second-turn reload, and durable canonical turns.
- A checkpoint/inflight API path proves unified recovery or documented degraded continuation.
- Focused tests, one harness soft-path regression, compile, Ruff, and readiness evidence are recorded in the progress file.
