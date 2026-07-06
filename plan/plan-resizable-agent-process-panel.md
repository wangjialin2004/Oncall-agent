# Plan：智能体过程栏可拖拽调整宽度 + 文字自适应

- 文档版本：v1.0
- 日期：2026-06-25
- 状态：待评审 / 未进入实现阶段（受 CLAUDE.md 约束）
- 关联 PRD：[`../prd/prd-resizable-agent-process-panel.md`](../prd/prd-resizable-agent-process-panel.md)

## 1. 实施阶段概览

| 阶段 | 目标 | 涉及范围 | 前置 |
| --- | --- | --- | --- |
| P0 评审与决策 | 确认 PRD 中 5 个待确认项 | PRD §9 | — |
| P1 设计与 API 草稿 | 输出组件接口、CSS 变量、数据流图 | 新增组件 + CSS 变量 | P0 |
| P2 实现 | 写代码 + 单测 | `App.tsx`、`AgentProcessPanel.tsx`、`styles.css`、新增 `ResizableSplitter.tsx`、`useResizableWidth.ts` 等 | P1 |
| P3 联调与可访问性 | 键盘、屏幕阅读器、响应式 | 同上 | P2 |
| P4 回归与发布 | 既有测试 + 视觉回归 + 灰度 | 全前端 | P3 |

### P0 决策结果（2026-06-25 用户拍板）

- 实现方式：自实现
- 默认宽度：`clamp(320px, 24vw, 480px)`，首次进入页面计算
- 持久化：`localStorage.superBizAgent.processPanel.width`
- 重置入口：双击 handle
- Tooltip：`title="拖动调整过程栏宽度，双击重置"`

## 2. 关键任务拆解

### P0 评审与决策（建议 0.5d）

- 与用户确认：是否引入第三方库、默认宽度策略、是否提供"重置"按钮、是否需要云端同步。
- 与设计确认：handle 视觉（颜色/宽度/hover/focus 状态）、三档字号对应 token 名称、是否引入新 spacing token。
- 与后端确认：是否需要在 `users` 表新增 `preferences.process_panel_width` 字段（默认否）。

### P1 设计与 API 草稿（建议 0.5d）

产出物（不进代码库，仅评审）：

- 数据流：
  - 单一可变数据源 = CSS 变量 `--process-panel-width`（驱动 grid 列宽与 ResizeObserver 入口）。
  - React state `width` = 持久化层，仅在 `pointerup` / `keyup` 提交，避免拖动过程触发重渲染。
  - `localStorage` 键 `superBizAgent.processPanel.width`（仅在拖拽结束 / 键盘提交时写入）。
- 组件接口草稿：
  - `<ResizableSplitter orientation="vertical" targetSelector=".process-panel" min={320} max={...} />`
  - `useResizableWidth({ storageKey, defaultWidth, min, max })` 返回 `{ width, setWidth, bind }`。
- 字号档映射：
  - `data-density="compact"`：`--fs-13/14` 不变，`line-height: var(--lh-base)`。
  - `data-density="standard"`：默认。
  - `data-density="comfortable"`：`--fs-13 → 14`，`--fs-14 → 15`，`line-height` 提升一档。

### P2 实现（建议 1.5–2d）

涉及文件（不限于）：

- `frontend/src/App.tsx`：在 `Sidebar` / `Workspace` / `ProcessPanel` 三栏之间插入 `<ResizableSplitter />`。
- `frontend/src/components/AgentProcessPanel.tsx`：面板根部读取 `data-density` 属性，不改既有展示。
- `frontend/src/styles.css`：
  - `.app-shell` 第三列由 `clamp(268px, 24vw, 420px)` 改为 `minmax(var(--process-panel-min, 320px), var(--process-panel-width, 360px))`，并保留上限封顶。
  - 新增 `.resize-handle`（hover/focus/drag 视觉）。
  - 新增 `[data-density="compact|standard|comfortable"]` 选择器。
  - `@media (max-width: 1280px)` 与 `@media (max-width: 760px)` 中将 `.resize-handle` 设为 `display: none`。
- 新增 `frontend/src/components/ResizableSplitter.tsx`：拖拽 + 键盘事件处理。
- 新增 `frontend/src/hooks/useResizableWidth.ts`：封装读取、写入、clamp、持久化。
- 新增单元测试：`frontend/src/components/__tests__/ResizableSplitter.test.tsx`、`useResizableWidth.test.ts`。

### P3 联调与可访问性（建议 0.5d）

- 键盘：Tab 聚焦 handle，箭头键 8px 步进、Shift+箭头 32px 步进、Home/End 跳极值、Space/Enter 进入"键盘拖拽模式"。
- 屏幕阅读器：handle `role="separator"`、`aria-orientation="vertical"`、`aria-valuenow/min/max`、`aria-label`。
- 响应式：1280px / 760px 断点下验证 handle 不出现、grid 行为与现状一致。
- 与现有焦点路径共存：消息气泡的 `Enter/Space` 触发选中、停止按钮等不受影响。

### P4 回归与发布（建议 0.5d）

- 既有测试 `pnpm test` 全部通过（特别是 `App.test.tsx`、`agentStream.test.ts`、`conversationHistory.test.tsx`）。
- 视觉回归（若有 Storybook/Chromatic）：覆盖 3 档密度 × 3 个宽度区间。
- 灰度：先在 `feature/resizable-process-panel` 分支提 PR，Code Review 后合入。

## 3. 依赖与前置条件

- 内部：
  - 既有 `App.tsx` 三栏 grid 结构（已知）。
  - 既有 CSS 变量体系（`--fs-12/13/14`、`--lh-base/loose`、`--radius-md/lg`）。
- 外部：
  - 不引入新依赖（如 §P0 决策为引入 `react-resizable-panels`，需 `pnpm add`，并更新 lockfile 与 README）。
- 工具：
  - `pnpm test` 可执行。
  - Playwright 可用（用于 P3 端到端验证；非必需）。

## 4. 验证方式

- 单元测试：覆盖 `useResizableWidth` 的 clamp、localStorage 读写降级、边界值。
- 组件测试：覆盖 ResizableSplitter 鼠标事件、键盘事件、`aria-*` 属性。
- 端到端：
  - AC-1/AC-3/AC-8：拖动 → 刷新 → 视口缩放。
  - AC-6：1280px 与 760px 断点 handle 不可见。
  - AC-7：键盘导航通过 axe-core 扫描。
- 视觉：3 档密度截图对比；与设计基线对照。
- 性能：拖动过程 FPS ≥ 50（Chrome DevTools Performance 面板抽样）。

## 5. 回滚与降级方案

- 代码回滚：合并后通过 Revert PR 即可，所有改动集中在新增文件 + `App.tsx` / `styles.css` 三处，diff 可控。
- 功能降级开关：通过环境变量 `VITE_DISABLE_RESIZABLE_PANEL=1` 或运行时 feature flag，让 `App.tsx` 渲染不带 handle 的旧版三栏。
- 数据回滚：`localStorage` 旧值不影响旧版 UI；如引入云端同步，需要在迁移脚本中保留旧字段。

## 6. 风险点与阻塞项

- 阻塞 B1：PRD §9 待确认项未决策前不进入 P2。
- 风险 R1：CSS 变量与 React state 数据源不一致——按 P1 设计明确"CSS 变量为实时驱动、state 仅在提交时更新"。
- 风险 R2：`localStorage` 不可用（隐私模式）—— `useResizableWidth` 内 try/catch 降级为仅内存。
- 风险 R3：handle 与 ChatWorkspace 内 `ReactMarkdown` 渲染层出现 z-index 冲突——handle `z-index` 设高，pointer-events 仅 handle 自身响应。
- 风险 R4：键盘拖拽模式与 ChatWorkspace 内"消息 Enter/Space 选择"行为冲突——handle 必须 `event.stopPropagation()`，并要求聚焦态时 ChatWorkspace 不响应默认快捷键（通过 `data-focus-on-handle` 属性切换）。
- 风险 R5：极窄视口（如 800px）下即便禁用 handle，若用户偏好值很大，仍可能挤掉中间栏；需在 P2 校验并 clamp。

## 7. 不在本次实施范围内

- 任何对后端 API、AgentRun 流式协议、登录鉴权、基线管理视图的改动。
- 任何对左侧 Sidebar 宽度/可折叠性的改造。
- 任何对响应式断点（1280/900/760）数值的调整。
- 任何对设计 token 体系本身的扩展（如新增 color/spacing token）。

## 8. 待用户授权方可进入实现

按 CLAUDE.md，本 Plan 仅做规划与风险说明。需要在用户明确授权后，才进入 P2 实施阶段。