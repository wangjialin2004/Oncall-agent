# Frontend Command Center Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the frontend into a polished OnCall command-center UI while preserving existing behavior and fixing visible Chinese mojibake.

**Architecture:** Keep the current React component structure and API/data flow. Concentrate behavior-neutral copy fixes in the existing components and tests, then drive the visual redesign through `frontend/src/styles.css` tokens and focused component selectors. Avoid backend changes and avoid broad refactors.

**Tech Stack:** React 18, TypeScript, Vite, Vitest, Testing Library, lucide-react, react-markdown, CSS custom properties.

---

## File Structure

- Modify: `frontend/src/components/LoginPage.tsx`
  - Responsibility: login form copy and login error wording.
- Modify: `frontend/src/components/Sidebar.tsx`
  - Responsibility: navigation labels, session empty state, delete/logout labels.
- Modify: `frontend/src/components/ChatWorkspace.tsx`
  - Responsibility: chat header copy, empty state copy, mode labels, composer labels, send/stop labels.
- Modify: `frontend/src/components/AgentProcessPanel.tsx`
  - Responsibility: status/route/mode/agent/stage labels, event detail labels, feedback labels, fallback text.
- Modify: `frontend/src/components/ServiceBaselineManager.tsx`
  - Responsibility: service baseline management copy, validation messages, table/form labels.
- Modify: `frontend/src/App.tsx`
  - Responsibility: assistant stream error/cancel copy and baseline side-help copy.
- Modify: `frontend/src/components/__tests__/App.test.tsx`
  - Responsibility: update assertions and mocked event copy to readable Chinese.
- Modify: `frontend/src/components/__tests__/ChatWorkspace.test.tsx`
  - Responsibility: update Markdown rendering assertions to readable Chinese.
- Modify: `frontend/src/components/__tests__/ServiceBaselineManager.test.tsx`
  - Responsibility: update baseline UI assertions to readable Chinese.
- Modify: `frontend/src/styles.css`
  - Responsibility: command-center design tokens, shell layout, sidebar, chat, evidence stream panel, baseline manager, login page, responsive rules.
- Modify: `frontend/index.html`
  - Responsibility: page title, theme color, font loading if needed.

## Task 1: Make Chinese Copy Testable

**Files:**
- Modify: `frontend/src/components/__tests__/App.test.tsx`
- Modify: `frontend/src/components/__tests__/ChatWorkspace.test.tsx`
- Modify: `frontend/src/components/__tests__/ServiceBaselineManager.test.tsx`

- [ ] **Step 1: Update the mocked event text and assertions in `App.test.tsx`**

Replace mojibake strings with readable Chinese in mocked stream events and assertions:

```tsx
summary: "综合诊断开始",
todos: ["确认目标", "选择工具"],
required_evidence: ["指标曲线"],
required_params: [{ name: "target", prompt: "服务名", reason: "指标查询需要目标" }],
onEvent({ type: "content", data: "诊断结论已确认" });
summary: "已生成调度计划",
answer: "诊断结论已确认",
```

Update the assertions to these exact labels:

```tsx
await user.selectOptions(screen.getByLabelText("模式"), "auto");
await user.type(screen.getByLabelText("消息"), "checkout-api slow");
await user.click(screen.getByRole("button", { name: "发送" }));

expect(await screen.findByText("智能体过程")).toBeInTheDocument();
expect(await screen.findByText("路由分发")).toBeInTheDocument();
expect(await screen.findByText("综合诊断开始")).toBeInTheDocument();
expect(await screen.findByText("已生成调度计划")).toBeInTheDocument();
expect(await screen.findByText("查看调度详情")).toBeInTheDocument();
expect(await screen.findByText("计划步骤")).toBeInTheDocument();
expect(await screen.findByText("确认目标")).toBeInTheDocument();
expect(await screen.findByText("服务名：指标查询需要目标")).toBeInTheDocument();
expect(await screen.findByText("已完成")).toBeInTheDocument();
expect((await screen.findAllByText("诊断结论已确认")).length).toBeGreaterThan(0);
```

For feedback assertions:

```tsx
const adopt = await screen.findByRole("button", { name: "采纳" });
expect(mockSubmitFeedback.mock.calls[0][0]).toMatchObject({
  acceptanceLevel: "strong",
  userMessage: "checkout-api slow",
  assistantAnswer: "诊断结论已确认",
});
expect(await screen.findByText("已采纳，将沉淀为长期经验。")).toBeInTheDocument();
```

- [ ] **Step 2: Update `ChatWorkspace.test.tsx` Markdown assertions**

Use readable Markdown content:

```tsx
content: [
  "## 诊断结论",
  "",
  "| 指标 | 数值 |",
  "| --- | ---: |",
  "| P95 | 280ms |",
  "",
  "`checkout-api` 延迟升高",
].join("\n"),
```

Assert:

```tsx
expect(screen.getByRole("heading", { name: "诊断结论" })).toBeInTheDocument();
expect(screen.getByRole("table")).toBeInTheDocument();
expect(screen.getByText("checkout-api")).toBeInTheDocument();
```

- [ ] **Step 3: Update `ServiceBaselineManager.test.tsx` assertions**

Replace label and error assertions with:

```tsx
expect(await screen.findByText(/归属：payments/)).toBeInTheDocument();

await user.selectOptions(screen.getByLabelText("指标"), "cpu");
await user.type(screen.getByLabelText("下限"), "10");
await user.type(screen.getByLabelText("上限"), "70");
await user.click(screen.getByRole("button", { name: "新增/更新基线" }));
```

For validation:

```tsx
await screen.findByText(/归属：payments/);
await user.type(screen.getByLabelText("下限"), "90");
await user.type(screen.getByLabelText("上限"), "10");
await user.click(screen.getByRole("button", { name: "新增/更新基线" }));

expect(await screen.findByText("下限不能大于上限")).toBeInTheDocument();
```

- [ ] **Step 4: Run tests and confirm they fail for the expected reason**

Run:

```powershell
cd frontend
npm test
```

Expected: tests fail because the components still render mojibake labels.

- [ ] **Step 5: Keep the test update for the copy-fix commit**

Do not commit at this point. Keep the updated test expectations in the working tree so Task 2 can make them pass and commit the tests with the component copy fixes.

## Task 2: Fix Component Copy

**Files:**
- Modify: `frontend/src/components/LoginPage.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/components/ChatWorkspace.tsx`
- Modify: `frontend/src/components/AgentProcessPanel.tsx`
- Modify: `frontend/src/components/ServiceBaselineManager.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Fix `LoginPage.tsx` visible text**

Use these exact strings:

```tsx
setError("网络错误，请检查连接后重试");

<h1>智能 OnCall 运维平台</h1>
<p>Agent Gateway · 智能体运维中枢</p>
<label className="login-label" htmlFor="login-username">用户名</label>
<input placeholder="请输入用户名" />
<label className="login-label" htmlFor="login-password">密码</label>
<input placeholder="请输入密码" />
{loading ? "登录中..." : <>... 登录</>}
```

- [ ] **Step 2: Fix `Sidebar.tsx` visible text**

Use these exact strings:

```tsx
新建会话
服务基线
历史会话
暂无历史记录
未命名会话
{session.turn_count} 轮
删除会话 ${session.title}
退出登录
```

- [ ] **Step 3: Fix `ChatWorkspace.tsx` visible text**

Use these exact strings:

```tsx
<h2>运维助手</h2>
正在执行智能体推理...
智能 OnCall 运维平台
模式
<option value="auto">自动</option>
<option value="rag">知识库</option>
运维助手已就绪
描述一个告警事件，或向知识库提问
消息
描述告警事件或提出运维问题...
停止
发送
查看该回合的智能体过程
```

- [ ] **Step 4: Fix `AgentProcessPanel.tsx` labels**

Use this exact mapping:

```tsx
const statusLabels: Record<string, string> = {
  idle: "待命",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  degraded: "降级",
  error: "错误",
  cancelled: "已取消",
  evidence_insufficient: "证据不足",
  root_cause_ready: "根因已就绪",
};

const routeLabels: Record<string, string> = {
  knowledge: "知识问答",
  metric: "告警/指标",
  log: "日志分析",
  change: "变更/发布",
  diagnosis: "综合诊断",
  clarify: "待澄清",
  unknown: "未知",
  error: "错误",
};

const modeLabels: Record<string, string> = {
  auto: "自动",
  rag: "知识库",
};
```

Use these exact user-facing labels and fallbacks:

```tsx
智能体过程
路由
模式：{labelFor(run.mode, modeLabels)}
时间线
暂无事件
查看调度详情
计划步骤
要求证据
必要参数
自检缺口
置信度
成功证据数
失败证据数
可用工具数
最大步数
历史轮数
Token 估算
耗时 ms
工具参数
默认值
原因
证据缺口
用量
无
事件已记录
案例
报告
错误
反馈
这次诊断有帮助吗？
请填写实际根因
提交纠正
取消
采纳
纠正
已采纳，将沉淀为长期经验。
已记录纠正，将沉淀为长期经验。
```

- [ ] **Step 5: Fix `ServiceBaselineManager.tsx` visible text**

Use these exact labels:

```tsx
服务基线管理
刷新
新建服务
加载中...
暂无服务，请先新建。
从左侧选择一个服务以查看/编辑基线。
服务名
环境
归属团队
描述
创建服务
保存中...
归属：{detail.owner_team || detail.owner_user || "未设置"}
指标
下限
上限
单位
采样窗口
操作
尚无基线，请在下方新增。
删除
新增/更新基线
服务名不能为空
上下限必须为数值
下限不能大于上限
```

- [ ] **Step 6: Fix `App.tsx` fallback and side-help copy**

Use these exact strings:

```tsx
content: item.content === RUNNING_PLACEHOLDER ? "（执行失败）" : item.content
content: item.content === RUNNING_PLACEHOLDER ? "（已取消）" : item.content
<h3>服务基线</h3>
录入每个服务关键指标（CPU/内存/QPS/P95）的正常区间。诊断时会作为“服务知识增强”附加到指标/日志结果中，帮助区分噪声与真异常。
```

- [ ] **Step 7: Run tests and confirm copy fixes pass**

Run:

```powershell
cd frontend
npm test
```

Expected: frontend tests pass, or failures are limited to timing in existing async tests and not missing Chinese labels.

- [ ] **Step 8: Commit readable copy tests and component copy fixes**

```powershell
git add -- frontend/src/components/__tests__/App.test.tsx frontend/src/components/__tests__/ChatWorkspace.test.tsx frontend/src/components/__tests__/ServiceBaselineManager.test.tsx frontend/src/components/LoginPage.tsx frontend/src/components/Sidebar.tsx frontend/src/components/ChatWorkspace.tsx frontend/src/components/AgentProcessPanel.tsx frontend/src/components/ServiceBaselineManager.tsx frontend/src/App.tsx
git commit -m "fix: restore readable frontend Chinese copy"
```

## Task 3: Establish Command-Center Visual Tokens

**Files:**
- Modify: `frontend/src/styles.css`
- Modify: `frontend/index.html`

- [ ] **Step 1: Update `frontend/index.html` metadata and font loading**

Use:

```html
<meta name="theme-color" content="#0b1117" />
<title>智能 OnCall 运维平台</title>
```

Ensure the font import supports Inter and JetBrains Mono either in `index.html` or the CSS import:

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');
```

- [ ] **Step 2: Replace the root token block in `styles.css`**

Keep token names used by existing selectors. Use this palette:

```css
:root {
  color-scheme: dark;
  --bg-void: #05080c;
  --bg-sidebar: #081019;
  --bg-base: #0b1117;
  --bg-surface: #101923;
  --bg-raised: #16212d;
  --bg-high: #202d3b;
  --bg-input: #0d151e;

  --paper: #f4f7fb;
  --paper-2: #e8eef6;
  --ink: #0f1720;

  --border-0: rgba(180, 199, 218, 0.08);
  --border-1: rgba(180, 199, 218, 0.15);
  --border-2: rgba(180, 199, 218, 0.24);
  --border-3: rgba(180, 199, 218, 0.38);

  --text-0: #f3f8fd;
  --text-1: #c3d0dc;
  --text-2: #8495a7;
  --text-3: #566474;

  --brand: #2dd4bf;
  --brand-light: #7dded3;
  --brand-dim: #0f766e;
  --brand-fill: #0f766e;
  --brand-fill-2: #0e7490;
  --brand-glow: rgba(45, 212, 191, 0.18);
  --brand-subtle: rgba(45, 212, 191, 0.10);
  --brand-border: rgba(45, 212, 191, 0.30);

  --s-run: #f59e0b;
  --s-run-sub: rgba(245, 158, 11, 0.12);
  --s-run-border: rgba(245, 158, 11, 0.32);
  --s-ok: #34d399;
  --s-ok-sub: rgba(52, 211, 153, 0.10);
  --s-ok-border: rgba(52, 211, 153, 0.26);
  --s-err: #fb7185;
  --s-err-sub: rgba(251, 113, 133, 0.10);
  --s-err-border: rgba(251, 113, 133, 0.28);
  --s-warn: #fb923c;
  --s-warn-sub: rgba(251, 146, 60, 0.10);
  --s-warn-border: rgba(251, 146, 60, 0.28);

  --font-ui: 'Inter', ui-sans-serif, system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', 'SFMono-Regular', Consolas, monospace;
}
```

- [ ] **Step 3: Search for old purple identity remnants**

Run:

```powershell
Select-String -Path frontend\src\styles.css,frontend\index.html -Pattern '#7c3aed|#a855f7|#a78bfa|#6d28d9|#4f46e5|124,\\s*58,\\s*237'
```

Expected: no matches. If matches appear in active styles, replace them with token-based cyan/teal values.

- [ ] **Step 4: Commit visual token foundation**

```powershell
git add -- frontend/src/styles.css frontend/index.html
git commit -m "style: establish command center design tokens"
```

## Task 4: Redesign Shell, Sidebar, And Chat Workspace

**Files:**
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Update shell sizing**

Use a desktop shell that keeps the evidence panel useful:

```css
.app-shell {
  display: grid;
  grid-template-columns: 244px minmax(420px, 1fr) minmax(380px, 34vw);
  height: 100dvh;
  min-height: 0;
  overflow: hidden;
  background:
    linear-gradient(120deg, rgba(45, 212, 191, 0.06), transparent 28%),
    var(--bg-void);
}
```

- [ ] **Step 2: Restyle sidebar for operations navigation**

Keep `.sidebar-inner`, `.sidebar-action`, `.session-list`, and footer selectors. Apply:

```css
.sidebar {
  background: linear-gradient(180deg, #081019 0%, #060b12 100%);
  border-right: 1px solid var(--border-1);
}

.sidebar-action.active,
.session-item.active {
  background: var(--brand-subtle);
  border-color: var(--brand-border);
  color: var(--brand-light);
}
```

Ensure delete and logout hover colors remain red/pink status colors, not brand colors.

- [ ] **Step 3: Restyle chat workspace**

Make the chat area calm and readable:

```css
.workspace {
  background: linear-gradient(180deg, #0d151e 0%, #0a1017 100%);
}

.chat-header {
  min-height: 68px;
  background: rgba(16, 25, 35, 0.92);
  border-bottom: 1px solid var(--border-1);
}

.messages {
  padding: 22px clamp(18px, 2.2vw, 34px);
  gap: 12px;
}

.message-bubble {
  max-width: min(760px, 86%);
  border-radius: 12px;
}
```

Use dark, low-noise assistant bubbles and teal-filled user bubbles with safe white text contrast.

- [ ] **Step 4: Restyle composer**

Keep the input/button grid, but make the composer feel docked:

```css
.composer {
  padding: 14px clamp(18px, 2.2vw, 34px);
  background: rgba(16, 25, 35, 0.96);
  border-top: 1px solid var(--border-1);
}
```

- [ ] **Step 5: Run build**

Run:

```powershell
cd frontend
npm run build
```

Expected: TypeScript and Vite build succeed.

- [ ] **Step 6: Commit shell/chat styling**

```powershell
git add -- frontend/src/styles.css
git commit -m "style: redesign command center shell and chat"
```

## Task 5: Redesign Agent Process Panel As Evidence Stream

**Files:**
- Modify: `frontend/src/styles.css`
- Optional modify: `frontend/src/components/AgentProcessPanel.tsx` only if class names are needed for clearer styling.

- [ ] **Step 1: Style the panel container**

Use the right panel as the signature element:

```css
.process-panel {
  background: linear-gradient(180deg, #101923 0%, #0b1117 100%);
  border-left: 1px solid var(--border-1);
  overflow-y: auto;
}

.agent-panel {
  padding: 16px;
  gap: 12px;
}
```

- [ ] **Step 2: Make panel cards denser**

Use:

```css
.panel-card {
  border: 1px solid var(--border-1);
  border-radius: 8px;
  padding: 12px;
  background: rgba(22, 33, 45, 0.76);
}
```

- [ ] **Step 3: Make the timeline scan like evidence**

Use:

```css
.timeline > li {
  grid-template-columns: 28px 1fr;
  gap: 10px;
  padding-bottom: 14px;
}

.timeline-icon {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: var(--brand-subtle);
  border-color: var(--brand-border);
}

.event-details {
  border-radius: 7px;
  background: rgba(13, 21, 30, 0.84);
}
```

- [ ] **Step 4: Confirm detail fields do not overflow**

Run the app and open event details. Long `Trace`, `Span`, tool argument, and evidence strings should wrap inside the panel:

```css
.event-detail-fields dd,
.timeline > li > div > p,
.event-detail-block li {
  overflow-wrap: anywhere;
}
```

- [ ] **Step 5: Commit evidence panel styling**

```powershell
git add -- frontend/src/styles.css frontend/src/components/AgentProcessPanel.tsx
git commit -m "style: redesign agent process evidence stream"
```

## Task 6: Redesign Login And Service Baseline Manager

**Files:**
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Restyle login as console entry**

Use the existing `login-root`, `login-card`, and `login-brand` selectors. Keep one centered form. Use:

```css
.login-root {
  background:
    linear-gradient(135deg, rgba(45, 212, 191, 0.11), transparent 32%),
    linear-gradient(180deg, #0b1117 0%, #05080c 100%);
}

.login-card {
  max-width: 420px;
  border-radius: 10px;
  background: rgba(16, 25, 35, 0.92);
  border: 1px solid var(--border-1);
}
```

- [ ] **Step 2: Restyle baseline manager as service reliability panel**

Use:

```css
.baseline-manager {
  padding: 18px clamp(18px, 2vw, 28px);
  gap: 14px;
}

.baseline-body {
  grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
  gap: 14px;
}

.baseline-detail {
  border-radius: 8px;
  background: rgba(16, 25, 35, 0.82);
}

.baseline-table {
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 3: Tighten baseline table density**

Keep numeric columns right-aligned and make action buttons stable:

```css
.baseline-table th:nth-child(2),
.baseline-table th:nth-child(3),
.baseline-table td:nth-child(2),
.baseline-table td:nth-child(3) {
  text-align: right;
}

.baseline-table .icon-button,
.baseline-table td .icon-button {
  width: 32px;
  height: 32px;
}
```

- [ ] **Step 4: Commit login and baseline styling**

```powershell
git add -- frontend/src/styles.css
git commit -m "style: redesign login and service baseline views"
```

## Task 7: Responsive And Final Verification

**Files:**
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Update responsive rules**

Use:

```css
@media (max-width: 1280px) {
  .app-shell {
    grid-template-columns: 220px minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) 42vh;
  }
  .process-panel {
    grid-column: 1 / -1;
    border-left: none;
    border-top: 1px solid var(--border-1);
  }
}

@media (max-width: 760px) {
  .app-shell {
    grid-template-columns: 1fr;
  }
  .sidebar {
    display: none;
  }
  .process-panel {
    display: none;
  }
  .chat-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .mode-select {
    width: 100%;
    justify-content: space-between;
  }
  .message-bubble {
    max-width: 94%;
  }
  .baseline-body {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: Run full frontend verification**

Run:

```powershell
cd frontend
npm test
npm run build
```

Expected: tests pass and build succeeds.

- [ ] **Step 3: Start dev server for visual QA**

Run:

```powershell
cd frontend
npm run dev
```

Open `http://localhost:5173`. If port 5173 is already in use, Vite will show the actual URL; use that URL.

- [ ] **Step 4: Browser-check core screens**

Check these states:

- Login page renders readable Chinese and command-center styling.
- Empty chat page renders readable header, mode selector, empty state, and composer.
- Sending a message renders user and assistant bubbles, and the right evidence panel updates.
- Service baseline page renders list, detail, table, and form without text overlap.
- Widths near 1365px, 1100px, and 760px avoid horizontal overflow.

- [ ] **Step 5: Search for lingering mojibake and old purple identity**

Run:

```powershell
Select-String -Path frontend\src\components\*.tsx,frontend\src\App.tsx -Pattern '锛|鏅|杩|鎻|鐧|妯|娑|鍙|涓|灏|褰|鍔'
Select-String -Path frontend\src\styles.css,frontend\index.html -Pattern '#7c3aed|#a855f7|#a78bfa|#6d28d9|#4f46e5|124,\\s*58,\\s*237'
```

Expected: no active UI copy mojibake remains. Purple matches should be absent from active brand styles.

- [ ] **Step 6: Final commit**

```powershell
git add -- frontend/src/styles.css frontend/index.html frontend/src/App.tsx frontend/src/components/LoginPage.tsx frontend/src/components/Sidebar.tsx frontend/src/components/ChatWorkspace.tsx frontend/src/components/AgentProcessPanel.tsx frontend/src/components/ServiceBaselineManager.tsx frontend/src/components/__tests__/App.test.tsx frontend/src/components/__tests__/ChatWorkspace.test.tsx frontend/src/components/__tests__/ServiceBaselineManager.test.tsx
git commit -m "feat: redesign frontend command center experience"
```

## Self-Review Notes

- Spec coverage: tasks cover visible Chinese copy, login, app shell, sidebar, chat workspace, process panel, service baseline manager, responsive behavior, error presentation, tests, build, and browser checks.
- Type consistency: no new API types or backend contracts are introduced.
- Scope guard: backend files, auth flow, SSE protocol, conversation storage, and new product features remain outside the plan.
