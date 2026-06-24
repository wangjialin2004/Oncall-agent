# Frontend Paper Evidence Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the React/Vite frontend around the approved C2 paper evidence visual direction while preserving current product behavior.

**Architecture:** Keep the existing React component structure and app state intact. Apply the redesign through corrected visible copy, index metadata/font strategy, and a rewritten token-driven stylesheet that styles the existing shell, chat workspace, process panel, login page, and service baseline manager.

**Tech Stack:** React 18, Vite 8, TypeScript, CSS, lucide-react, Vitest.

---

## File Structure

- Modify: `frontend/index.html`
  - Update page title, theme color, and font loading strategy for the C2 visual system.
- Modify: `frontend/src/components/LoginPage.tsx`
  - Correct visible Chinese copy and keep login behavior unchanged.
- Modify: `frontend/src/components/Sidebar.tsx`
  - Correct sidebar labels, session fallbacks, delete aria labels, and logout text.
- Modify: `frontend/src/components/ChatWorkspace.tsx`
  - Correct chat header, mode selector, empty state, composer labels, and button aria labels.
- Modify: `frontend/src/components/AgentProcessPanel.tsx`
  - Correct status/route/mode/stage labels, timeline details, feedback text, report labels, and error labels.
- Modify: `frontend/src/components/ServiceBaselineManager.tsx`
  - Correct visible service baseline manager copy, validation messages, table headings, empty states, and action labels.
- Modify: `frontend/src/App.tsx`
  - Correct assistant fallback strings and the service baseline side-help copy.
- Replace: `frontend/src/styles.css`
  - Rebuild global tokens and component styling around C2: paper center, graphite rails, blue signal, warm operational status colors, and local-first Chinese typography.

## Baseline

Already verified before implementation:

- `cd frontend && npm run build` passed.
- `cd frontend && npm test` passed with 6 test files and 21 tests.

## Task 1: Correct Metadata And Visible Copy

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/src/components/LoginPage.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/components/ChatWorkspace.tsx`
- Modify: `frontend/src/components/AgentProcessPanel.tsx`
- Modify: `frontend/src/components/ServiceBaselineManager.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Update document metadata**

Replace `frontend/index.html` head metadata with these values while keeping the Vite root and module script:

```html
<meta name="theme-color" content="#fbfaf6" />
<title>智能 OnCall 运维平台</title>
```

Remove Google Fonts preconnect and stylesheet links. The stylesheet will use a local-first font stack.

- [ ] **Step 2: Correct login page copy**

Use these exact strings in `LoginPage.tsx`:

```ts
setError("网络错误，请检查连接后重试");
```

Visible labels:

```text
智能 OnCall 运维平台
Agent Gateway · 智能体运维中枢
用户名
请输入用户名
密码
请输入密码
登录中...
登录
```

- [ ] **Step 3: Correct sidebar copy**

Use these exact strings in `Sidebar.tsx`:

```text
新建会话
服务基线
历史会话
暂无历史记录
未命名会话
{turn_count} 轮
删除会话 {session.title}
退出登录
```

- [ ] **Step 4: Correct chat workspace copy**

Use these exact strings in `ChatWorkspace.tsx`:

```text
运维助手
正在执行智能体推理...
就绪 · 智能 OnCall 运维平台
模式
自动
知识库
运维助手已就绪
描述一个告警事件，或向知识库提问
查看该回合的智能体过程
消息
描述告警事件或提出运维问题...
停止
发送
```

- [ ] **Step 5: Correct agent process panel copy**

Use these label dictionaries in `AgentProcessPanel.tsx`:

```ts
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

Use these visible panel labels and fallbacks:

```text
智能体过程
路由
模式：
时间线
暂无事件
案例
报告
错误
事件已记录
查看调度详情
无
```

- [ ] **Step 6: Correct service baseline manager copy**

Use these exact strings in `ServiceBaselineManager.tsx`:

```text
服务基线管理
刷新
新建服务
加载中...
暂无服务，请先新建。
从左侧选择一个服务以查看/编辑基线。
服务名不能为空
服务名
环境
归属团队
描述
保存中...
创建服务
归属：
未设置
指标
下限
上限
单位
采样窗口
操作
暂无基线，请在下方新增。
删除
上下限必须为数字
下限不能大于上限
新增/更新基线
```

- [ ] **Step 7: Correct App fallback copy**

Use these exact strings in `App.tsx`:

```text
（执行失败）
（已取消）
服务基线
录入每个服务关键指标（CPU/内存/QPS/P95）的正常区间。诊断时会作为“服务知识增强”附在指标/日志结果中，帮助区分噪声与真实异常。
```

- [ ] **Step 8: Verify copy still compiles**

Run:

```bash
cd frontend && npm run build
```

Expected: `✓ built` and exit code 0.

## Task 2: Rebuild C2 Visual Tokens And Base Styles

**Files:**
- Replace: `frontend/src/styles.css`

- [ ] **Step 1: Replace font import and token system**

Remove the Google Fonts `@import`. Use this token foundation:

```css
:root {
  color-scheme: light;
  --paper: #fbfaf6;
  --paper-2: #f4f0e7;
  --paper-3: #eee7db;
  --surface: #fffdf8;
  --surface-muted: #f8f4ec;
  --graphite: #2c3440;
  --graphite-2: #1f2630;
  --graphite-3: #3c4654;
  --ink: #202936;
  --ink-soft: #4c5564;
  --muted: #667085;
  --muted-2: #8b94a3;
  --line: #e5ded2;
  --line-strong: #d7cdbc;
  --signal: #2f6df6;
  --signal-2: #1e57cf;
  --signal-soft: #eaf0ff;
  --signal-line: rgba(47, 109, 246, 0.28);
  --success: #2f9d75;
  --success-soft: #e7f5ee;
  --running: #d98324;
  --running-soft: #fbefd9;
  --risk: #d95f44;
  --risk-soft: #fbe8e2;
  --shadow-sm: 0 1px 2px rgba(44, 52, 64, 0.08);
  --shadow-md: 0 14px 34px rgba(44, 52, 64, 0.12);
  --font-ui: "HarmonyOS Sans SC", "MiSans", "Microsoft YaHei UI", "PingFang SC", "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "JetBrains Mono", "Cascadia Code", "SFMono-Regular", Consolas, monospace;
}
```

- [ ] **Step 2: Add base reset and readable defaults**

Use light body defaults:

```css
html,
body,
#root {
  height: 100%;
  overflow: hidden;
}

body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 14px;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
```

- [ ] **Step 3: Add accessible focus and reduced motion rules**

Use:

```css
:focus-visible {
  outline: 2px solid var(--signal);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
```

## Task 3: Restyle The App Shell, Login, Sidebar, Chat, Process Panel, And Baseline Manager

**Files:**
- Replace: `frontend/src/styles.css`

- [ ] **Step 1: Implement shell and rail layout**

Keep `.app-shell`, `.sidebar`, `.workspace`, and `.process-panel` as the main layout selectors:

```css
.app-shell {
  display: grid;
  grid-template-columns: 248px minmax(420px, 1fr) minmax(390px, 34vw);
  height: 100dvh;
  min-height: 0;
  overflow: hidden;
  background: var(--paper);
}

.sidebar,
.process-panel {
  background: linear-gradient(180deg, var(--graphite) 0%, var(--graphite-2) 100%);
  color: rgba(255, 253, 248, 0.86);
}

.workspace {
  min-width: 0;
  min-height: 0;
  background: linear-gradient(180deg, var(--paper) 0%, var(--paper-2) 100%);
  color: var(--ink);
}
```

- [ ] **Step 2: Implement paper components and form controls**

Style `.login-card`, `.panel-card`, `.baseline-detail`, `.message-bubble`, `.composer input`, `.login-input`, `.login-submit`, and `.icon-button` using paper surfaces, 8px radii, fine borders, and the blue signal for primary actions.

- [ ] **Step 3: Implement the evidence stream**

Style `.agent-panel`, `.timeline`, `.timeline-icon`, `.event-details`, `.status-pill`, `.report-card`, `.feedback-card`, and `.error-card` so the right panel reads as a graphite evidence ledger with compact rows and clear statuses.

- [ ] **Step 4: Implement responsive rules**

Use:

```css
@media (max-width: 1280px) {
  .app-shell {
    grid-template-columns: 220px minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) minmax(260px, 40vh);
  }
  .process-panel {
    grid-column: 1 / -1;
    border-left: 0;
    border-top: 1px solid rgba(255, 253, 248, 0.1);
  }
}

@media (max-width: 760px) {
  .app-shell {
    grid-template-columns: 1fr;
  }
  .sidebar {
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
}
```

- [ ] **Step 5: Verify styling compiles**

Run:

```bash
cd frontend && npm run build
```

Expected: `✓ built` and exit code 0.

## Task 4: Test And Render-Verify The Redesign

**Files:**
- No source edits unless verification exposes a defect.

- [ ] **Step 1: Run frontend tests**

Run:

```bash
cd frontend && npm test
```

Expected: 6 test files and 21 tests pass.

- [ ] **Step 2: Start the Vite dev server**

Run:

```bash
cd frontend && npm run dev
```

Expected: a local Vite URL on port 5173 or a clear alternate port if 5173 is busy.

- [ ] **Step 3: Verify rendered UI**

Flow under test:

```text
app loads -> login screen renders with C2 paper/graphite style -> controls are visible and no framework overlay appears.
```

Use Browser plugin if available. If the Browser runtime cannot be used, use Playwright as a fallback and record the fallback reason.

Check:

```text
desktop 1420x900
mobile 390x844
page title: 智能 OnCall 运维平台
not blank
no Vite/React error overlay
no relevant console errors
no horizontal overflow
login controls visible
```

- [ ] **Step 4: Run final build after any visual fixes**

Run:

```bash
cd frontend && npm run build
```

Expected: `✓ built` and exit code 0.

- [ ] **Step 5: Commit frontend redesign**

Stage only the frontend redesign files and this plan:

```bash
git add docs/superpowers/plans/2026-06-24-frontend-paper-evidence-redesign.md frontend/index.html frontend/src/App.tsx frontend/src/components/LoginPage.tsx frontend/src/components/Sidebar.tsx frontend/src/components/ChatWorkspace.tsx frontend/src/components/AgentProcessPanel.tsx frontend/src/components/ServiceBaselineManager.tsx frontend/src/styles.css
git commit -m "style: redesign frontend paper evidence workspace"
```
