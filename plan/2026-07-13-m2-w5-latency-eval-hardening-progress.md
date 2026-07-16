# M2 W5 Progress: Latency and Evaluation Hardening

Date: 2026-07-14

## Implemented

- Evaluator reads verification status from `agent_event.status`, rejects known
  Harness fallback answers, enforces required trigger events, and now preserves
  `primary_route` separately from a fallback `final_route`.
- Structured runtime-tool error payloads now become failed tool results, which
  makes rule-based replan observe real provider failures.
- RE1 requires real re-evidence. RE2 is a controlled Prometheus failure case
  requiring real replan and an explicit data-gap answer.
- Latency controls: diagnosis step cap of 3; delegated experts use one tool
  round and return the first tool batch to the parent Harness for synthesis.
  Successful investigation evidence also closes the parent planning loop early.
- A concrete-target plus incident-signal semantic knowledge result can be
  promoted to diagnosis by `ROUTER_CONCRETE_INCIDENT_OVERRIDE_ENABLED=true`.

Rollback controls:

```text
HARNESS_ROUTE_TIMEOUT_PROFILE=false
HARNESS_DIAGNOSIS_MAX_STEPS=5
HARNESS_INVESTIGATION_EVIDENCE_EARLY_CLOSE=false
HARNESS_DELEGATE_MAX_TOOL_ROUNDS=2  # or 3 for historical budget
HARNESS_DELEGATE_EVIDENCE_ONLY=false
ROUTER_CONCRETE_INCIDENT_OVERRIDE_ENABLED=false
```

## Verification

```text
pytest M1/M2 regression subset: 69 passed
  tests/test_m1_close_the_loop.py
  tests/test_m1_w2_context_checkpoint.py
  tests/test_m1_w3_replan_latency.py
  tests/test_m1_w4_latency_exit.py
  tests/test_m2_latency_eval_hardening.py
  tests/test_harness_verifier.py
  tests/test_harness_checkpoint.py
  tests/test_context_integration.py
  tests/test_harness_observability.py
  tests/test_agent_loop.py

ruff E9/F821/F822/F823: passed on changed Python files
py_compile: passed on changed Python files
```

The broader command including `tests/test_harness_service.py` exceeded the
120-second runner limit before producing a result. Its router-focused subset
passed together with the M2 tests.

## Live Evidence

| Run | Result |
|---|---|
| `oncall_selected_20260714_153049.json` | S1 passed, 117.42s, re-evidence=1, replan=1. Earlier same-case timeout was 210.08s. |
| `oncall_selected_20260714_153302.json` | RE1 passed, 48.09s, re-evidence=1, replan=0. Replan is intentionally not required for a successful knowledge-evidence path. |
| `oncall_selected_20260714_153952.json` | Controlled RE2 passed, 171.46s, re-evidence=1, replan=1. Prometheus was temporarily pointed at `127.0.0.1:1` and delegation disabled; normal `.env` values were restored immediately afterward. |
| `oncall_minimal_20260714_154334.json` | 6/10, P50 109.69s, P95 186.29s, complete rate 1.0, re-evidence rate 0.6, replan rate 0.4. Baseline was 9/10, P50 110.42s, P95 210.07s. |

The fresh minimal run was a valid strict-scoring observation, not an exit pass:
S4 was rejected as `harness_degraded_fallback`, N6 hit `client_timeout`, and
RE1 did not consistently emit re-evidence. During that run both local MCP
servers were unavailable (HTTP 502), so it is not comparable as a functional
acceptance result. They were subsequently restarted and verified with the
native client (`cls=8` tools, `monitor=10` tools).

After MCP recovery, S4 completed successfully but still took 206.14s. Logs
show the remaining tail is dominated by upstream LLM slow/empty responses and
the outer 180-second Harness timeout, not MCP tool duration. The backend,
Milvus, Prometheus, and MCP services are currently restored; `/health` returns
200.

## Current Assessment

- P50/P95 improved in the fresh 10-case sample, especially P95
  (210.07s -> 186.29s), without accepting fallback answers.
- Real re-evidence and replan are both proven on live requests.
- M2 W5 is not an L1.5 exit upgrade: rerun minimal only after the LLM provider
  is stable, with MCP services healthy for the entire suite. Investigate
  provider latency/empty-response failures and configure a supported lighter
  `LLM_PLANNER_MODEL` before attempting another tail-latency gate.
