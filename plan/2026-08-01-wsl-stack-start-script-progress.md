# WSL Full Stack Startup Script Progress

## Status

Implemented and live-verified on 2026-08-01.

## Initial Context

- The existing `make up` and `make start` targets own dependency, Redis, MCP, and FastAPI lifecycle.
- Vite is intentionally separate from `make start`; the launcher will own only this missing orchestration boundary.
- `make up` reported the pre-existing `milvus-attu` host-port `8000` conflict, but `milvus-standalone`, etcd, and MinIO were healthy and Milvus `/healthz` returned `200`. Attu is ancillary to the API path and was not restarted or removed.

## Verification Evidence

- `bash -n scripts/start_wsl.sh` passed.
- `shellcheck` is not installed in this Ubuntu distribution; Bash parsing plus live startup checks are the available local script validation.
- `scripts/start_wsl.sh --help` passed and documented both environment switches.
- `WSL_START_SKIP_FRONTEND=true scripts/start_wsl.sh` exited `0`; FastAPI readiness returned `{"status":"ready","issues":[]}` and the script correctly reported the app as skipped.
- A normal script invocation reused the healthy API and frontend. After verifying the exact manually-started Vite PID (`/home/wangjialin/projects/super_biz_agent_py/frontend/node_modules/vite/bin/vite.js`), that PID was stopped, then `scripts/start_wsl.sh` launched Vite from `frontend/`; the frontend remained reachable after the launcher exited.
- A second normal invocation exited `0`, reported `Frontend already serving`, and `pgrep -fc '/home/wangjialin/projects/super_biz_agent_py/frontend/node_modules/.bin/vite'` remained `1`.
- `curl --noproxy '*' -fsS http://127.0.0.1:9900/health/readiness` returned HTTP `200` with `status=ready` and no issues.
- `curl --noproxy '*' -i -sS http://127.0.0.1:5173/api/auth/me` returned the expected proxy response `401 token_missing`, proving Vite -> FastAPI routing.
- `make status-mcp` reported CLS `8003` and Monitor `8004` running and reachable. `docker compose -f vector-database.yml ps` reported the Milvus services healthy.
- Automated path E2E session `wsl-e2e-start-5307e58b555c` used a runtime-signed local token and two streamed `/api/assistant` turns. Both returned `200` and a `complete` event; `/api/conversations/{session_id}` returned `history_status=200`, `turn_count=2`, and `turn_indexes=[0,1]`. The exact temporary session was then deleted with `deleted=true`.
- PowerShell `Invoke-WebRequest` could not consume the SSE stream (client-side `NullReferenceException` after the server accepted a `200`); it was not counted as a pass. The successful retry used the WSL virtualenv's standard Python HTTP client, which read both streams to completion without exposing tokens or answer text.
