# WP-7 deployment and CI contracts

> Status: phase 1 implementation complete; CI execution pending remote workflow

## Problem

Operational commands and container/CI definitions have drifted from the real
authenticated API and runtime. Upload targets use a removed endpoint,
`check-all` modifies the worktree, the Dockerfile masks dependency failures,
and the backend container has no persistent application data or healthcheck.

## Decisions and defaults

- Upload commands target `/api/files` and require `AUTH_TOKEN`; missing auth
  fails before network access.
- `check-all` is read-only. Formatting remains an explicit command.
- Docker dependency installation must fail closed. Static serving stays off
  unless a build explicitly provides assets.
- Compose persists SQLite/file data and uses readiness for backend health.
- CI remains service-free and does not download BGE or call live providers.

## Scope

- Makefile upload/check contracts.
- Backend Dockerfile and pilot compose persistence/health contracts.
- Python 3.12/3.13 CI matrix with security/tenant/migration tests and frontend
  test/build jobs.

## Non-goals

- No deployment, image push, live workflow execution, or secret creation.
- No production Milvus/MCP stack changes.

## Verification

```bash
make -n upload
make -n check-all
docker compose -f deploy/compose/docker-compose.pilot.yml config
git diff --check
```

Exit criteria: commands reference current APIs, unauthenticated uploads fail
fast, checks do not mutate files, container config preserves application data,
and CI covers Python 3.12/3.13 plus frontend/security boundaries.

## Progress and verification (2026-07-18)

- `make -n upload AUTH_TOKEN=test-token` references `/api/files`; `make upload`
  without a token exits before curl.
- `make -n check-all` shows `format-check` and no formatting write command.
- `docker compose -f deploy/compose/docker-compose.pilot.yml config` and
  `--profile full config` pass.
- `docker compose ... --profile full build backend` succeeded with the strict
  dependency install and image healthcheck.
- CI changes are declarative and require the hosted workflow to run; no remote
  CI execution was triggered from this workspace.

## Risks and rollback

Compose paths and health behavior may differ across Docker Desktop/Linux;
validate `docker compose config` before rollout. Revert these declarative files
to the prior pilot draft if deployment validation fails; no runtime state is
modified by this implementation.
