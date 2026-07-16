# M2 W6 Progress: Parallel Delegation · Aux Execution

Date: 2026-07-14

## Implemented

- New tool `delegate_parallel` (experts[] + subtasks[]) with `asyncio.gather`,
  per-expert timeout, de-dupe, and `HARNESS_PARALLEL_MAX_EXPERTS` truncation.
- Shared `run_one_delegate` / `run_parallel_delegates` helpers used by both
  serial `delegate_to_expert` and parallel fan-out.
- Router `aux_routes` auto probe via `ROUTER_AUX_EXECUTION_MODE=off|serial|parallel`
  (default `parallel`, max 2). Emits `stage=aux_probe` and folds evidence into
  parent messages + timeline (for early-close / evidence match).
- Parallel start/done timeline stages: `delegate_parallel_start` /
  `delegate_parallel_done`.
- Optional trace export (`HARNESS_TRACE_EXPORT_ENABLED`, default false).
- Evaluator: `parallel_event` summary flag + `require_parallel_event` gate.
- Eval cases expanded **20 → 23** (`P1-parallel-cross-domain`, `N2-no-write-action`,
  `K3-experience-recall`).
- Shared-kernel **design only**: `plan/2026-07-14-m2-shared-kernel-design.md`.

Rollback:

```text
HARNESS_PARALLEL_DELEGATION_ENABLED=false
HARNESS_PARALLEL_MAX_EXPERTS=1
ROUTER_AUX_EXECUTION_MODE=off
ROUTER_AUX_MAX_PROBES=0
HARNESS_TRACE_EXPORT_ENABLED=false
```

## Verification

```text
pytest M1/M2 + W6:
  tests/test_m1_close_the_loop.py
  tests/test_m1_w2_context_checkpoint.py
  tests/test_m1_w3_replan_latency.py
  tests/test_m1_w4_latency_exit.py
  tests/test_m2_latency_eval_hardening.py
  tests/test_m2_w6_parallel_delegation.py
  tests/test_harness_verifier.py
  tests/test_harness_checkpoint.py
  tests/test_context_integration.py

Result: 72 passed (W6 file 13 tests + prior M1/M2 subset)
```

Key W6 unit proofs:

- two 50ms experts complete under 90ms wall (true concurrency)
- parallel disabled rejects tool
- max experts truncation
- sibling failure isolation
- aux off / parallel / serial modes
- delegate round-cap still forwarded (W5 regression)
- trace export noop when disabled
- require_parallel_event scoring gate

## Live evidence

Not re-run in this coding session (no forced L1.5 Go attempt). Recommended next:

```bash
# health + MCP up
python scripts/evaluate_oncall_local.py --case P1-parallel-cross-domain --timeout-extra 90
python scripts/evaluate_oncall_local.py --suite minimal --timeout-extra 90
```

Keep **L1.5 Conditional Go** until a healthy full minimal suite is recorded.

## Assessment

- W6 code goals (B1 parallel tool, B3 aux execution, F3a 23 cases, G1a trace
  skeleton, shared-kernel design) are met at unit level.
- Wall-clock live P50 benefit still needs a healthy MCP + LLM environment.
- Shared kernel implementation deferred to W7 by design.
