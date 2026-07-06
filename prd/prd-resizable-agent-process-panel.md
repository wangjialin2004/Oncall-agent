# PRD：智能体过程栏可拖拽调整宽度 + 文字自适应

- 文档版本：v1.0
- 日期：2026-06-25
- 作者：Claude Code（产品视角）
- 状态：待评审

## 1. 背景与目标

### 1.1 背景

在当前 OnCall 智能体平台中，"智能体过程"（Agent Process Panel，即用户口中的"右边进程栏"）固定在主界面右侧，其宽度由 `frontend/src/styles.css` 中 `.app-shell` 的 `grid-template-columns: 220px minmax(420px, 1fr) clamp(268px, 24vw, 420px)` 决定，第三列 `clamp(268px, 24vw, 420px)` 在窄屏与宽屏下都不允许用户自由调整。

实际使用中观察到以下问题：

1. 当事件时间线较长（例如 `event.payload.arguments`、`payload.token_estimate`、`payload.usage` 等 JSON 详情展开时），420px 上限会导致内容横向截断或出现不必要的换行。
2. 当用户希望聚焦过程面板、或同时对比消息气泡时，缺少将其进一步拉宽的能力。
3. 面板内部字号固定为设计稿的 `var(--fs-12/13/14)`，当面板被拉宽后，文字密度反而显得稀疏，需要让字号/排版随宽度变化自适应。

### 1.2 目标

- 允许用户通过拖拽调整"智能体过程"面板的宽度，范围受限于合理的最小/最大值。
- 调整结果在用户维度持久化（同一浏览器 + 同一登录态），刷新后保持。
- 面板在窄宽度（接近最小值）下不出现横向滚动；在中宽度下保持当前阅读密度；在宽宽度下文字密度与行高适度放大，避免"拉宽后留白过大"。
- 在 1280px 与 760px 断点下保持现有响应式行为：不出现"水平拖出去后整页布局崩塌"。
- 不影响左侧 Sidebar、中间 ChatWorkspace 的现有行为与状态。

## 2. 用户与业务场景

### 2.1 主要用户

- 运维工程师（SRE / OnCall 工程师），长时间停留在此页面阅读智能体推理过程。
- 平台开发与测试同学，需要在测试环境下复现特定宽度下的 UI 表现。

### 2.2 典型场景

1. **场景 A：阅读长 payload**
   用户在"调度详情"中展开 `event.payload.arguments` 或 `event.usage`，发现宽度不足以在同一行显示完键值对，希望把面板拉到 600–720px 以减少换行。
2. **场景 B：对比消息与过程**
   用户需要在中间 ChatWorkspace 阅读助手结论，同时关注右侧过程面板中关键事件，需要把过程面板收到 320px 左右，为中间留出更多空间。
3. **场景 C：多用户/多设备**
   用户 A 在 1920×1080 显示器上偏好 480px；用户 A 切到 14 寸笔记本后，希望默认值更小（按视口宽度计算），而不是直接套用之前的偏好（待确认，见 §10）。

## 3. 功能范围

### 3.1 In-Scope

- 在 `.process-panel` 与 `.workspace` 之间新增一个可拖拽分隔条（resize handle），支持横向拖动改变过程面板宽度。
- 拖动时限制最小宽度（建议 `320px`）与最大宽度（建议 `min(720px, 视口宽度 - 左侧 Sidebar - 中间 ChatWorkspace 最小宽度 - 安全余量)`）。
- 拖动过程中通过 `pointermove` + `requestAnimationFrame` 实时更新中间变量，避免抖动；松手时提交最终值。
- 宽度偏好持久化：保存到 `localStorage`（键名待定，假设 `superBizAgent.processPanel.width`）。
- 文字自适应：
  - 面板宽度 `< 360px`：使用紧凑字号档（沿用现有 `--fs-12/13`），时间线标题不换行省略。
  - 面板宽度 `360–520px`：使用标准档（沿用现有 `--fs-13/14`）。
  - 面板宽度 `> 520px`：使用宽松档，`line-height` 提升一档，`--fs-13 → --fs-14`、`--fs-14 → --fs-15`，并提高 `.panel-card` 内部 padding（待确认）。
- 键盘可达：分隔条 `tabIndex=0`，支持 `←/→` 调整 8px 步进、`Shift+←/→` 32px 步进、`Home/End` 跳到最大/最小。
- 在 1280px 与 760px 断点下禁用拖拽（响应式自动布局：1280 以下过程面板下沉为第二行，760 以下进一步单列）。
- 在视觉上让分隔条在 hover/focus/drag 时有明显反馈（颜色、宽度变化）。
- 提供"重置为默认"入口（可选，详见 §10 待确认）。

### 3.2 Out-of-Scope（非目标）

- 不改动左侧 Sidebar 与中间 ChatWorkspace 的尺寸逻辑与拖拽行为。
- 不实现垂直方向（高度）的拖拽；高度仍由 grid 决定。
- 不做云端同步；首版仅做本机 `localStorage` 持久化。
- 不调整响应式断点本身的数值（1280 / 900 / 760）。
- 不引入第三方拖拽库（如 `react-resizable-panels`、`allotment`），如需引入需另行评估（见 §10）。
- 不影响后端接口协议、AgentRun 状态结构、TLS / 鉴权。

## 4. 关键流程

### 4.1 拖拽流程

1. 用户鼠标按下分隔条（或键盘聚焦后按 `Space` / `Enter` 进入"拖拽模式"）。
2. 监听 `pointermove`，按视口宽度换算新宽度，clamp 到 `[min, max]`，写入 React state。
3. 拖拽过程中 CSS 变量 `--process-panel-width` 实时驱动 `.process-panel` 的列宽，避免组件重渲染。
4. `pointerup` 时把最终宽度写入 `localStorage` 并同步 React state。
5. 触发一次轻量布局校验：若视口宽度变化导致 `width > max` 重新计算，将当前值 clamp 到新 max。

### 4.2 启动加载流程

1. 页面挂载时读取 `localStorage.processPanel.width`。
2. 若不存在或非法值，使用默认 `clamp(268px, 24vw, 420px)` 中的中位值（如 360px）。
3. 将其写入 CSS 变量与 React state。

### 4.3 字体自适应判定

1. 使用 `ResizeObserver` 监听 `.process-panel` 的 contentRect 宽度。
2. 按 §3.1 的三档规则更新 `data-density="compact|standard|comfortable"`。
3. CSS 端通过属性选择器应用不同字号、行高、padding。

## 5. 验收标准

| 编号 | 验收项 | 判定方式 |
| --- | --- | --- |
| AC-1 | 在 1440px 视口下，可将过程面板从 320px 拖到 720px，松手后保持 | 手动 + 截图 |
| AC-2 | 拖动过程中画面不抖动，无明显掉帧 | 录制，肉眼/FrameTiming |
| AC-3 | 拖动后刷新页面，宽度仍为上次设置值 | 手动 |
| AC-4 | 宽度 < 360px 时无横向滚动条 | 手动 |
| AC-5 | 宽度 > 520px 时行高与字号明显大于宽度 < 360px 时的状态 | 截图对比 |
| AC-6 | 在 1280px 以下视口，拖拽 handle 不显示，过程面板行为与现在一致 | 手动 |
| AC-7 | 分隔条可通过 `Tab` 聚焦，`←/→/Shift+←/→/Home/End` 均可工作 | 键盘测试 |
| AC-8 | 当视口缩放导致当前宽度超过新 max 时，自动 clamp 到新 max，不破版 | 手动 |
| AC-9 | 不影响 Sidebar 折叠、ChatWorkspace 滚动、AgentRun 推送事件流 | 手动 + 既有测试 |
| AC-10 | 已有前端测试（`App.test.tsx`、`agentStream.test.ts` 等）全部通过 | `pnpm test` |

## 6. 指标与成功信号

- 用户可读性：宽度可调后，客服/内部反馈中"过程栏太窄看不全"相关抱怨消失（定性指标）。
- 留存/使用：拖拽 handle 在前两周内的实际使用占比（需埋点；首版可不做）。
- 稳定性：拖拽过程中无报错、无控制台 warning；FPS 保持 ≥ 50。

## 7. 风险与依赖

- 风险 R1：CSS Grid 列宽与 JS 状态同时驱动同一宽度时，可能出现"拖动后突然回弹"。需要明确单一数据源（建议 CSS 变量为实时驱动，state 与 localStorage 为持久化层）。
- 风险 R2：`localStorage` 在隐私模式或被禁用时不可用，需要有降级（不持久化，运行时仍可调）。
- 风险 R3：拖拽 handle 与滚动条可能重叠，需要 z-index 与命中区域设计。
- 风险 R4：极端视口（< 800px）下即便禁用 handle，也需要保证布局不破。
- 风险 R5：键盘交互与既有焦点管理（Enter 触发消息选择、Space 触发按钮等）冲突，需要正确 `event.preventDefault` 与 keyCode 限定。
- 依赖 D1：现有 `App.tsx` 渲染 `Sidebar / ChatWorkspace / AgentProcessPanel` 三栏结构，需新增 handle 节点。
- 依赖 D2：现有 `styles.css` 的 `.app-shell` 第三列需改为可变量驱动（CSS 变量 `--process-panel-width`）。

## 8. 兼容性

- 桌面端 Chrome / Edge / Safari / Firefox 最新两个大版本。
- 不涉及移动端断点（760px 以下时拖拽 handle 隐藏，沿用现有堆叠布局）。

## 9. 决策记录（2026-06-25 用户拍板）

1. **拖拽实现方式**：自实现（约 80–120 行），不引入第三方库。
2. **持久化范围**：仅本机 `localStorage`，键名 `superBizAgent.processPanel.width`。
3. **默认宽度**：按视口宽度取百分比（24vw），clamp 到 `[320, 480]`；与响应式断点逻辑解耦。
4. **"重置为默认"入口**：双击 handle 重置（无视觉额外占位）。
5. **handle tooltip**：使用原生 `title="拖动调整过程栏宽度，双击重置"`。

## 10. 假设

- H1：当前实现里"右边进程栏"特指 `.process-panel`（即 `<AgentProcessPanel />` 所在的容器），而非 ChatWorkspace。
- H2：现有设计系统（CSS 变量 + 主题）已支持基础字号档，调整字号不需要新增主题分支。
- H3：项目不强制要求"宽度单位为像素"；如团队偏好 fr/百分比单位，需在 Plan 中追加。
