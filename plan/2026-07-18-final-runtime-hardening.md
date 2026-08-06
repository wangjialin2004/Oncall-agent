# Final runtime hardening implementation plan

## Problem

The completion review found four code-level gaps after the security/tenant handoff: `/metrics` is public, readiness has no Redis/MCP capability state, public health responses expose topology/error details, and the RAG migration planner cannot inspect a legacy collection that lacks scope fields.

## Decisions and defaults

- Metrics access defaults to `internal`; `bearer` is the explicit protected alternative. `public` is allowed only when `DEBUG=true` and an explicit degradation switch is enabled.
- Public health endpoints expose only low-sensitivity status and stable issue codes. Detailed dependency data remains behind an operator/internal diagnostic path.
- Redis is `ready`/`degraded` when enabled; it does not fail readiness when checkpoint/stateful paths are explicitly disabled. MCP is required only when `HARNESS_MCP_ENABLED=true`.
- RAG migration remains read-only by default. Legacy rows without trustworthy scope are reported, never guessed or copied. No live Milvus or SQLite mutation is performed in this change.

## Scope

- Add configuration and tests for metrics access policy.
- Add process-local dependency status for Redis and MCP and use it in readiness.
- Redact public health payloads while preserving endpoint/status compatibility.
- Extend RAG dry-run inspection to describe legacy schema gaps and produce a deterministic unresolved-row report without mutation.

## Non-goals

- No destructive Milvus drop, SQLite down migration, backup deletion, or production remediation executor.
- No SSE event shape changes.
- No change to tenant visibility policy or old `biz` collection deletion behavior.

## Affected files

- `app/config.py`, `.env.example`
- `app/core/metrics.py`, `app/api/health.py`, `app/services/redis_client.py`, `app/main.py`
- `scripts/migrate_rag_scope.py`
- focused tests under `tests/`
- this plan and progress evidence

## Verification commands

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_readiness_security.py tests/test_metrics_access.py \
  tests/test_rag_tenant_scope.py -q --no-cov
PYTHONPATH=. .venv/bin/python -m compileall -q app scripts
PYTHONPATH=. .venv/bin/ruff check --select E9,F63,F7,F82 app scripts tests
git diff --check
```

## Exit criteria

- Unauthenticated metrics access is rejected in non-debug defaults; protected scrape path is tested.
- Readiness reports Redis/MCP state according to enabled capabilities and never exposes raw exception text.
- Public health payload contains no internal URL, collection name, or provider exception string.
- Legacy RAG dry-run reports missing scope fields/unresolved rows and remains `mutated=false`.
- Focused tests and static checks pass.

## Risks and rollback

- Tightening metrics/health defaults may require a deployment secret or internal-network configuration; set the documented degradation switch only in isolated local debug.
- If readiness integration causes false negatives, disable the new capability checks via their env switches while preserving metrics/health redaction.
- Revert only the code/config files from this plan; do not reset unrelated user work or touch live data.

## Progress and verification (2026-07-18)

- Added fail-closed metrics policy (`internal` default, protected `bearer`, debug-only explicit `public`) and mounted ASGI protection.
- Added Redis lifecycle health snapshots, capability-aware readiness for Redis/MCP, and MCP prewarm gating.
- Public health payload now contains stable status/issues only; detailed health is disabled by default and admin-protected when enabled.
- Legacy RAG dry-run now reports missing scope fields and unresolved rows, including JSON metadata parsing, without mutation.
- Focused hardening tests: **19 passed**; security/tenant/checkpoint/migration set: **69 passed**; CI smoke extension: **88 passed**.
- compileall, affected-file Ruff, and `git diff --check` passed.
- Before the explicit approval, live Milvus `apply` and real SQLite `up` remained intentionally unexecuted.

### Approved live-operation deviation

The user explicitly approved live migration after the initial implementation. The
Milvus endpoint was reachable and an empty legacy `biz` collection was safely
used to create scoped `biz_v2`; the real long-term-memory SQLite backup was
refreshed and `up` completed transactionally. No non-empty legacy RAG rows were
guessed or copied, and no collection was dropped.
