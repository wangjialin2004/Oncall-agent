# Retire Legacy Context Builder Progress

## Status

Implemented; automated API/Harness E2E and focused quality gates passed.
Live FastAPI readiness could not run because no process was listening on port
9900; this is recorded as an environment limitation, not a healthy live run.

## Scope

- Retire only legacy context load and terminal persistence branches.
- Preserve canonical conversation turns, compact projections, unified recovery, and existing schema/data.

## Verification Evidence

- Red-to-green removal guard: the new `stream_inner.py` static guard initially
  failed on `context_builder.abuild(`, then passed after the unified-only load
  path replaced the three-way branch.
- `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py tests/test_context_repository.py tests/test_harness_stateful_context.py --no-cov`
  - `38 passed in 1.00s`.
- `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py::test_assistant_two_turn_unified_e2e_persists_compact_projection --no-cov`
  - `1 passed in 0.64s`; it exercises two `/api/assistant` handler SSE turns,
    canonical `conversation_turns`, compact projection, and same-session
    history reload.
- `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py -k 'inflight or checkpoint or two_turn' --no-cov`
  - `2 passed in 0.65s`; unified inflight/checkpoint recovery has no builder
    fallback.
- `PYTHONPATH=. .venv/bin/pytest -q -o addopts='' tests/test_context_unified_wiring.py tests/test_context_repository.py tests/test_harness_stateful_context.py tests/test_harness_service.py tests/test_public_progress_e2e.py tests/test_tool_failure_contract.py --no-cov`
  - `120 passed in 90.99s`; includes the Harness soft-path regression,
    public event contract, and tool failure contract.
- `PYTHONPATH=. .venv/bin/python -m compileall -q app tests`
  - passed.
- `.venv/bin/ruff check app/agent/context/unified.py app/agent/harness/stream_inner.py app/agent/harness/checkpoint_ops.py app/agent/harness/tools_runtime.py app/agent/harness/close_path.py app/agent/harness/events_emit.py app/api/assistant.py app/services/context_repository.py`
  - `All checks passed!`.
- `Get-NetTCPConnection -LocalPort 9900 -State Listen`
  - no listening process. A live readiness/authenticated request was therefore
    not run; no service was started or restarted for this backend-only change.

## Deviations

- The API now calls the same unified completion committer when an injected or
  nonstandard Harness emits a complete event without its commit marker. This
  preserves a single atomic repository commit and does not restore the removed
  `conversation_service.append_turn` fallback.
- A broad Ruff run over `app/agent/context/` also reports existing formatting
  and modernization findings in unrelated context modules and legacy-format
  test files. They were not autoformatted in this scoped removal change; every
  production file modified for this plan passes its targeted Ruff check.
