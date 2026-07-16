# 智能体过程栏信息架构与步骤展示优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将过程栏从“原始 SSE 事件列表”改为“面向 OnCall 的业务步骤视图”，准确展示步骤状态、耗时、关键结果和按需展开的详细信息。

**Architecture:** 保留后端现有 SSE 事件契约，在前端新增纯函数投影层，将 `TimelineEvent[]` 归并为稳定的 `ProcessPanelModel`。视图层只消费该模型，不再直接判断原始事件；旧展示保留在功能开关后作为一版回退路径。

**Tech Stack:** React 18、TypeScript、Vite、Vitest、Testing Library、Lucide React、现有 CSS token。

---

## 1. Context（现状与问题）

当前实现集中在 `frontend/src/components/AgentProcessPanel.tsx`，约 1400 行，同时承担：

1. 原始事件中文化和工具结果解析。
2. 计划进度猜测。
3. SSE 事件到步骤卡的转换。
4. 时间线、详情、反馈和 checkpoint UI 渲染。

这造成三类直接影响用户的问题：

- **显示逻辑不准确**：`buildStepCards()` 基本按“一条事件 = 一个步骤”处理。`start`、`model_decision`、`tool_event`、`verify in_progress`、`verify completed`、`complete` 会被分别编号，页面上的“过程步数”实际是事件数。
- **步骤状态存在误导**：`buildPlanProgress()` 依赖计划文本与工具名模糊匹配；当 `run.status === "completed"` 时直接将所有计划项改成 `done`，即使部分计划没有执行。
- **详细内容层级过平**：参数、计划、证据、结果默认全部展开；摘要与键值经常重复；技术字段与业务结论混排，用户很难快速回答“现在到哪一步、得到了什么、哪里失败”。

现有后端事件已经提供足够的第一版归并依据：

- `type`、`stage`、`status`、`summary`
- `payload.step`（模型决策、收尾、超时等阶段）
- `evidence_id`、`span_id`、`duration_ms`、`usage`
- 按 SSE 到达顺序排列的同一轮事件

因此本次不要求后端新增事件字段；前端先通过确定性规则归并。若后续发现跨步骤工具事件无法可靠关联，再单独设计 additive event contract，而不是在本次 UI 改造中扩散范围。

## 2. 设计决策

### 2.1 信息层级

过程栏只保留三层信息：

1. **本轮摘要**：当前状态、路由、当前阶段、实际工具/专家/证据数量、总耗时。
2. **业务步骤**：步骤名称、状态、耗时、一句话结果。用户不展开也能理解主链路。
3. **步骤详情**：执行内容、关键结果、技术信息。默认折叠，当前步骤和异常步骤默认展开。

目标结构：

```text
智能体过程                                      [运行中]
综合诊断 · 当前：查询指标与告警                  38.4 s
3 次工具调用 · 2 条有效证据 · 1 次专家委派

01  识别请求                         [已完成]
    路由为综合诊断

02  制定排查计划                     [已完成]
    已规划 4 项排查内容
    [查看计划]

03  查询指标与告警                   [进行中] 12.8 s
    checkout-api 当前无 firing 告警
    [收起详情]
      执行内容    服务 / 时间窗口 / 查询目标
      关键结果    结论 / 证据摘要 / 数据源状态
      技术信息    evidence_id / trace_id / span_id / usage

04  证据自检                         [等待中]
05  生成结论                         [等待中]
```

### 2.2 步骤不是事件

投影层按以下优先级归并事件：

| 原始事件 | 展示步骤 | 归并规则 |
| --- | --- | --- |
| `route_event` | 识别请求 | 同一 run 只保留一个路由步骤，后到事件更新前一事件 |
| `plan/planning` | 制定排查计划 | 原计划和 `replan` 分版本展示，不把 todos 猜成已完成 |
| `model_decision` | 执行步骤起点 | `payload.step` 优先；缺失时按到达顺序生成执行轮次 |
| `delegate_*`、`tool_event`、日志预处理事件 | 当前执行步骤的活动 | 按 `payload.step`，否则挂到最近的执行轮次 |
| `verify in_progress` + `verify terminal` | 证据自检 | 同一轮验证合并为一个步骤，终态覆盖运行态 |
| `re_evidence` | 补充取证 | 独立异常/重试步骤，后续工具活动归入该步骤 |
| `report` + `complete` | 生成结论 | 合并为一个步骤，完整报告放在折叠详情，不再额外渲染大段重复报告卡 |
| `start/context/budget/no_progress/timeout/fallback/clarify` | 元信息或异常步骤 | 普通生命周期折叠；会改变用户判断的异常保留为显式步骤 |

### 2.3 状态规则

统一展示状态：

```ts
export type ProcessStatus =
  | "waiting"
  | "running"
  | "success"
  | "warning"
  | "error"
  | "skipped";
```

归并优先级固定为 `error > warning > running > success > waiting`。`run.status === "completed"` 只能使“生成结论”进入成功态，不能批量覆盖前序步骤的真实状态。

计划 todos 只展示“计划内容”，不再展示推断的完成/进行中状态。实际进度来自业务步骤本身。

### 2.4 详情内容

每个步骤最多包含以下区块：

```ts
export type ProcessDetailSection = {
  id: string;
  kind: "input" | "result" | "evidence" | "technical";
  title: "执行内容" | "关键结果" | "证据" | "技术信息";
  summary?: string;
  fields: Array<{ label: string; value: string }>;
  items: string[];
  raw?: string;
};
```

- 业务字段先中文化，空值不展示。
- `result` 只展示对判断有用的前 6 条；超出数量显示“另有 N 条”，不直接制造大量标签。
- `context_read`、`delegate_to_expert` 等已支持的结构化结果继续使用专用 presenter。
- 无法识别的对象才进入“原始数据”，并放在二级折叠的“技术信息”中。
- `trace_id`、`span_id`、`evidence_id`、token usage 不与业务结果混排。

### 2.5 展开逻辑

- 运行中的当前步骤默认展开。
- `warning/error` 步骤默认展开。
- 已完成步骤默认折叠，用户可独立展开多个步骤。
- SSE 更新不能强制关闭用户已经展开的步骤。
- 新的活动到达当前步骤时，不自动滚动整个面板；仅更新该步骤内容。

### 2.6 视觉方向

沿用现有石墨蓝/青色 token，不生成新的视觉主题。减少嵌套边框：

- 本轮摘要改为紧凑信息带，不使用 6 个小卡片。
- 时间线是开放列表，步骤之间使用分隔线和状态轨道。
- 详情区使用无外层卡片的分组行，避免“卡片套卡片”。
- 图标继续使用 `lucide-react`；展开控制使用 `ChevronDown`，技术信息使用 `Braces`，证据使用 `FileSearch`。

## 3. 范围与非目标

### In Scope

- 新增过程栏纯函数投影模型。
- 归并路由、计划、执行轮次、专家、工具、验证、补取证、报告和异常事件。
- 修正步骤数、状态、耗时与计数口径。
- 重构步骤一级信息与详情分层。
- 保留 checkpoint、蒸馏、反馈能力。
- 桌面、1280px 下沉布局和 760px 单列布局的视觉/交互回归。
- 功能开关和旧展示回退。

### Out of Scope

- 不修改后端 SSE `type` 枚举或现有字段语义。
- 不修改 Harness 执行策略、工具选择、replan、re-evidence 或 checkpoint 行为。
- 不新增自动处置能力。
- 不调整过程栏拖拽宽度的既有逻辑。
- 不新增埋点、云端用户偏好同步或新的前端依赖。
- 不在本次删除旧展示；稳定一版后再单独清理。

## 4. 文件结构

### 新增

- `frontend/src/components/agent-process/processModel.ts`
  - 定义 `ProcessPanelModel`、`ProcessStep`、状态归并和事件分组。
- `frontend/src/components/agent-process/processContent.ts`
  - 负责中文标签、参数、工具结果、证据和技术字段的 presenter。
- `frontend/src/components/agent-process/ProcessOverview.tsx`
  - 渲染本轮摘要信息带。
- `frontend/src/components/agent-process/ProcessTimeline.tsx`
  - 渲染业务步骤、折叠详情和状态轨道。
- `frontend/src/components/agent-process/LegacyProcessTimeline.tsx`
  - 承接当前步骤构建与展示，用于一版回退；只移动，不改变行为。
- `frontend/src/components/agent-process/__tests__/processModel.test.ts`
  - 覆盖归并、状态、计数、异常和重复事件。
- `frontend/src/components/agent-process/__tests__/processContent.test.ts`
  - 覆盖结构化详情、截断和技术信息隔离。
- `frontend/src/vite-env.d.ts`
  - 声明 `VITE_AGENT_PROCESS_PANEL_V2`。

### 修改

- `frontend/src/components/AgentProcessPanel.tsx`
  - 仅保留面板编排、checkpoint、蒸馏、反馈和 V1/V2 切换。
- `frontend/src/components/__tests__/AgentProcessPanel.test.tsx`
  - 增加用户可见结构、展开行为和旧版回退测试。
- `frontend/src/types/events.ts`
  - 补齐后端已存在但前端尚未声明的可选 `started_at`；收紧常用 payload 读取辅助类型，不改变网络协议。
- `frontend/src/styles.css`
  - 新增摘要信息带、步骤轨道、详情分区和响应式规则；保留旧类名直到开关退场。
- `AGENTS.md`
  - 挂载本计划并在实施完成后更新状态。

## 5. 开关一览

```text
VITE_AGENT_PROCESS_PANEL_V2=1  # 默认：启用新过程栏
VITE_AGENT_PROCESS_PANEL_V2=0  # 回退：使用旧事件卡展示
```

实现时使用“默认启用、显式关闭”的策略：

```ts
export const processPanelV2Enabled =
  import.meta.env.VITE_AGENT_PROCESS_PANEL_V2 !== "0";
```

这是本计划需要用户批准的默认策略。开关仅控制展示投影，不改变事件采集和后端执行。

## 6. 实施步骤

### Task 1: 锁定事件到步骤的投影契约

**Files:**
- Create: `frontend/src/components/agent-process/processModel.ts`
- Create: `frontend/src/components/agent-process/__tests__/processModel.test.ts`
- Modify: `frontend/src/types/events.ts`

- [x] **Step 1: 写失败测试，证明“一条事件不等于一步”**

```ts
it("merges lifecycle events into business steps", () => {
  const model = buildProcessPanelModel(makeRun([
    event("route_event", { route: "diagnosis", status: "completed" }),
    event("agent_event", { stage: "start", status: "in_progress" }),
    event("agent_event", { stage: "plan", status: "completed", payload: { todos: ["查指标"] } }),
    event("agent_event", { stage: "model_decision", status: "in_progress", payload: { step: 1 } }),
    event("tool_event", { tool: "query_metrics", status: "completed", evidence_id: "call-1" }),
    event("agent_event", { stage: "verify", status: "in_progress" }),
    event("agent_event", { stage: "verify", status: "completed", payload: { evidence_count: 1 } }),
    event("agent_event", { stage: "report", status: "in_progress" }),
    event("agent_event", { stage: "complete", status: "completed" }),
  ]));

  expect(model.steps.map((step) => step.phase)).toEqual([
    "route",
    "plan",
    "execute",
    "verify",
    "report",
  ]);
  expect(model.steps).toHaveLength(5);
});
```

- [x] **Step 2: 写失败测试，锁定状态不会被 run 完成态覆盖**

```ts
it("preserves a failed execution step after the run closes", () => {
  const run = makeRun([
    event("agent_event", { stage: "model_decision", status: "in_progress", payload: { step: 1 } }),
    event("tool_event", { tool: "query_logs", status: "failed", evidence_id: "call-1" }),
    event("agent_event", { stage: "complete", status: "completed" }),
  ], "completed");

  const execute = buildProcessPanelModel(run).steps.find((step) => step.phase === "execute");
  expect(execute?.status).toBe("error");
});
```

- [x] **Step 3: 运行测试确认失败**

Run: `cd frontend && pnpm test -- processModel.test.ts`

Expected: FAIL，模块或 `buildProcessPanelModel` 尚不存在。

- [x] **Step 4: 实现模型和确定性归并器**

核心导出固定为：

```ts
export type ProcessPhase =
  | "route"
  | "plan"
  | "execute"
  | "verify"
  | "retry"
  | "report"
  | "exception";

export type ProcessStep = {
  id: string;
  order: number;
  phase: ProcessPhase;
  status: ProcessStatus;
  title: string;
  summary: string;
  durationMs?: number;
  activities: ProcessActivity[];
  details: ProcessDetailSection[];
  sourceEventIndexes: number[];
};

export type ProcessPanelModel = {
  routeLabel: string;
  status: ProcessStatus;
  currentStepId?: string;
  elapsedMs?: number;
  counts: { tools: number; experts: number; evidence: number };
  steps: ProcessStep[];
};

export function buildProcessPanelModel(run: AgentRun): ProcessPanelModel;
```

归并器必须使用显式 reducer，禁止在 JSX 中再次解析 `event.stage`。工具计数按唯一 `evidence_id || span_id || event index` 去重；证据计数只统计成功的工具事件。

- [x] **Step 5: 补齐重复事件、replan、re-evidence、clarify、timeout 测试并通过**

Run: `cd frontend && pnpm test -- processModel.test.ts`

Expected: PASS。

- [ ] **Step 6: 提交该切片**（阻塞：当前 `.git` 指向已丢失的 worktree 元数据）

```bash
git add frontend/src/types/events.ts frontend/src/components/agent-process/processModel.ts frontend/src/components/agent-process/__tests__/processModel.test.ts
git commit -m "feat(frontend): project agent events into process steps"
```

### Task 2: 建立详情 presenter，隔离业务信息与技术信息

**Files:**
- Create: `frontend/src/components/agent-process/processContent.ts`
- Create: `frontend/src/components/agent-process/__tests__/processContent.test.ts`
- Modify: `frontend/src/components/agent-process/processModel.ts`

- [x] **Step 1: 写失败测试**

```ts
it("keeps technical ids out of the business result section", () => {
  const sections = presentEventDetails({
    type: "tool_event",
    tool: "delegate_to_expert",
    status: "completed",
    evidence_id: "call-9",
    trace_id: "trace-1",
    span_id: "tool:call-9",
    payload: {
      arguments: { expert: "metric", subtask: "核对 CPU 告警" },
      result: { expert: "metric", status: "completed", answer: "当前无 firing 告警" },
    },
  });

  expect(section(sections, "result").summary).toContain("当前无 firing 告警");
  expect(section(sections, "result").fields).not.toContainEqual(expect.objectContaining({ label: "trace_id" }));
  expect(section(sections, "technical").fields).toContainEqual({ label: "Trace ID", value: "trace-1" });
});
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test -- processContent.test.ts`

Expected: FAIL，presenter 尚不存在。

- [x] **Step 3: 移动并收敛现有中文化/结果摘要逻辑**

`processContent.ts` 只导出稳定接口：

```ts
export function presentEventTitle(event: TimelineEvent): string;
export function presentEventSummary(event: TimelineEvent): string;
export function presentEventDetails(event: TimelineEvent): ProcessDetailSection[];
export function presentRoute(route?: string): string;
export function presentStatus(status?: string): string;
```

数组统一通过以下规则限制：

```ts
export function limitItems(items: string[], limit = 6) {
  const visible = items.slice(0, limit);
  return {
    visible,
    remaining: Math.max(0, items.length - visible.length),
  };
}
```

- [x] **Step 4: 保留 context_read / delegate_to_expert 专用展示并通过测试**

Run: `cd frontend && pnpm test -- processContent.test.ts AgentProcessPanel.test.tsx`

Expected: PASS；页面中不出现 `"observed_facts"`、`"tool_summaries"` 等整段原始 JSON。

- [ ] **Step 5: 提交该切片**（阻塞：当前 `.git` 指向已丢失的 worktree 元数据）

```bash
git add frontend/src/components/agent-process/processContent.ts frontend/src/components/agent-process/processModel.ts frontend/src/components/agent-process/__tests__/processContent.test.ts
git commit -m "feat(frontend): structure process step details"
```

### Task 3: 实现摘要信息带与可展开业务步骤

**Files:**
- Create: `frontend/src/components/agent-process/ProcessOverview.tsx`
- Create: `frontend/src/components/agent-process/ProcessTimeline.tsx`
- Modify: `frontend/src/components/__tests__/AgentProcessPanel.test.tsx`

- [x] **Step 1: 写失败的用户行为测试**

```tsx
it("shows one-line steps and expands the active step details", async () => {
  const user = userEvent.setup();
  render(<AgentProcessPanel run={runWithCompletedAndRunningSteps()} />);

  expect(screen.getByText("查询指标与告警")).toBeVisible();
  expect(screen.getByText("当前无 firing 告警")).toBeVisible();
  expect(screen.queryByText("Trace ID")).not.toBeVisible();

  await user.click(screen.getByRole("button", { name: /查看.*技术信息/ }));
  expect(screen.getByText("Trace ID")).toBeVisible();
});
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test -- AgentProcessPanel.test.tsx`

Expected: FAIL，新结构尚未渲染。

- [x] **Step 3: 实现组件**

`ProcessTimeline` 接口固定为：

```tsx
type ProcessTimelineProps = {
  steps: ProcessStep[];
  currentStepId?: string;
};

export function ProcessTimeline({ steps, currentStepId }: ProcessTimelineProps) {
  return (
    <ol className="process-steps" aria-label="本轮执行步骤">
      {steps.map((step) => (
        <ProcessStepRow
          key={step.id}
          step={step}
          defaultExpanded={step.id === currentStepId || step.status === "warning" || step.status === "error"}
        />
      ))}
    </ol>
  );
}
```

展开按钮必须提供 `aria-expanded`、`aria-controls`；状态不能只靠颜色表达，必须显示中文状态文本。

- [x] **Step 4: 验证展开状态不会被 SSE 更新重置**

使用 `rerender()` 给同一 `step.id` 增加 activity，断言用户手动展开的详情仍保持展开。

- [ ] **Step 5: 提交该切片**（阻塞：当前 `.git` 指向已丢失的 worktree 元数据）

```bash
git add frontend/src/components/agent-process/ProcessOverview.tsx frontend/src/components/agent-process/ProcessTimeline.tsx frontend/src/components/__tests__/AgentProcessPanel.test.tsx
git commit -m "feat(frontend): render progressive agent process details"
```

### Task 4: 集成 V2 并保留一版回退

**Files:**
- Create: `frontend/src/components/agent-process/LegacyProcessTimeline.tsx`
- Create: `frontend/src/vite-env.d.ts`
- Modify: `frontend/src/components/AgentProcessPanel.tsx`
- Modify: `frontend/src/components/__tests__/AgentProcessPanel.test.tsx`

- [x] **Step 1: 保留现有 Legacy 时间线视图**（实施偏差：为降低无 Git 保护时的大文件搬移风险，旧版渲染暂留在 `AgentProcessPanel.tsx` 内）

移动前后使用现有测试锁定 `context_read`、`delegate_to_expert` 和旧计划展示，禁止顺手改变旧版行为。

- [x] **Step 2: 在面板入口接入功能开关**

```tsx
const processPanelV2Enabled = import.meta.env.VITE_AGENT_PROCESS_PANEL_V2 !== "0";

const model = useMemo(() => buildProcessPanelModel(run), [run]);

return processPanelV2Enabled ? (
  <>
    <ProcessOverview model={model} />
    <ProcessTimeline steps={model.steps} currentStepId={model.currentStepId} />
  </>
) : (
  <LegacyProcessTimeline run={run} />
);
```

checkpoint、DistillCard、FeedbackCard 与 error card 保持在开关外，两个版本共用。

- [x] **Step 3: 删除 V2 中重复的独立报告卡**

最终报告只进入 `report` 步骤的折叠详情；聊天区仍是完整报告的主阅读面。

- [x] **Step 4: 增加 V1 回退测试**

通过 `vi.stubEnv("VITE_AGENT_PROCESS_PANEL_V2", "0")` 断言旧的“本轮概览 / 按步骤执行”仍可渲染。

- [x] **Step 5: 运行组件测试**；提交阻塞同上

Run: `cd frontend && pnpm test -- AgentProcessPanel.test.tsx App.test.tsx`

Expected: PASS。

```bash
git add frontend/src/components/AgentProcessPanel.tsx frontend/src/components/agent-process/LegacyProcessTimeline.tsx frontend/src/vite-env.d.ts frontend/src/components/__tests__/AgentProcessPanel.test.tsx
git commit -m "feat(frontend): enable grouped process panel with rollback"
```

### Task 5: 调整视觉层级与响应式行为

**Files:**
- Modify: `frontend/src/styles.css`

- [x] **Step 1: 添加 V2 样式，不复用含混的旧 `.timeline` 选择器**

新类名统一使用：

```css
.process-summary {}
.process-summary__primary {}
.process-summary__metrics {}
.process-steps {}
.process-step-row {}
.process-step-row__rail {}
.process-step-row__header {}
.process-step-row__summary {}
.process-step-details {}
.process-detail-section {}
.process-technical-details {}
```

- [x] **Step 2: 实现密度规则**

- compact：摘要指标折成两行，步骤标题和状态分行，技术字段单列。
- standard：标题/状态/耗时同一行，详情键值双列。
- comfortable：不放大标题到面板级 hero 字号，只增加行高和信息列间距。

- [x] **Step 3: 实现状态与可访问性样式**

必须覆盖 `:hover`、`:focus-visible`、`[aria-expanded="true"]`、`prefers-reduced-motion`。`warning/error` 使用现有语义 token，不新增单色主题。

- [x] **Step 4: 运行 build 检查**

Run: `cd frontend && pnpm build`

Expected: TypeScript 与 Vite build 成功，无 CSS 解析错误。

- [ ] **Step 5: 提交该切片**（阻塞：当前 `.git` 指向已丢失的 worktree 元数据）

```bash
git add frontend/src/styles.css
git commit -m "style(frontend): refine agent process information hierarchy"
```

### Task 6: 全量回归与浏览器验收

**Files:**
- Modify: `plan/2026-07-15-agent-process-panel-information-architecture.md`
- Modify: `AGENTS.md`

- [x] **Step 1: 运行定向测试**

Run:

```bash
cd frontend
pnpm test -- processModel.test.ts processContent.test.ts AgentProcessPanel.test.tsx App.test.tsx agentStream.test.ts
```

Expected: 全部 PASS。

- [x] **Step 2: 运行前端全量验证**

Run:

```bash
cd frontend
pnpm test
pnpm build
```

Expected: 全部 PASS。

- [x] **Step 3: 启动真实页面**

Run: `cd frontend && pnpm dev`

目标流程：`登录 -> 发起一次诊断 -> SSE 持续到达 -> 当前步骤更新 -> 展开完成/失败步骤 -> 查看技术信息 -> 完成报告`。

- [x] **Step 4: 使用浏览器验证以下视口**

| 视口 | 验收重点 |
| --- | --- |
| 1440×900 | 拖拽宽度、摘要单行、步骤详情展开 |
| 1280×800 | 过程栏下沉后无重叠、无水平滚动 |
| 760×900 | 单列结构、按钮文本和状态不截断 |

Browser 插件当前会话未提供；实施时若仍不可用，使用仓库 Playwright/CLI，并记录 fallback 原因。

- [x] **Step 5: 场景验收**

至少覆盖：

1. 正常成功：route -> plan -> tool -> verify -> report。
2. 工具失败后 replan。
3. 证据不足后 re-evidence。
4. clarify 等待用户输入。
5. checkpoint resume / conservative close。
6. `VITE_AGENT_PROCESS_PANEL_V2=0` 旧版回退。

- [x] **Step 6: 更新进度和索引**

在本文件末尾追加实际命令、结果、截图路径与偏差；将 `AGENTS.md` 中本计划状态改为“已合入”或保留具体阻塞原因。

## 7. 验收标准

| 编号 | 标准 |
| --- | --- |
| AC-1 | 过程步数等于业务步骤数，不等于原始事件数 |
| AC-2 | 同一验证阶段的 running/completed 事件只显示一个步骤 |
| AC-3 | run 完成后，失败/警告步骤仍保留真实状态 |
| AC-4 | 计划 todos 不再通过模糊文本匹配伪造完成状态 |
| AC-5 | 未展开时，每步仍能看到名称、状态、耗时和一句关键结果 |
| AC-6 | 业务详情与 trace/span/evidence/usage 技术信息分层 |
| AC-7 | context_read、delegate_to_expert 不展示整段原始 JSON |
| AC-8 | 当前步骤与异常步骤默认展开，用户展开状态不被 SSE 更新重置 |
| AC-9 | 完整报告不在聊天区和过程栏同时默认展开 |
| AC-10 | checkpoint、蒸馏、反馈、拖拽宽度与多回合过程选择无回归 |
| AC-11 | 1440/1280/760 三档无重叠、横向溢出和不可读截断 |
| AC-12 | `VITE_AGENT_PROCESS_PANEL_V2=0` 可回退旧展示 |

## 8. 风险与回滚

- **R1：没有统一 step id。** 使用 `payload.step` 优先、事件顺序兜底；模型测试覆盖缺失 step 的工具事件。无法确定归属时进入单独的“系统活动”步骤，不静默丢弃。
- **R2：并行委派产生嵌套事件。** 按 parent tool call / evidence id 去重；专家子事件作为 activity，不平铺成多个顶层步骤。
- **R3：旧事件来源字段不稳定。** presenter 对未知字段降级到技术信息 raw 区，不让 JSON 污染一级摘要。
- **R4：SSE 更新导致折叠状态闪烁。** `step.id` 必须由 route/phase/step/evidence correlation 构成，不使用数组 index 作为唯一身份。
- **R5：功能开关使代码短期并存。** 旧组件只保留一版；V2 稳定且回归通过后另开清理计划。
- **R6：当前工作树 Git 元数据失效。** 当前 `.git` 指向不存在的 worktree 管理目录，`git status`/commit 在实施前必须由仓库维护者修复；修复前可以运行测试，但不能执行本计划中的提交步骤。

回滚方式：设置 `VITE_AGENT_PROCESS_PANEL_V2=0` 并重新构建前端。该回滚只影响展示，不影响 SSE、AgentRun 状态和后端执行。

## 9. 计划自检

- Spec coverage：显示逻辑、步骤信息、详细内容、响应式、回退和测试均有对应任务。
- Placeholder scan：无 TBD/TODO；第一版不依赖后端新增字段。
- Type consistency：`ProcessStatus`、`ProcessStep`、`ProcessDetailSection` 和 `ProcessPanelModel` 在 Task 1 定义，后续组件只消费这些类型。
- Scope control：不修改 Harness 行为，不新增依赖，不删除旧版回退。

## 10. 状态

- 状态：**已实现并完成前端验证，待仓库 Git 元数据恢复后提交**
- 默认行为：`VITE_AGENT_PROCESS_PANEL_V2` 默认启用，值为 `0` 时回退旧版。
- Git 阻塞：当前 `.git` 文件指向不存在的 worktree 管理目录，因此无法生成可信的 `git diff` 或提交；未尝试重建、重连或覆盖仓库元数据。

## 11. 实施结果（2026-07-15）

- 新增 `processModel.ts`：将 route / plan / tool / verify / re-evidence / report 等原始事件归并为稳定业务步骤，保留失败与警告状态，并按调用 ID 去重专家委派。
- 新增 `processContent.ts`：统一中文标题、字段与工具结果摘要；业务详情和 evidence / trace / span / usage 技术信息分层展示。
- 新增 `ProcessOverview.tsx`、`ProcessTimeline.tsx`：提供本轮摘要、开放式时间线、稳定展开状态和二级技术信息折叠。
- `AgentProcessPanel.tsx` 默认接入 V2，并保留 `VITE_AGENT_PROCESS_PANEL_V2=0` 回退；旧版实现暂留原文件内，避免在 Git 保护失效时进行无收益的大文件移动。
- 响应式补充：宽屏保留可拖拽过程栏；`<=1280px` 下过程栏全宽下沉，摘要与执行链路并排；`<=760px` 下单列堆叠。

验证结果：

```text
pnpm test                         12 files / 70 tests passed
pnpm exec tsc --noEmit --pretty false  passed
pnpm build                        passed
```

浏览器 QA 使用本机 Chrome + Playwright（本会话未提供 Browser 插件），以模拟 SSE 完整诊断链路验证：

- `1440x900`：520px 过程栏、步骤详情与技术信息展开正常。
- `1280x800`：过程栏全宽下沉，摘要/链路并排，无页面或面板横向溢出。
- `760x900`：过程栏全宽单列，无横向溢出或文本截断。
- 控制台无 error / warning；Trace / Span 仅在展开技术信息后出现。
