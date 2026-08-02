# 可观测执行详情进度

**Status:** implemented; automated API/history e2e, frontend tests/build, and live SSE passed; browser automation unavailable in this environment

## Evidence

- 2026-08-01: repository review found the existing public projection strips `todos`, `required_evidence`, `gaps`, and tool `arguments/result`; current granular activity model only receives that stripped contract.
- 2026-08-01: external React documentation and assistant-ui GitHub maintenance/license checks recorded in the implementation plan; no dependency adoption.
- 2026-08-01: added `HARNESS_PUBLIC_PROGRESS_DETAILS_ENABLED=true` (default). With the switch disabled, the public projection returns to the previous minimal event payload.
- 2026-08-01: public timeline events now include bounded plan/replan steps, required evidence, verifier counts/confidence/gaps, and safe tool-result summaries. Tool results expose only allowlisted scalar fields, metric samples, bounded series summaries, and resource/statistics fields; raw arguments, raw result objects, PromQL, internal IDs, prompts, and context remain excluded.
- 2026-08-01: the inline chat activity feed now expands plan, tool, and verification nodes by default when they carry observable details. Live and restored history use the same model.

## Deviations

- A live CPU check revealed that the existing phone-number redactor interpreted an 11-digit byte count as a phone number. Typed numeric values from explicitly allowlisted metric/resource fields now preserve their value; untrusted strings continue to use token, email, phone, and URL-credential redaction.
- Browser-level automation could not run because this environment has no Playwright/Puppeteer package or Chromium executable. Testing Library UI coverage, Vite production build, and authenticated live API/SSE verification were run instead.

## Verification

Backend focused contract and automated API-to-persistence/history e2e:

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_public_agent_events.py tests/test_public_progress_e2e.py tests/test_tool_failure_contract.py -q --no-cov
# 22 passed in 0.82s

.venv/bin/ruff check app/agent/public_events.py tests/test_public_agent_events.py
# All checks passed
```

Frontend focused and full regression:

```bash
cd frontend
npm test -- --run src/components/chat/__tests__/inlineActivityModel.test.ts src/components/chat/__tests__/AgentActivityFeed.test.tsx src/components/__tests__/agentStream.test.ts
# 3 files, 34 tests passed

npm test -- --run
# 16 files, 116 tests passed

npm run build
# TypeScript and Vite production build passed
```

Live/runtime e2e (2026-08-01): an isolated authenticated FastAPI instance on `127.0.0.1:9901` received session `observable-live-20260801` and emitted 36 SSE frames. The stream and restored conversation history both contained plan/replan `todos`, `required_evidence`, tool result summaries, and verifier `gaps`, while internal call IDs, raw arguments, and private test values were absent. Session `observable-live-20260801-final` emitted 20 frames and confirmed nested `get_local_resource_usage` fields such as CPU usage/core count and memory metrics reach the public tool event. After the typed-scalar fix, session `observable-live-20260801-metric-values` asserted live plan and evidence-gap fields plus a visible CPU value and a visible `memory.total_bytes=16353755136` value, rather than a false phone redaction. Each isolated server was stopped and its temporary SQLite/SSE directory removed after verification.

## Exit Criteria

- [x] Live and history public events share the safe plan/result/evidence-detail contract.
- [x] The default UI renders plan steps, tool result summaries, and missing evidence in expandable activity nodes.
- [x] Raw IDs, arguments, prompt/context fields, and full raw results are excluded by tests and live inspection.
- [x] The details flag restores the minimal contract when disabled.
- [x] Focused backend/frontend tests, full frontend regression, lint, production build, and authenticated live API/SSE path passed.
