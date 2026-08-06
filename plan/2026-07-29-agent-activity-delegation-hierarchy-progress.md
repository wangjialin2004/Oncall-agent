# Agent Activity Delegation Hierarchy Progress

## Status

Implementation and automated verification passed on 2026-07-29. Authenticated Browser visual QA remains pending because the available running Vite page presents the login form and no test credentials or authenticated browser session were available. Authentication was not bypassed.

## Delivered Behavior

- A non-empty public `parent_tool_call_id` is resolved before root tool and delegation branches.
- Child Agent `tool_start` and terminal `tool_event` rows upsert one stable tool item under the owning expert at feed depth two.
- Parented delegation markers reuse the parent dispatch group, so no second `调用领域专家` group is created.
- The granular feed uses bounded depth-two indentation: 52px desktop and 36px in the existing mobile media query.
- The aggregate inline card shares the same model and recursively renders the child tool below its Agent.

## Material Deviation

The initial plan stated that every parented `delegate_parallel_done` should update a single owning expert. The backend terminal event is emitted by `tools_runtime.py` with `parent_tool_call_id` but without `delegated_expert`; it represents completion of the parent parallel call. The implementation therefore resolves that event to the existing parent group and completes its child experts, while parented per-expert lifecycle events still update only their owning Agent. No public event shape changed.

## Verification Evidence

| Check | Command / Fixture | Result |
| --- | --- | --- |
| Red regression proof | `npm test -- --run src/components/chat/__tests__/inlineActivityModel.test.ts` before reducer change | Failed as expected: two expert groups were created instead of one. |
| Focused frontend / streamed application path | `npm test -- --run src/components/__tests__/App.test.tsx src/components/chat/__tests__/inlineActivityModel.test.ts src/components/chat/__tests__/AgentActivityFeed.test.tsx src/components/chat/__tests__/InlineAgentActivity.test.tsx` | 4 files, 30 tests passed. The App fixture uses public IDs `activity-parent`, `activity-child-dispatch`, and `activity-metric-tool`; it verifies one group and the tool inside an expert branch. |
| Full frontend regression | `npm test` | 16 files, 113 tests passed. |
| Production build | `npm run build` | Passed. Existing Vite `INEFFECTIVE_DYNAMIC_IMPORT` warning for `src/api/httpClient.ts` remains. |
| Public SSE/API continuity | `PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_public_agent_events.py tests/test_public_progress_e2e.py -q --no-cov` | 5 passed in 0.65s. |

## Rendered QA Boundary

The existing rendered Testing Library App test exercises the public mock SSE sequence through `streamAgent` into the aggregate activity card. It asserts one root group and a descendant tool under an expert branch. The live Vite app at `http://127.0.0.1:5173/` was reachable but stopped at its username/password screen. No authenticated desktop/mobile screenshots, console capture, or width/overflow measurements were taken. This is the remaining acceptance risk.

## Rollback

Revert the scoped reducer, feed CSS, focused tests, this plan/progress record, and the matching `AGENTS.md` index entry. For immediate display rollback, set `VITE_GRANULAR_AGENT_ACTIVITY_ENABLED=false` to use the aggregate inline card, or set `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` to restore the legacy process panel, then restart Vite.
