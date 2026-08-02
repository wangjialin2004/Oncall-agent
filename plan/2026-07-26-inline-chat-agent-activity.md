# 对话内实时工具 / 专家过程流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前独立右侧过程栏改为助手消息内部的实时执行轨迹，让路由、计划、工具调用和专家委派在最终答案生成前持续可见，同时在服务端出站边界移除参数、原始结果、提示词和内部标识。

**Architecture:** 保留现有 `route_event` / `agent_event` / `tool_event` / `decision_event` SSE 类型与 `AgentRun.events` 状态链路，在 API 出站处生成最小公开事件，再由前端把公开事件归并成对话内单列活动流。运行中的活动流自动展开；完成后折叠成摘要；并行专家显示为同一活动下的纵向分支，不再使用窄栏横向卡片。最终答案仍按现有 `content` 事件流式追加，活动流负责覆盖取证阶段的等待空白，不伪造提前答案。

**Tech Stack:** FastAPI / SSE / Python 3.14、React 18、TypeScript、Vite、Vitest、Testing Library、现有 Lucide React 图标与 CSS token。

---

## 1. Problem and confirmed evidence

### Current behavior

1. `App.tsx` 已逐帧消费 SSE，并将 timeline 事件写入 `AgentRun.events`；因此前端不是“只在 complete 后才收到全部过程”。
2. `ChatWorkspace.tsx` 只渲染 `ChatMessage.content`，运行初期内容为空，所以对话区呈现空白；过程只能在独立 `AgentProcessPanel` 中看到。
3. `tools_runtime.py::_execute_tools()` 仅在专家委派前发送 start 事件；一般工具的 `tool_event` 在执行完成后才生成。并行专家的子事件由 `_delegate_child_events()` 在 fan-out 全部结束后回灌。
4. 当前横向专家网格位于 320–720 px 的右侧栏内，`minmax(160px, 1fr)` 在三专家场景下造成中文逐字换行，截图中的视觉问题可稳定解释。
5. 当前前端详情会读取 `payload.arguments`、`payload.result`、`subtask` 等字段。即使 UI 折叠，这些字段仍已到达浏览器，不满足“不该暴露就不要暴露”。

### Intended user experience

```text
助手消息
┌──────────────────────────────────────────────┐
│ ◌ 正在排查 · 已进行 18 秒                    │
│ │                                            │
│ ├─ ✓ 已识别为综合诊断                        │
│ ├─ ✓ 已制定排查步骤                          │
│ ├─ ◌ 正在并行调用 3 位专家                   │
│ │   ├─ 指标专家 · 正在检查指标               │
│ │   ├─ 日志专家 · 正在检索日志               │
│ │   └─ 变更专家 · 等待结果                   │
│ └─ ○ 正在整理结论                            │
│                                              │
│ [最终答案开始流式出现……]                     │
└──────────────────────────────────────────────┘
```

- 运行时：自动展开，第一条 route/start 事件到达即显示。
- 完成后：默认折叠为“已完成 · 4 步 · 3 位专家 · 2 次工具调用”，用户可展开。
- 工具：只显示产品化名称、状态、耗时和安全结果状态，例如“查询指标 · 已完成 · 1.8 s”。
- 专家：只显示专家角色和运行状态，不展示子任务提示词或模型草稿。
- 失败：显示“调用失败，已降级继续”，不展示异常堆栈、URL、查询参数或原始错误。

## 2. Decisions and defaults requiring approval

| Item | Proposed decision |
|---|---|
| Chat default | 对话视图默认启用 inline activity；旧右侧过程栏退出默认布局 |
| Baseline view | 服务基线页面的右侧帮助栏保留，不受影响 |
| Rollback flag | `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` 恢复旧过程栏；默认 `true` |
| SSE types | 保留现有四类 timeline type，不增加不兼容 type |
| Public event boundary | SSE 单事件、`complete.events`、历史会话 `turn.events` 均应用同一公开投影 |
| Hidden data | 丢弃 `arguments`、`result`、`subtask`、prompt/context、trace/span/evidence ID、usage、原始 error |
| Final answer latency | 不提前生成未经取证的回答；通过实时活动流解决空白等待感 |
| Dependencies | 不新增 npm / Python 依赖 |

## 3. Scope and non-goals

### In scope

- 对话消息内展示实时 route / plan / tool / expert / verify / complete 过程。
- 并行专家用纵向分支展示，支持 1–8 位专家且不横向挤压正文。
- 一般工具在执行前生成 `in_progress` 公开事件，完成后更新同一 activity。
- 服务端公开事件投影和历史恢复一致性。
- 运行中自动展开、完成后自动折叠、键盘可访问、reduced-motion 兼容。
- 旧过程栏通过环境变量保留一个版本周期作为回滚路径。

### Non-goals

- 不改变 Agent 的工具选择、并行策略、路由或证据判断。
- 不展示模型思维链、内部决策文本、工具参数、原始工具结果或专家草稿。
- 不承诺缩短最终答案的真实 TTFT；本阶段改善的是取证期间的可感知响应。
- 不引入 AI SDK、OpenAI Agents SDK 或新的状态管理库。
- 不删除 `AgentProcessPanel` 源码；确认新路径稳定后另立退役计划。

## 4. Existing capability reuse

- 复用 `frontend/src/api/agentStream.ts` 的逐帧 SSE parser。
- 复用 `AgentRun.events`、`normalizeTimelineEvents()` 和现有事件去重语义。
- 从 `processContent.ts` 复用工具 / 专家的人类可读名称映射；拆出纯展示 helper，避免 inline 组件依赖 1500 行旧面板。
- 复用 Lucide React 图标和现有 paper / graphite / status CSS token。
- 复用 `make_agent_event()` / `make_tool_event()`，不改变事件 type 语义。

## 5. External research checked on 2026-07-26

| Candidate | Version / release checked | License | Maintenance and security status | Compatibility | Decision |
|---|---|---|---|---|---|
| [Vercel AI SDK](https://github.com/vercel/ai) / [tool usage docs](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-tool-usage) | npm `ai@7.0.37`; repo pushed 2026-07-26 | Apache-2.0 | Active; GitHub Community Profile 87%; no repo-local security policy surfaced | React compatible, but existing project already has a working SSE parser | Adopt the lifecycle idea (`input-streaming` → `input-available` → `output-available` / `output-error`) as local activity states; reject dependency addition |
| [OpenAI Agents SDK Python](https://github.com/openai/openai-agents-python) / [streaming docs](https://openai.github.io/openai-agents-python/streaming/) | `v0.18.3`, published 2026-07-17; pushed 2026-07-26 | MIT | Active; Community Profile 75%; no repo-local security policy surfaced | Python compatible, but replacing the custom harness would be a broad architecture migration | Adopt separation between semantic `RunItemStreamEvent` and raw token stream as a design model; reject dependency / migration |
| [OpenAI Codex](https://github.com/openai/codex) | `rust-v0.145.0`, published 2026-07-21; pushed 2026-07-26 | Apache-2.0 | Active; Community Profile 75%; no repo-local security policy surfaced | TUI implementation is not directly reusable in React | Use as interaction reference: concise chronological tool rows, progressive status, details hidden by default; do not copy code |
| [Anthropic Claude Code](https://github.com/anthropics/claude-code) | `v2.1.220`, published/pushed 2026-07-25 | No repository license detected | Active issue/release repository; Community Profile 50%; no repo-local security policy surfaced | UX reference only; license does not permit assuming source reuse | Use only the user-requested visual behavior as reference; reject code reuse and dependency |

No third-party code will be copied and no dependency or license surface will change.

## 6. File map

### Create

- `app/agent/public_events.py` — single server-side allowlist/projection for public timeline events.
- `frontend/src/components/chat/inlineActivityModel.ts` — pure event → activity-group reducer.
- `frontend/src/components/chat/InlineAgentActivity.tsx` — accessible inline activity UI.
- `frontend/src/components/chat/__tests__/inlineActivityModel.test.ts` — reducer privacy/dedup/parallel tests.
- `frontend/src/components/chat/__tests__/InlineAgentActivity.test.tsx` — rendered running/completed/keyboard tests.
- `tests/test_public_agent_events.py` — server allowlist and nested `complete.events` tests.
- `tests/test_public_progress_e2e.py` — SSE → frontend-safe payload → persistence/reload contract E2E.
- `plan/2026-07-26-inline-chat-agent-activity-progress.md` — implementation deviations and evidence.

### Modify

- `app/api/assistant.py` — project every outgoing stream event; persist original internal event.
- `app/api/conversations.py` — project restored `turn.events` before response serialization.
- `app/agent/harness/tools_runtime.py` — emit safe lifecycle start markers before ordinary tool execution; do not include arguments.
- `app/config.py` / `.env.example` — document degradation switch and default.
- `frontend/src/types/events.ts` — add public activity presentation types without widening raw payload access.
- `frontend/src/api/agentStream.ts` — accept projected events and reject unexpected private payload keys defensively.
- `frontend/src/App.tsx` — pass per-message runs into chat; select inline vs legacy panel by flag.
- `frontend/src/components/ChatWorkspace.tsx` — render inline activity before assistant Markdown and scroll on run progress.
- `frontend/src/components/AppShell.tsx` — support an optional right panel in chat mode while preserving baseline layout.
- `frontend/src/components/agent-process/processContent.ts` — export safe product labels shared by legacy and inline paths.
- `frontend/src/components/__tests__/ChatWorkspace.test.tsx` — blank-answer progress and final answer assertions.
- `frontend/src/components/__tests__/agentStream.test.ts` — public event/private key rejection tests.
- `frontend/src/components/__tests__/App.test.tsx` — flag-on inline / flag-off legacy regression.
- `frontend/src/styles.css` — single-column inline trace, expert branches, responsive and reduced-motion rules.
- `AGENTS.md` — index and completion status only.

## 7. Public event contract

The browser-visible event keeps compatible type semantics but is allowlist-only:

```python
PUBLIC_TOP_LEVEL_KEYS = {
    "type",
    "agent",
    "stage",
    "status",
    "tool",
    "route",
    "duration_ms",
    "started_at",
}

PUBLIC_PAYLOAD_KEYS = {
    "experts",
    "delegated_expert",
    "parallel",
    "wall_ms",
    "step",
    "results",  # each item reduced to expert + status only
    "tool_call_id",  # replaced with an opaque per-response activity id
    "parent_tool_call_id",  # replaced with the same opaque namespace
}
```

Implementation rule:

```python
def to_public_stream_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if event.get("type") == "complete":
        public = {key: value for key, value in event.items() if key not in {"events"}}
        public["events"] = [to_public_timeline_event(item) for item in event.get("events") or []]
        return public
    if event.get("type") in TIMELINE_EVENT_TYPES:
        return to_public_timeline_event(event)
    return dict(event)
```

`arguments`, `result`, `subtask`, `reason`, `context`, `prompt`, `trace_id`, `span_id`, `usage`, `evidence_id`, raw errors and unknown payload keys must be absent from live SSE and history JSON. Internal state and database persistence continue using the original event.

## 8. Implementation tasks

### Implementation deviations recorded after approval

- Ordinary tool starts will use `agent_event(stage="tool_start", status="in_progress")`, not a second `tool_event`, because the existing harness metrics and event counters treat `tool_event` as a terminal tool observation. This preserves compatibility while giving the UI an immediate lifecycle marker.
- Public activity correlation IDs will be mapped by a request-scoped `PublicEventProjector` instance. A stateless helper would generate a different opaque ID when the same raw call ID appears in start, terminal, nested complete events, and history projections.
- The inline reducer keeps a local product-label allowlist instead of importing legacy `processContent` detail helpers, because that module intentionally exposes raw event-oriented details needed by the old panel.
- The rollback switch is frontend-only (`VITE_INLINE_AGENT_ACTIVITY_ENABLED`); `app/config.py` stays unchanged so backend configuration cannot imply a Vite build-time flag.

### Task 1: Server-side public event projection

**Files:**
- Create: `app/agent/public_events.py`
- Create: `tests/test_public_agent_events.py`

- [x] **Step 1: Write failing allowlist tests**

```python
def test_public_tool_event_removes_arguments_results_and_internal_ids():
    event = {
        "type": "tool_event",
        "tool": "search_app_logs",
        "status": "completed",
        "trace_id": "trace-secret",
        "payload": {
            "arguments": {"keyword": "customer-secret"},
            "result": {"raw": "private-log"},
            "delegated_expert": "log",
        },
    }
    public = to_public_timeline_event(event)
    assert public["tool"] == "search_app_logs"
    assert public["payload"] == {"delegated_expert": "log"}
    assert "trace_id" not in public
    assert "customer-secret" not in json.dumps(public)
    assert "private-log" not in json.dumps(public)
```

- [x] **Step 2: Run the focused test and confirm it fails**

Run: `PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_public_agent_events.py -q --no-cov`

Expected: import/function missing failure.

- [x] **Step 3: Implement the pure projection helper**

Implement explicit top-level and payload allowlists, recursive `complete.events` projection, bounded string lengths, safe expert result reduction, and request-local opaque activity IDs. Do not mutate the input mapping.

- [x] **Step 4: Rerun the focused test**

Expected: all `tests/test_public_agent_events.py` tests pass.

### Task 2: Apply the public boundary to live and restored events

**Files:**
- Modify: `app/api/assistant.py`
- Modify: `app/api/conversations.py`
- Create: `tests/test_public_progress_e2e.py`

- [x] **Step 1: Add an API-level failing test**

The fake harness stream must emit a private tool payload followed by `complete.events` containing the same payload. Assert the streamed JSON contains the safe tool/status fields but not the argument or result sentinel. Persist a turn with private events, GET `/api/conversations/{session_id}`, and assert restored events are projected identically.

- [x] **Step 2: Project only at serialization boundaries**

In `assistant.py`, pass `event` to persistence/commit unchanged and pass `to_public_stream_event(event)` to `json.dumps`. In `conversations.py`, copy each returned turn and replace only its `events` list with projected events.

- [x] **Step 3: Run API E2E and authorization regression**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_public_progress_e2e.py \
  tests/test_api_authorization_matrix.py \
  -q --no-cov
```

Expected: public privacy assertions pass; owner/auth behavior unchanged.

### Task 3: Emit visible lifecycle starts for ordinary tools

**Files:**
- Modify: `app/agent/harness/tools_runtime.py`
- Modify: `tests/test_harness_service.py`
- Modify: `tests/test_m1_close_the_loop.py`

- [x] **Step 1: Add a failing ordering assertion**

For one ordinary tool call, assert a `tool_event(status="in_progress")` is yielded before the executor resolves and the existing terminal tool event follows with `completed` or `failed`. The start event may contain `tool` and opaque call identity only; it must not contain arguments.

- [x] **Step 2: Generalize the existing delegate-start helper**

Replace the delegate-only preflight helper with a focused lifecycle helper in `tools_runtime.py`. Preserve `delegate_parallel_start` / `delegate_start`; add ordinary `tool_event` start markers. Keep `loop.py` unchanged and keep the file under 1000 lines.

- [ ] **Step 3: Run harness regressions**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_harness_service.py \
  tests/test_m1_close_the_loop.py \
  -k 'tool or delegate or two_turn or history' \
  -q --no-cov
```

Expected: ordering assertion passes; existing soft path remains green.

### Task 4: Build the pure inline activity model

**Files:**
- Create: `frontend/src/components/chat/inlineActivityModel.ts`
- Create: `frontend/src/components/chat/__tests__/inlineActivityModel.test.ts`
- Modify: `frontend/src/components/agent-process/processContent.ts`

- [x] **Step 1: Write reducer tests**

Tests must cover:

- route → plan → tool start → tool done ordering;
- parallel experts grouped under one parent with 2/3/8 branches;
- child expert events attributed by opaque parent ID;
- duplicate terminal events merged into the existing activity;
- unknown tools mapped to “执行检查” rather than exposing function names;
- no model property includes `arguments`, `result`, raw payload, prompt, trace/span ID or expert subtask;
- completed summary counts tools and experts correctly.

- [x] **Step 2: Implement typed local states**

```ts
export type InlineActivityState =
  | "queued"
  | "running"
  | "completed"
  | "degraded"
  | "failed";

export type InlineActivityItem = {
  id: string;
  kind: "route" | "plan" | "tool" | "expert-group" | "expert" | "verify" | "report";
  label: string;
  state: InlineActivityState;
  durationMs?: number;
  children?: InlineActivityItem[];
};
```

The reducer must be pure, stable-ID based, single-pass, and independent from React.

- [x] **Step 3: Run model tests**

Run: `npm test -- --run src/components/chat/__tests__/inlineActivityModel.test.ts`

Expected: all reducer/privacy tests pass.

### Task 5: Render the activity feed inside each assistant message

**Files:**
- Create: `frontend/src/components/chat/InlineAgentActivity.tsx`
- Create: `frontend/src/components/chat/__tests__/InlineAgentActivity.test.tsx`
- Modify: `frontend/src/components/ChatWorkspace.tsx`
- Modify: `frontend/src/types/events.ts`
- Modify: `frontend/src/styles.css`

- [x] **Step 1: Write rendering tests**

Render an assistant message with empty content and a running `AgentRun`; assert the visible activity exists immediately. Append a tool event and expert group; assert one tool row and vertically stacked expert branches. Mark the run complete; assert the compact summary appears and details can be reopened with keyboard.

- [x] **Step 2: Implement the component**

Use semantic `<details>` / `<summary>` or an equivalent button+region contract. Auto-open while running; after completion, collapse only once and never fight a user’s explicit toggle. Use Lucide `LoaderCircle`, `Check`, `Wrench`, `Users`, `CircleAlert`, and `ChevronDown`; no manually drawn SVG.

- [x] **Step 3: Integrate with message rendering**

`ChatWorkspace` receives `runs: Record<string, AgentRun>` and renders `<InlineAgentActivity run={runs[item.id]} />` before Markdown for assistant messages. Its autoscroll dependency includes the active run event count and answer length, not the entire runs object.

- [x] **Step 4: Apply responsive styling**

- no nested large cards;
- one 2 px activity rail and compact rows;
- expert branches remain one column at all widths;
- assistant bubble uses a readable max width independent from a right process panel;
- `@media (prefers-reduced-motion: reduce)` disables spinner/expand transitions;
- focus-visible ring and 44 px touch targets for toggles.

- [x] **Step 5: Run component tests**

Run:

```bash
npm test -- --run \
  src/components/chat/__tests__/InlineAgentActivity.test.tsx \
  src/components/__tests__/ChatWorkspace.test.tsx
```

Expected: empty-answer progress, final Markdown, accessibility and privacy assertions pass.

### Task 6: Switch chat layout with a degradation flag

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/components/__tests__/App.test.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `.env.example`

- [x] **Step 1: Add flag-on / flag-off tests**

With `VITE_INLINE_AGENT_ACTIVITY_ENABLED` enabled/defaulted, assert chat mode passes no process panel and uses inline activities. With it set to `false`, assert the existing `AgentProcessPanel` and resizer still render. Baseline view must keep its side help in both modes.

- [x] **Step 2: Make `AppShell.panel` optional**

When panel is absent, render `.app-shell.has-no-process-panel`, omit the splitter and process `<aside>`, and let the workspace occupy the remaining width. Do not remove resizer code because it is the rollback path.

- [x] **Step 3: Wire callbacks without duplicating the old panel**

Pass `runs` to `ChatWorkspace`. Feedback, distill confirmation and suggested-action controls remain available in a compact message footer for completed assistant turns; preserve their existing API callbacks and owner authorization behavior.

- [x] **Step 4: Run frontend regression**

Run:

```bash
npm test -- --run \
  src/components/chat/__tests__/inlineActivityModel.test.ts \
  src/components/chat/__tests__/InlineAgentActivity.test.tsx \
  src/components/__tests__/ChatWorkspace.test.tsx \
  src/components/__tests__/agentStream.test.ts \
  src/components/__tests__/App.test.tsx
```

Expected: focused suite passes with both flag states.

### Task 7: Full validation and live E2E

**Files:**
- Create: `plan/2026-07-26-inline-chat-agent-activity-progress.md`
- Modify: `plan/2026-07-26-inline-chat-agent-activity.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Backend focused + soft-path regression**

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_public_agent_events.py \
  tests/test_public_progress_e2e.py \
  tests/test_harness_service.py \
  tests/test_m1_close_the_loop.py \
  tests/test_api_authorization_matrix.py \
  -q --no-cov
```

- [x] **Step 2: Frontend full regression and build**

```bash
cd frontend
npm test -- --run
npm run build
```

- [x] **Step 3: Automated path E2E**

Exercise: authenticated `/api/assistant` SSE → route/plan/tool/expert public events → content/complete → persisted turn → `/api/conversations/{id}` reload. Assert event ordering, stable public identity, final answer, and absence of private sentinels on both live and reload paths.

- [ ] **Step 4: Live/runtime E2E**

On the running WSL stack, create a non-secret session ID and send a diagnosis request that triggers metric + log parallel experts. Record:

- first public progress event time;
- first final content time;
- tool/expert visible states;
- complete status;
- restored history activity summary;
- live SSE/history search proving private arguments/results are absent.

If the upstream LLM or tools degrade, record the degraded result and do not claim semantic continuity until a healthy run passes.

- [ ] **Step 5: Browser QA**

The flow under test is: authenticated chat → send diagnosis → inline progress appears before final prose → expert branches update → final answer streams → completed activity collapses → history reload restores the same public summary.

Validate at desktop 1280×720 and mobile 390×844:

- page identity and nonblank content;
- no framework overlay;
- no relevant console errors/warnings;
- screenshot evidence;
- keyboard expand/collapse;
- no horizontal overflow or one-character wrapping;
- flag-off legacy rollback smoke test.

- [x] **Step 6: Record evidence and update index**

Write exact commands, counts, session/request identifiers and screenshots to the progress document. Mark the AGENTS index complete only after automated path E2E, live/runtime E2E, browser QA, full frontend tests and build have all passed.

## 9. Exit criteria

1. An empty assistant message shows meaningful progress after the first route/start SSE event; no blank waiting bubble.
2. A three-expert parallel call renders as one parent row with three vertical branches, without horizontal cards or character-by-character wrapping.
3. Ordinary tool calls have visible running and terminal states.
4. Final prose streams in the same assistant message below the process feed.
5. Live SSE, nested `complete.events`, and restored history contain no tool arguments, raw results, prompts, subtasks, internal IDs, usage or raw errors.
6. Completed activities collapse to a concise summary and remain keyboard-accessible.
7. Feedback, distill and suggested actions remain usable.
8. `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` restores the legacy panel without backend or data rollback.
9. Focused backend/frontend tests, relevant harness soft-path regression, full frontend tests and production build pass.
10. Automated API→stream→persist→reload E2E and healthy live/browser E2E pass with recorded evidence.

## 10. Risks and rollback

| Risk | Mitigation |
|---|---|
| Public projection removes a field needed by old panel | Flag-off regression uses the same projected contract; keep only explicitly approved safe fields and add fixture coverage |
| Duplicate start/done events inflate counts | Stable opaque activity identity + reducer merge tests |
| Frequent events cause scroll/re-render churn | Pure reducer, memoized per-run model, primitive effect dependencies, no subscription to unrelated runs |
| Completion auto-collapse overrides user interaction | Track user toggle separately; auto-collapse only on the first running→terminal transition |
| Historical events contain unknown/private fields | Apply the same server projection on history reads; frontend also ignores unknown payload keys |
| Runtime E2E blocked by upstream | Pass automated path E2E, record degraded live evidence, keep status incomplete |

Rollback:

1. Set `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` and restart Vite to restore the legacy right panel.
2. The public event projection remains enabled because it is a data-minimization/security boundary; reverting it requires a separate reviewed decision.
3. No schema migration or stored-event rewrite is required; internal persisted events remain unchanged.
