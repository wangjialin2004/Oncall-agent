# Agent Activity Delegation Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render one dispatch group per public parent activity, with each child Agent's tool and MCP calls nested beneath that Agent rather than repeated as top-level dispatch prompts.

**Architecture:** Keep the existing public SSE projection and opaque `tool_call_id` / `parent_tool_call_id` contract unchanged. Update the frontend activity reducer to resolve a parent before handling generic `tool_start`, `delegate_start`, and `delegate_parallel_*` stages; parented lifecycle markers update the owning expert instead of creating another expert group. The shared model remains the source for both `InlineAgentActivity` and `AgentActivityFeed`, with feed depth extended to make tool nesting visible.

**Tech Stack:** React 18, TypeScript, Vitest, Testing Library, existing public `TimelineEvent` contract.

**Research:** This is a regression fix, not a new capability or dependency. Existing repository support was checked: `PublicEventProjector` already preserves opaque `parent_tool_call_id`, and `tools_runtime.py` attaches it to child expert events. No external dependency or code adoption is proposed.

## Problem

The current reducer handles `stage === "tool_start"` and delegation starts before it checks `parent_tool_call_id`. A child Agent's start event is therefore projected as a root tool, while its terminal `tool_event` may later be associated with the Agent. A child delegation lifecycle marker can also create a second root `expert-group`, producing repeated "call domain experts" UI even when it belongs to an existing public parent activity.

## Decisions And Defaults

1. A public `parent_tool_call_id` is authoritative. It selects the existing dispatch group and the event's public `delegated_expert` (or sanitized `agent`) selects its child Agent.
2. A parented `tool_start` and its terminal `tool_event` share one tool row by opaque activity ID and live below that Agent.
3. A parented `delegate_start`, `delegate_parallel_start`, or `delegate_parallel_done` updates the owning Agent state; it does not create another root expert group. This intentionally keeps the UI at dispatch -> Agent -> tool depth.
4. Events without a public parent remain separate dispatches. Do not merge by label, expert name, time, or route; those heuristics could hide legitimate second delegations.
5. Keep the existing `VITE_GRANULAR_AGENT_ACTIVITY_ENABLED` and `VITE_INLINE_AGENT_ACTIVITY_ENABLED` behavior. The fix applies to both modes through their shared reducer.

## Scope And Non-Goals

In scope: frontend model grouping, feed indentation, regression tests, rendered mock-browser verification, and progress evidence.

Out of scope: backend event-schema changes, harness delegation policy, persistence rewrites, new flags, changing tool labels, exposing tool arguments/results/prompts/trace data, and merging unrelated dispatches.

## Affected Files

- Modify: `frontend/src/components/chat/inlineActivityModel.ts`
- Modify: `frontend/src/components/chat/__tests__/inlineActivityModel.test.ts`
- Modify: `frontend/src/components/chat/__tests__/AgentActivityFeed.test.tsx`
- Modify: `frontend/src/components/chat/__tests__/InlineAgentActivity.test.tsx`
- Modify: `frontend/src/components/__tests__/App.test.tsx`
- Modify: `frontend/src/styles.css`
- Create: `plan/2026-07-29-agent-activity-delegation-hierarchy-progress.md`
- Modify: `AGENTS.md`

## Flags, Rollback, And Safety

No new flag is introduced. Rollback remains `VITE_GRANULAR_AGENT_ACTIVITY_ENABLED=false` for the aggregate inline card and `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` for the legacy panel. The public event allowlist remains unchanged, and tests must assert that no private payload fields appear in the derived model or DOM.

## Verification And Exit Criteria

```bash
source ~/.nvm/nvm.sh
cd frontend
npm test -- --run \
  src/components/chat/__tests__/inlineActivityModel.test.ts \
  src/components/chat/__tests__/AgentActivityFeed.test.tsx \
  src/components/chat/__tests__/InlineAgentActivity.test.tsx
npm test
npm run build
```

Run the existing automated API/SSE/history path to prove the unchanged parent-ID contract:

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_public_agent_events.py \
  tests/test_public_progress_e2e.py \
  -q --no-cov
```

Use the running Vite app with a public mock SSE sequence at 1280x720 and 390x844. The sequence must contain one parallel parent, two child Agents, child `tool_start` plus terminal MCP/tool events, and a parented nested delegation marker. Verify: exactly one root dispatch group, no top-level child tool, tools visually appear below their Agent, no horizontal overflow, and no relevant console errors. Record commands and pass/fail evidence in the progress file.

## Risks

- Reordering parent resolution can accidentally swallow an independent dispatch. Mitigation: only branch when a non-empty public parent ID exists; preserve the current root path otherwise.
- A terminal event can arrive before its start marker. Mitigation: the child tool upsert must create a stable row from either event and update it in place when its counterpart arrives.
- Deeper indentation can overflow on mobile. Mitigation: use bounded depth styling with a smaller mobile offset and test at 390px.

### Task 1: Lock The Event Regression

**Files:**
- Modify: `frontend/src/components/chat/__tests__/inlineActivityModel.test.ts`

- [x] **Step 1: Add a failing parented lifecycle fixture**

Add one test whose events are: `delegate_parallel_start(activity-parent, metric+log)`, a parented `delegate_start` from `metric_expert`, a parented `tool_start(query_memory_metrics, activity-metric-tool)`, its parented terminal `tool_event`, and equivalent log MCP start/terminal events. Assert:

```ts
expect(model.items.filter((item) => item.kind === "expert-group")).toHaveLength(1);
expect(model.feed.map((item) => item.id)).toEqual([
  "experts:activity-parent",
  "experts:activity-parent:metric",
  "experts:activity-parent:log",
  "tool:activity-metric-tool",
  "tool:activity-log-tool",
]);
expect(model.feed.find((item) => item.id === "tool:activity-metric-tool")).toMatchObject({
  parentId: "experts:activity-parent:metric",
  depth: 2,
  state: "completed",
});
```

- [x] **Step 2: Run the focused test and confirm it fails**

```bash
source ~/.nvm/nvm.sh
cd frontend
npm test -- --run src/components/chat/__tests__/inlineActivityModel.test.ts
```

Expected before implementation: the child start appears as a root tool and/or a parented delegation produces a second expert group.

### Task 2: Resolve Parents Before Generic Stages

**Files:**
- Modify: `frontend/src/components/chat/inlineActivityModel.ts`

- [x] **Step 1: Add a parented-event helper**

Before generic `tool_start` and delegation handling, resolve `parentActivityId(event)`. For a parented event, get `expertGroup(parentId)`, resolve the owner with `payload.delegated_expert ?? event.agent`, and upsert that expert. Handle `tool_start` and `tool_event` through one stable tool-upsert path using `activityId(event)`; call `publicTool(payload.tool ?? event.tool)` and preserve duration/state.

- [x] **Step 2: Collapse parented delegation markers**

For parented `delegate_start`, `delegate_parallel_start`, and `delegate_parallel_done`, update the owning expert state and do not call `expertGroup(activityId(event))`. Keep the existing root-group logic unchanged for unparented delegation events.

- [x] **Step 3: Extend feed depth safely**

Change `InlineActivityItem.depth` and `registerFeed` to support `0 | 1 | 2`. Register an Agent at depth `1` and its tool at depth `2`; do not add unbounded nesting.

- [x] **Step 4: Run the focused model test**

Run the Task 1 command. Expected: all model tests pass, including start/terminal in-place updates and the existing private-payload assertions.

### Task 3: Render The Third Level In The Feed

**Files:**
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/components/chat/__tests__/AgentActivityFeed.test.tsx`

- [x] **Step 1: Add a feed render assertion**

Render the Task 1 model through `AgentActivityFeed`. Assert the MCP/tool row has `data-activity-id="tool:activity-metric-tool"`, follows its `metric` Agent row, and carries a depth-two class or data attribute distinct from the Agent's depth-one presentation.

- [x] **Step 2: Add bounded desktop and mobile indentation**

Use a depth-specific class/data attribute for tool rows. Apply a second bounded inset on desktop and a smaller inset inside the existing mobile media query; do not introduce nested cards or allow an arbitrary depth value.

- [x] **Step 3: Run the focused feed tests**

```bash
source ~/.nvm/nvm.sh
cd frontend
npm test -- --run \
  src/components/chat/__tests__/inlineActivityModel.test.ts \
  src/components/chat/__tests__/AgentActivityFeed.test.tsx \
  src/components/chat/__tests__/InlineAgentActivity.test.tsx
```

Expected: all focused tests pass and aggregate mode still recursively renders each child tool under its Agent.

### Task 4: Full Regression, Rendered QA, And Evidence

**Files:**
- Create: `plan/2026-07-29-agent-activity-delegation-hierarchy-progress.md`
- Modify: `AGENTS.md`

- [x] **Step 1: Run frontend and public-contract regressions**

Run every command in Verification And Exit Criteria. Record exact pass/fail totals, known unrelated warnings, and the fact that no backend contract changed.

- [ ] **Step 2: Run rendered desktop and mobile QA**

Use the Browser plugin if available. If authenticated live data cannot safely reproduce the event sequence, use the existing public mock-SSE test harness rather than bypassing authentication. Capture desktop and mobile screenshots after triggering the event sequence; record DOM/console evidence and overflow measurements.

- [x] **Step 3: Record status and update the index**

Write the verification evidence, session identifier, screenshots, residual risk, and rollback commands in the progress file. Update this plan's entry in `AGENTS.md` from in-progress to its evidence-backed status.

## Rollback

Revert only the reducer, tests, CSS, plan, progress record, and index entry introduced by this work. If an immediate visual rollback is needed, set `VITE_GRANULAR_AGENT_ACTIVITY_ENABLED=false` or, for the legacy panel, `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false`, then restart Vite. Do not roll back public event projection or alter persisted event data.
