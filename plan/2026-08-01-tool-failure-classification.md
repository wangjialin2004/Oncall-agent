# Tool Failure Classification And Suppression Plan

## Problem

OnCall tool failures currently arrive through several incompatible paths:

- a local tool can catch an exception and return ordinary text, which the
  executor records as a completed tool call;
- MCP `CallToolResult(isError=True)` is serialized as text but not recognized
  as a failed call;
- structured permanent-capability failures, such as the intentionally absent
  change source, consume the normal retry budget; and
- a known non-retryable failure can be offered again later in the same harness
  run, creating duplicate failed calls and wasting the investigation budget.

Runtime evidence in `logs/app_2026-07-29.log` showed both transient Prometheus
connection/502 failures and repeated failed calls. On 2026-08-01, the configured
`http://127.0.0.1:9090/api/v1/alerts` endpoint returned HTTP 200 with the pilot
alerts, so this work must preserve real upstream failures instead of replacing
them with synthetic local evidence.

## Decisions And Defaults

1. Preserve the existing public SSE contract: terminal outcomes remain
   `tool_event` with `status=completed|failed`. Internal `payload.retryable` is
   allowed for harness policy only; `PublicEventProjector` continues to omit it.
2. Normalize three failure signals in the shared `GuardedToolExecutor`:
   structured mapping/JSON failures, MCP `isError=True`, and explicit local
   tool failure payloads. A failure can include an optional boolean
   `retryable`; otherwise the executor conservatively infers permanent
   capability/authorization/configuration errors as non-retryable.
3. Keep bounded retries for transient failures under the existing
   `HARNESS_TOOL_MAX_RETRIES` and `HARNESS_TOOL_RETRY_BACKOFF_SECONDS` controls.
   An MCP execution error is terminal at the outer executor because the MCP
   client has already performed its own bounded retry loop.
4. Local tools that catch exceptions must return a safe structured failure
   (`success=false`, `status=error`, `error_code`, `retryable`) rather than raw
   exception prose. The knowledge tool must not treat a missing tenant request
   scope as evidence; an empty successful search remains distinct from failure.
5. Add `HARNESS_FAILED_TOOL_SUPPRESSION_ENABLED=true`. During one harness run,
   a tool with a terminal non-retryable failure is removed from subsequent model
   tool menus, while alternative tools and delegation remain available. Setting
   it to `false` restores the prior re-offer behavior.
6. No fallback from Prometheus to host-local metrics is introduced. That would
   change the evidence data-source strategy and could misrepresent the target
   service.

## Scope And Non-Goals

In scope: shared executor classification, safe local failure payloads,
non-retryable same-run suppression, focused tests, SSE/persistence contract
coverage, and live dry-run evidence.

Out of scope: automatic restart/rollback/scaling, a new Prometheus provider,
modifying MCP transport retries, changing public event type semantics, exposing
failure internals to the browser, or changing stateful-context persistence.

## Existing Capability And External Research

Repository search found the existing shared execution path in
`app/agent/agent_loop.py`, structured failure handling in
`_structured_failure_reason`, local tools in `app/tools/`, MCP wrappers in
`app/agent/mcp_client.py`, and public-event redaction in
`app/agent/public_events.py`. The implementation extends those paths instead
of adding a tool framework or dependency.

| Candidate / documentation | Checked | License / maintenance / security | Decision |
| --- | --- | --- | --- |
| Python asyncio timeout docs: https://docs.python.org/3/library/asyncio-task.html | 2026-08-01; project supports Python 3.11-3.13 | PSF License; official maintained docs; existing `asyncio.wait_for` timeout boundary remains valid | Retain current timeout ownership; do not add a retry library. |
| HTTPX exceptions docs: https://www.python-httpx.org/exceptions/ and https://github.com/encode/httpx | HTTPX 0.28.1 in `uv.lock`; GitHub checked 2026-08-01, active/non-archived | BSD-3-Clause; maintained upstream; existing dependency and security update path already lockfile-managed | Reuse current `httpx` behavior only; no upgrade or copied code. |
| MCP tools specification: https://modelcontextprotocol.io/specification/2025-06-18/server/tools and https://github.com/modelcontextprotocol/python-sdk | Specification and GitHub checked 2026-08-01; SDK is active/non-archived | MIT; official maintained SDK; project already locks `mcp` through FastMCP | Honor `CallToolResult.isError` as the documented tool-execution failure signal; no SDK change. |

## Affected Files

- Modify: `app/core/tool_calling.py`
- Modify: `app/agent/agent_loop.py`
- Modify: `app/agent/harness/policy.py`
- Modify: `app/tools/knowledge_tool.py`
- Modify: `app/tools/time_tool.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: focused tests under `tests/`
- Create: `plan/2026-08-01-tool-failure-classification-progress.md`
- Modify: `AGENTS.md`

## Flags, Safety, And Rollback

- `HARNESS_FAILED_TOOL_SUPPRESSION_ENABLED=true` is the new degradation switch.
  Set it to `false` to restore the previous behavior of advertising a
  non-retryable failed tool later in the same run.
- `HARNESS_TOOL_MAX_RETRIES` and `HARNESS_TOOL_RETRY_BACKOFF_SECONDS` continue
  to control only transient retry behavior; setting retries to `0` disables
  retry attempts.
- All tools remain read-only. Failed data-source calls produce an explicit
  evidence gap; the system must not claim that a fallback source queried the
  requested service.
- Rollback is scoped to the files above. No migration, state data, provider
  configuration, or external service needs rollback.

## Verification And Exit Criteria

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_agent_loop.py \
  tests/test_harness_service.py \
  tests/test_m2_latency_eval_hardening.py \
  tests/test_public_agent_events.py \
  tests/test_public_progress_e2e.py \
  -q --no-cov

PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m3_w9_residuals.py \
  -q --no-cov

ruff check app/core/tool_calling.py app/agent/agent_loop.py \
  app/agent/harness/policy.py app/tools/knowledge_tool.py app/tools/time_tool.py
```

Automated path exit criteria:

- a local caught exception, an MCP `isError`, and structured `success=false`
  all produce a failed tool result;
- a permanent source failure consumes one execution attempt and is unavailable
  to later model turns when suppression is enabled;
- a transient structured failure still recovers through the existing bounded
  retry path;
- public SSE/history retain `failed` while redacting raw error data; and
- a multi-step harness flow observes the failure, replans/continues safely,
  and does not turn it into successful evidence.

Live/runtime exit criteria:

- run the documented dry-run path against the live local Prometheus alerts
  endpoint and record its HTTP status and tool result;
- exercise the deterministic failed-tool test path without real credentials or
  a live LLM; and
- record all commands, request/session identifiers, and pass/fail evidence in
  the progress record. A healthy live LLM run is not required to verify this
  transport/error-classification change; any unavailable upstream result must
  be explicitly recorded rather than claimed as semantic success.

## Execution Checklist

- [x] Add focused failing tests for MCP errors, local structured failures,
  retryability, and same-run suppression.
- [x] Implement the shared executor failure normalization and internal event
  metadata without changing public SSE semantics.
- [x] Return safe structured error payloads from local tools that currently
  turn exceptions into plain successful text.
- [x] Filter terminal non-retryable tools for later harness turns behind the
  new environment switch.
- [x] Run focused, soft-path, API/persistence, lint, and live dry-run checks.
- [x] Write completion evidence in the progress record and update this index.

## Risks

- Incorrectly classifying a transient upstream response as permanent could hide
  a useful retry. Mitigation: only suppress explicit `retryable=false` and
  clearly permanent structured capability states; retain the existing transient
  retry default.
- A raw exception could leak into model-visible tool content. Mitigation: local
  error payloads use safe codes/messages, and public projection continues to
  remove raw tool results.
- Filtering every tool could prematurely close a run. Mitigation: delegation and
  context control-plane tools remain eligible, and the close path must emit an
  evidence gap instead of fabricated evidence.
