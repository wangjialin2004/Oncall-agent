# Tool Failure Classification And Suppression Progress

## Status

Implemented and verified on 2026-08-01. The shared executor now distinguishes
MCP execution errors, structured provider failures, and local exceptions;
terminal failures are omitted from later Harness tool menus behind an explicit
rollback flag. Public SSE/history payloads keep the existing `failed` status and
do not expose the internal retryability marker.

## Delivered Behavior

- `GuardedToolExecutor` recognizes `CallToolResult(isError=True)` and returns a
  failed result without applying a second outer retry loop.
- Structured `success=false` / error statuses honor explicit `retryable` and
  conservatively infer permanent capability, configuration, authorization, and
  invalid-input failures.
- Transient structured failures retain bounded retry/backoff behavior. Timeout
  failures keep the existing single-call timeout boundary and are marked
  retryable for a later model turn.
- Knowledge retrieval and time lookup return safe structured errors. Missing
  request scope is terminal and never becomes evidence; raw exception details
  are not returned in tool content.
- `HARNESS_FAILED_TOOL_SUPPRESSION_ENABLED=true` removes a terminal
  non-retryable failed investigation tool from subsequent model menus while
  retaining delegation and context-control tools. `false` restores re-offering.

## Verification Evidence

| Check | Command / Fixture | Result |
| --- | --- | --- |
| Focused contract + shared-loop tests | `PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_tool_failure_contract.py tests/test_agent_loop.py -q --no-cov` | 13 passed |
| Harness soft-path and public-contract regression | `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false HARNESS_LLM_PLANNING_ENABLED=false HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=false PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_m2_latency_eval_hardening.py tests/test_m1_w3_replan_latency.py tests/test_m3_w9_residuals.py tests/test_harness_observability.py tests/test_harness_service.py tests/test_public_agent_events.py tests/test_public_progress_e2e.py -q --no-cov` | 106 passed in 32.39s |
| Final combined regression | same controlled-environment command with `tests/test_tool_failure_contract.py tests/test_agent_loop.py` included | 119 passed in 34.85s |
| Public SSE/history redaction and multi-step Harness flow | `tests/test_tool_failure_contract.py` | Failed status is retained internally/history; `retryable` and raw private detail are absent from public output; terminal tool is absent from the next model menu |
| Lint | `.venv/bin/ruff check app/core/tool_calling.py app/agent/agent_loop.py app/agent/harness/policy.py app/tools/knowledge_tool.py app/tools/time_tool.py tests/test_tool_failure_contract.py` | All checks passed |
| Live Prometheus dry-run | `curl --connect-timeout 2 --max-time 10 http://127.0.0.1:9090/api/v1/alerts`; executor call id `live-prometheus-alerts-20260801` | HTTP 200, Prometheus `status=success`, 3 alerts (`firing=3`), executor `success=true` |
| Deterministic failed-tool dry-run | executor call id `deterministic-invalid-timezone-20260801`, `get_current_time(timezone='Mars/Olympus')` | `success=false`, `error_code=invalid_timezone`, `retryable=false`; no raw exception detail in result |

The regression command explicitly disabled local unified-context, LLM-planning,
and anti-pattern canaries so their unrelated `.env` state could not change the
legacy Harness fixture. No live LLM credentials were used or required for this
transport/error-classification change.

## Material Deviations

None. The Prometheus data source remains the configured target endpoint; no
host-local or synthetic fallback was added.

## Rollback

Set `HARNESS_FAILED_TOOL_SUPPRESSION_ENABLED=false` to restore the prior
same-run tool-menu behavior. Revert the scoped executor, policy, local-tool,
configuration, test, and documentation changes to remove the classification
behavior; no database migration or external-provider rollback is required.
