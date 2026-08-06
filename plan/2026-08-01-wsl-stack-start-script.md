# WSL Full Stack Startup Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide one WSL-native command that starts the local dependency stack, FastAPI/MCP services, and the Vite frontend, then reports only when the reachable application surfaces are healthy.

**Architecture:** Keep `Makefile` as the owner of Docker, Redis, MCP, and FastAPI lifecycle. Add a small Bash wrapper at `scripts/start_wsl.sh` for the missing orchestration boundary: load the WSL NVM toolchain, delegate backend startup to `make up` and `make start`, launch Vite in a detached session, and verify HTTP readiness. The script never restarts or kills a healthy process; it detects an existing frontend HTTP endpoint and leaves it in place.

**Tech Stack:** Bash, GNU make, Docker Compose, curl, util-linux `setsid`, NVM Node/npm, Vite, FastAPI.

---

## Problem and Decisions

- The documented WSL flow requires three separate commands and an error-prone background `nohup` invocation for Vite. PowerShell-to-WSL quoting can also terminate a frontend child when the caller exits.
- `scripts/start_wsl.sh` is the single entrypoint. It resolves the repository root from its own location, so it can be invoked from any WSL working directory.
- Default behavior starts the complete local stack. `WSL_START_SKIP_FRONTEND=true` is the documented degradation switch for backend-only troubleshooting; the default is `false`.
- `WSL_START_ALLOW_DEGRADED=true` is a separate explicit switch for dependency troubleshooting; by default, a non-200 FastAPI readiness response is a startup failure.
- The script uses existing targets rather than duplicating service ownership: `make up` for Milvus and `make start` for Redis, MCP, and FastAPI.
- Vite is launched with `setsid -f` from `frontend/`, logging to the existing ignored `frontend-vite.log`. A successful `http://127.0.0.1:5173/` means the script must not create a duplicate Vite process.
- No external dependency, copied implementation, or new network service is introduced. Repository capability search found the existing Make targets and README WSL section. The installed util-linux `setsid` manual (`2.41.3`, checked 2026-08-01) confirms that `-f` creates a new session; this is compatible with WSL terminal exit behavior. The attempted online documentation lookup was unavailable, so no external package is adopted.

## Scope and Non-Goals

**In scope:** WSL prerequisite checks, NVM loading, idempotent full-stack startup, HTTP readiness checks, a frontend-only degradation switch, documentation, and a live repeat-run verification.

**Out of scope:** changing Docker Compose, adding automatic dependency installation, restarting or killing an occupied port, modifying API/SSE contracts, adding production deployment behavior, or changing stop semantics.

## Affected Files

- Create: `scripts/start_wsl.sh` - WSL-native full-stack launcher.
- Modify: `README.md` - make the launcher the preferred WSL entrypoint and retain the manual equivalent as fallback.
- Create: `plan/2026-08-01-wsl-stack-start-script-progress.md` - execution and live verification evidence.
- Modify: `AGENTS.md` - current plan index status.

## Risks and Rollback

- A healthy unrelated listener on port 5173 could be mistaken for Vite. The script first requires an HTTP 200 from the expected root; a listener that does not respond correctly is reported as a conflict and is never terminated.
- NVM may not be loaded in a non-interactive WSL shell. The script sources `$NVM_DIR/nvm.sh` only when `node` or `npm` is unavailable, then fails with a precise prerequisite message.
- A dependency may be still loading after `make up`/`make start`. Bounded HTTP polling checks Milvus `/healthz`, FastAPI `/health/readiness`, and Vite `/` before success.
- Rollback is deleting `scripts/start_wsl.sh` and restoring the README's previous manual commands; no database schema, container data, or API behavior changes.

## Verification and Exit Criteria

- `bash -n scripts/start_wsl.sh` succeeds.
- `WSL_START_SKIP_FRONTEND=true bash scripts/start_wsl.sh` exits zero with FastAPI readiness at `200`.
- Two normal invocations exit zero without starting a duplicate Vite listener.
- `curl --noproxy '*' -fsS http://127.0.0.1:9900/health/readiness` and `curl --noproxy '*' -fsS http://127.0.0.1:5173/` both return success.
- The Vite proxy reaches FastAPI: `/api/auth/me` returns the expected unauthenticated `401`, not a proxy failure.
- A protected two-turn assistant request persists and reloads its conversation under one generated test session; remove that exact test session after verification.

### Task 1: Implement the WSL launcher

**Files:**
- Create: `scripts/start_wsl.sh`

- [x] **Step 1: Define the executable contract**

The script must use `#!/usr/bin/env bash` and `set -Eeuo pipefail`, derive `REPO_ROOT` from `BASH_SOURCE[0]`, and define these constants:

```bash
API_READINESS_URL="http://127.0.0.1:9900/health/readiness"
MILVUS_HEALTH_URL="http://127.0.0.1:9091/healthz"
FRONTEND_URL="http://127.0.0.1:5173/"
WSL_START_SKIP_FRONTEND="${WSL_START_SKIP_FRONTEND:-false}"
WSL_START_ALLOW_DEGRADED="${WSL_START_ALLOW_DEGRADED:-false}"
```

- [x] **Step 2: Add prerequisite and polling helpers**

Implement `require_command`, `load_node`, `wait_for_http`, and `port_is_listening`. `load_node` must source `${NVM_DIR:-$HOME/.nvm}/nvm.sh` only when needed, with nounset temporarily disabled. Every localhost curl must include `--noproxy '*'`.

- [x] **Step 3: Delegate backend startup and validate readiness**

Run the existing commands in this exact order:

```bash
make up
wait_for_http "$MILVUS_HEALTH_URL" 30
make start
wait_for_http "$API_READINESS_URL" 45
```

On timeout, print the relevant endpoint and log path, then exit nonzero without killing any process.

- [x] **Step 4: Add idempotent Vite startup**

When `WSL_START_SKIP_FRONTEND=false`, first accept an existing successful frontend root response. If port `5173` is listening but that response fails, exit with a port-conflict diagnostic. Otherwise launch:

```bash
setsid -f npm --prefix "$FRONTEND_DIR" run dev -- --host 0.0.0.0 --port 5173 \
  >>"$FRONTEND_LOG" 2>&1 < /dev/null
```

Wait up to 30 seconds for `FRONTEND_URL`; on failure, show the last 80 lines of `frontend-vite.log` and exit nonzero. For `WSL_START_SKIP_FRONTEND=true`, emit a backend-only message and skip this step.

- [x] **Step 5: Verify shell syntax**

Run:

```bash
bash -n scripts/start_wsl.sh
```

Expected: exit code `0`.

### Task 2: Document the entrypoint

**Files:**
- Modify: `README.md:202-258`

- [x] **Step 1: Replace the preferred WSL startup block**

Document this invocation after the first-run environment setup:

```bash
bash scripts/start_wsl.sh
```

State that it starts the dependency stack, FastAPI/MCP, and detached Vite from the WSL toolchain, and reports `5173`, `9900`, and `/docs` URLs only after HTTP checks pass.

- [x] **Step 2: Document bounded troubleshooting behavior**

Add the backend-only command:

```bash
WSL_START_SKIP_FRONTEND=true bash scripts/start_wsl.sh
```

Keep the existing `make up`, `make start`, and manual Vite command as an explicitly labeled fallback. State that the launcher never kills an existing port owner.

### Task 3: Run live end-to-end verification and record evidence

**Files:**
- Create: `plan/2026-08-01-wsl-stack-start-script-progress.md`
- Modify: `AGENTS.md`

- [x] **Step 1: Exercise the backend-only branch**

Run:

```bash
WSL_START_SKIP_FRONTEND=true bash scripts/start_wsl.sh
curl --noproxy '*' -fsS http://127.0.0.1:9900/health/readiness
```

Expected: both commands exit `0` and readiness reports `status=ready`.

- [x] **Step 2: Exercise normal and idempotent startup**

Run the script twice and then verify:

```bash
bash scripts/start_wsl.sh
bash scripts/start_wsl.sh
curl --noproxy '*' -fsS http://127.0.0.1:5173/
curl --noproxy '*' -i -sS http://127.0.0.1:5173/api/auth/me
```

Expected: one frontend listener, root `200`, and proxied `401 token_missing` for the unauthenticated endpoint.

- [x] **Step 3: Verify authenticated two-turn persistence and reload**

Generate a local signed test token without printing it, send two `/api/assistant` requests with one fresh `wsl-e2e-*` session id, then call `/api/conversations/{session_id}`. Assert that both turns are returned under that same owner/session. Delete only that generated session after the assertion succeeds.

- [x] **Step 4: Record outcome and update index**

Write exact commands, non-secret session id, readiness results, proxy response, persistence/reload evidence, and any degraded external dependency result to the progress file. Mark this plan implemented in `AGENTS.md` only when all applicable checks pass.
