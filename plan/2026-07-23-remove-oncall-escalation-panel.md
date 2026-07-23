# Remove OnCall Escalation Panel Implementation Plan

> **For agentic workers:** Execute this plan inline, task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the configurable OnCall escalation footer and its frontend panel completely, without changing the remaining read-only suggested-action workflow.

**Architecture:** The feature currently starts in `Settings.oncall_escalation_contacts`, is assembled by `HarnessClosePathMixin._build_escalation_block`, then travels in both completion payload variants as `escalation`. The frontend parses that optional property, stores it on `AgentRun`, and renders `EscalationCard`. Remove the entire contract rather than emit an always-empty compatibility field because the user requested the function and UI content be removed.

**Tech Stack:** Python 3.11, FastAPI/SSE harness, React 18, TypeScript, Vitest, pytest.

---

## Problem and decisions

- Problem: completed answers append an OnCall contact footer, including a visible message when contacts are not configured; the process panel also shows an escalation card.
- Decision: remove the feature end-to-end, including the setting and SSE field. Existing clients that still send or receive an `escalation` property remain harmless because unknown JSON properties are ignored by the server; the current frontend will no longer read it.
- Default and degradation switch: none. This removes an optional, read-only display-only capability and does not add a replacement behavior or a new runtime flag.
- Non-goals: do not alter suggested actions, HITL audit confirmation, operational escalation procedures outside this application, or historical plans/handoffs that accurately record the prior feature.
- New dependency/research: none. This is capability removal, so there is no external implementation, license, security, or release adoption decision.

## Affected files

- `app/config.py`: remove the unused `oncall_escalation_contacts` setting.
- `.env.example`: remove the documented environment variable.
- `app/agent/harness/close_path.py`: remove footer construction and both completion-payload fields.
- `tests/test_m3_w9_hitl_metrics.py`, `tests/test_harness_service.py`: remove escalation-only tests and test monkeypatching.
- `frontend/src/types/events.ts`, `frontend/src/api/agentStream.ts`, `frontend/src/App.tsx`: remove the client-side event contract and run state.
- `frontend/src/components/AgentProcessPanel.tsx`, `frontend/src/styles.css`: remove the process-panel card and exclusive style.
- `frontend/src/components/__tests__/AgentProcessPanel.test.tsx`, `frontend/src/components/__tests__/agentStream.test.ts`, `frontend/src/components/__tests__/App.test.tsx`: remove event fixtures and expectations dedicated to escalation.

## Task 1: Remove the backend contract

- [x] Delete `oncall_escalation_contacts` from `Settings` and its `.env.example` entry.
- [x] Delete `_build_escalation_block`, the soft-close `complete["escalation"]` field, the final-answer footer block, and both normal-completion `escalation` payload fields.
- [x] Remove only escalation-specific backend tests and the harness test monkeypatch which existed solely to suppress the footer.
- [x] Run: `pytest tests/test_m3_w9_hitl_metrics.py tests/test_harness_service.py -q`
- [x] Expected: all selected tests pass and `rg -n 'oncall_escalation_contacts|_build_escalation_block|"escalation"' app tests` finds no feature references.

## Task 2: Remove the frontend contract and display

- [x] Delete `EscalationInfo`, both optional escalation properties, the complete-event parser, App state propagation, `EscalationCard`, its invocation, and its exclusive CSS rule.
- [x] Remove escalation-only fixtures/assertions from frontend tests while retaining their suggested-actions assertions.
- [x] Run: `npm test -- --runInBand` is not supported by Vitest; instead run `npm test -- AgentProcessPanel agentStream App` and `npm run build` from `frontend/`.
- [x] Expected: selected Vitest suites and TypeScript/Vite build pass; `rg -n -i 'ONCALL_ESCALATION_CONTACTS|未配置值班联系人|EscalationCard|escalation-card' app frontend tests .env.example` finds no runtime/UI references.

## Exit criteria, risks, and rollback

- Exit criteria: completed answers no longer append an escalation footer; completion SSE payloads do not include `escalation`; the process panel has no escalation card; focused backend tests, focused frontend tests, and production frontend build pass.
- Risk: external clients may rely on the optional field. This is intentional removal requested by the user; other SSE event types and suggested-actions semantics stay unchanged.
- Rollback: restore this single plan's deleted feature blocks from version control, then restore the environment variable only if the prior UI and SSE contract are also restored.

## Verification evidence

- 2026-07-23: `PYTHONPATH=. .venv/bin/pytest tests/test_m3_w9_hitl_metrics.py tests/test_harness_service.py -q` passed: **71 passed**. The run emitted pre-existing `ResourceWarning` messages for unclosed SQLite connections in harness tests.
- 2026-07-23: `.venv/bin/ruff check app/config.py app/agent/harness/close_path.py tests/test_m3_w9_hitl_metrics.py` passed.
- 2026-07-23: `rg -n --glob '!node_modules' 'ONCALL_ESCALATION_CONTACTS|oncall_escalation_contacts|_build_escalation_block|EscalationCard|EscalationInfo|escalation-card|escalation:' app frontend/src tests .env.example` returned no matches.
- 2026-07-23: `node frontend/node_modules/typescript/bin/tsc -b frontend/tsconfig.json` passed when invoked through the Windows Node runtime against the WSL workspace.
- 2026-07-23: after explicitly loading WSL nvm (`~/.nvm/nvm.sh`), `npm test -- AgentProcessPanel agentStream App` passed: **3 files, 36 tests**.
- 2026-07-23: with the same WSL Node `v24.18.0` runtime, `npm run build` passed: TypeScript project build and Vite production build completed successfully.
- The earlier Windows-binding error came from invoking Windows Node against WSL-installed dependencies; it was not a missing project dependency and required no dependency-tree changes.
