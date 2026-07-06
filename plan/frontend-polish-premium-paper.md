# Plan — 智能 OnCall 运维平台前端「极简纸感精品工作室」升级

> 配套 PRD：[prd/frontend-polish-premium-paper.md](../prd/frontend-polish-premium-paper.md)
>
> 计划只描述"怎么做"，不进入实现。在用户明确授权实施前不修改任何代码。
>
> 改动主战场：[frontend/src/styles.css](../frontend/src/styles.css)（约 1595 行 / 唯一 CSS 文件）；少量组件文件（≤ 5 处微改），不动业务逻辑、不动 API、不动测试用例。

## 1. 总览

| 阶段 | 内容 | 风险 | 回滚 |
| --- | --- | --- | --- |
| **P0 — 设计系统 token 化** | 在 `:root` 增加字号/行高/字重/字距/动效/语义层 token；旧名保留为 alias | 低 | 删 token 块 |
| **P1 — 全局排版替换** | 把组件中硬编码 `font-size/font-weight/letter-spacing` 替换为 token | 低 | 单文件 git diff |
| **P2 — 关键组件精修** | 登录、侧边栏、聊天、过程面板、基线表、空状态 6 个区域按 PRD 精修 | 中 | 单文件 git diff / 局部 CSS 块回退 |
| **P3 — 微动效与可达性** | 运行中动效、报告 pre 行号、focus 环、prefers-reduced-motion 复核 | 低 | 关 keyframes 即可 |
| **P4 — 验证与回归** | npm run build / npm test / 视觉抽样 / grep 残留颜色 | 低 | 不涉及代码改动 |

> 阶段之间强依赖：P1 依赖 P0 的 token；P2 依赖 P1；P3 与 P2 可并行。P4 必须独立。

## 2. 涉及模块与文件

| 文件 | 改动类型 | 行数预估 |
| --- | --- | --- |
| [frontend/src/styles.css](../frontend/src/styles.css) | 主战场，token 增加 + 全文件规则替换 | +120 / -40 |
| [frontend/src/components/LoginPage.tsx](../frontend/src/components/LoginPage.tsx) | 替换"登录中..."文字为呼吸点 | +5 |
| [frontend/src/components/Sidebar.tsx](../frontend/src/components/Sidebar.tsx) | 删除按钮默认 opacity 改为 .55 | +3 |
| [frontend/src/components/ChatWorkspace.tsx](../frontend/src/components/ChatWorkspace.tsx) | 不改业务，只在 .messages 顶部增加 1px 渐变线元素（可选） | 0 或 +6 |
| [frontend/src/components/AgentProcessPanel.tsx](../frontend/src/components/AgentProcessPanel.tsx) | 不改业务，只把硬编码颜色替换为 token 类名（无新增类名） | 0 |
| [frontend/src/components/ServiceBaselineManager.tsx](../frontend/src/components/ServiceBaselineManager.tsx) | 不改业务 | 0 |
| [frontend/index.html](../frontend/index.html) | 不改 | 0 |
| [frontend/src/components/AppShell.tsx](../frontend/src/components/AppShell.tsx) | 不改 | 0 |

不涉及：
- [tests/](../../tests/)：本次不涉及后端 / 单测逻辑变更。
- backend / 业务代码 / 数据文件。
- package.json / lockfile：无新依赖。
- TS 编译配置：不动。

## 3. 实施步骤

### P0 — 设计系统 token 化（styles.css `:root`）

1. **保留** 现有 `:root` 全部 token（不删，向后兼容）。
2. **追加** 设计 token：
   ```
   /* 字号 */
   --fs-10 ~ --fs-28
   /* 行高 */
   --lh-tight / --lh-snug / --lh-normal / --lh-loose
   /* 字重 */
   --fw-regular / --fw-medium / --fw-semibold / --fw-bold
   /* 字距 */
   --tracking-tight / --tracking-normal / --tracking-wide / --tracking-wider
   /* 动效 */
   --ease-out / --ease-in / --dur-fast / --dur-base / --dur-slow
   ```
3. **追加** 语义层（映射到现有 hex，不改值）：
   ```
   --text-primary: var(--ink);
   --text-secondary: var(--ink-soft);
   --text-tertiary: var(--muted);
   --text-disabled: var(--muted-2);
   --surface-canvas: var(--bg);
   --surface-card: var(--surface);
   --surface-input: var(--surface);
   --surface-muted: var(--surface-muted); /* 不变 */
   --border-subtle: rgba(0,0,0,0.04);
   --border-default: var(--line);
   --border-strong: var(--line-strong);
   --accent-fg: #ffffff;
   --accent-bg: var(--accent);
   --accent-ring: rgba(204, 124, 90, 0.18);
   --status-ok-fg: #3e9451; --status-ok-bg: var(--success-soft); --status-ok-border: rgba(110,185,122,0.36);
   --status-run-fg: var(--running); --status-run-bg: var(--running-soft); --status-run-border: rgba(217,131,36,0.34);
   --status-warn-fg: #b5691a; --status-warn-bg: #fbeed8; --status-warn-border: rgba(217,131,36,0.34);
   --status-err-fg: var(--risk); --status-err-bg: var(--risk-soft); --status-err-border: rgba(217,95,68,0.34);
   ```
4. **保留** `--shadow-sm/md: none`，新增 `--shadow-popover: 0 1px 2px rgba(20,18,16,.04)`，仅 `.feedback-card:hover` 等例外使用。
5. **关键提示**：所有"语义层"都用 `var(--xxx)` 引用旧名，未来切主题只改 `:root`。

### P1 — 全局排版替换（styles.css）

> 原则：先改"高频类"（输入、按钮、标题、消息、时间线、表格、字段、pill），再扫尾。

| 选择器 | 当前 | 改为 |
| --- | --- | --- |
| `body` | font-size 13 | `var(--fs-13)` |
| `.login-brand h1` | 20 / 700 | `var(--fs-18)` / `var(--fw-semibold)` / `var(--tracking-tight)` |
| `.login-brand p` | 13 | `var(--fs-12)` |
| `.chat-header h2, .baseline-header h2, .agent-panel h2` | 15 / 500 | `var(--fs-15)` / `var(--fw-semibold)` |
| `.empty-state-title` | 15 / 600 | `var(--fs-15)` / `var(--fw-semibold)` |
| `.empty-state-hint` | 13 | `var(--fs-13)` |
| `.message-bubble` | line-height 1.72 | `var(--lh-loose)` → 1.62 |
| `.message-bubble`（assistant） | padding 12/14 | 14/16 |
| `.message.user .message-bubble` | padding 11/13 | 12/14 |
| `.message-bubble code` | 12 | `var(--fs-12)` |
| `.message-bubble pre` | 默认字号 | 继承 13，行高 `var(--lh-normal)` |
| `.message-bubble table` | 13 | `var(--fs-13)`，加 `font-variant-numeric: var(--tnum)` 仅对数字 td |
| `.timeline strong` | 13 / 600 | `var(--fs-13)` / `var(--fw-semibold)` |
| `.timeline span` | 12 | `var(--fs-12)` |
| `.timeline code` | 11 | `var(--fs-11)` |
| `.timeline p` | 默认 | 保持 13 |
| `.event-detail-block span` | 12 / 600 | eyebrow 11/600/wider/uppercase |
| `.event-detail-fields dt` | 12 | eyebrow 11/600/wider/uppercase |
| `.event-detail-fields dd` | 11 mono | `var(--fs-11)` / mono / `font-variant-numeric: tabular-nums` |
| `.status-pill` | 12 / 600 | `var(--fs-12)` / `var(--fw-semibold)` |
| `.feedback-actions button` | 13 | `var(--fs-13)` / `var(--fw-semibold)` |
| `.baseline-detail-head h3` | 15 / 600 | `var(--fs-15)` / `var(--fw-semibold)` |
| `.baseline-table` | 13 | `var(--fs-13)` |
| `.baseline-table th` | 12 / 600 | eyebrow 11/600/wider/uppercase |
| `.baseline-table td` | 默认 | `var(--fs-13)`；数字 td 加 `font-variant-numeric: tabular-nums`，右对齐（除文字列） |
| `.session-title` | 13 / 500 | `var(--fs-13)` / `var(--fw-medium)` |
| `.session-meta` | 11 | `var(--fs-11)` / 加 tabular-nums |
| `.login-label, .label` | 11 / 600 / wider / uppercase | `var(--fs-11)` / `var(--fw-semibold)` / `var(--tracking-wider)`（保留大写） |
| `.sidebar-section-label` | 11 / 600 / wider / uppercase | 同上 |
| `.sidebar-brand h1` | 15 / 600 | `var(--fs-15)` / `var(--fw-semibold)` / `var(--tracking-tight)` |
| `.composer` 等容器字号 | 默认继承 | 不动 |

#### 1.1 数字列对齐（关键）

- `.baseline-table td:nth-child(2), td:nth-child(3), td:nth-child(4), td:nth-child(5)`：加 `font-variant-numeric: tabular-nums; text-align: right;`。
- `.timeline code, .event-detail-fields dd`：加 `font-variant-numeric: tabular-nums`（仅数字更好看，不强制）。
- `.session-meta`：加 `font-variant-numeric: tabular-nums`。

#### 1.2 边框 / 圆角统一

- 所有 `border-radius: 5px` 替换为 `var(--radius-sm)`（6px）。
- 报告 `.report-card pre`、`pre` 容器 圆角 6 改 8（`--radius-md`），与卡片 12 形成层级。
- 状态 pill `border-radius: 5` 改为 `999px`（药丸），并在 running 状态加 6×6 圆点。

### P2 — 关键组件精修（styles.css 区域 + 少量组件微调）

#### 2.1 登录页

- 标题 18/600，副标题 12，placeholder 颜色 `--muted`（[styles.css:255-259](../frontend/src/styles.css#L255)）。
- 错误条 1px `var(--status-err-border)`，padding 10/12 保持。
- 提交按钮：增加 `:active { transform: translateY(0.5px); }`。
- [LoginPage.tsx:97-99](../frontend/src/components/LoginPage.tsx#L97) "登录中..." 改为 `<span class="accent-breathe" />` 砖红 6×6 圆点 + 文字。

#### 2.2 侧边栏

- `.session-item.active` 加 `box-shadow: inset 2px 0 0 0 var(--accent);`（左侧 2px 砖红条）。
- `.session-item` 加 `transition: background-color var(--dur-base) var(--ease), color var(--dur-base) var(--ease);`。
- `.session-item:hover .session-title` 颜色 `--ink`（自然过渡）。
- `.session-delete` 默认 `opacity: .55`，hover 1.0（[Sidebar.tsx:77-84](../frontend/src/components/Sidebar.tsx#L77) 给按钮本身加 `style={{opacity}}` 不优雅，改为 CSS `.session-delete { opacity: .55; } .session-item:hover .session-delete { opacity: 1; }`）。
- `.sidebar-logout` `min-height: 34` → `36`。
- `.sidebar-brand` 下方增加 `padding-bottom: 14` + 6px 视觉锚点（已经是 border-bottom: 1px，可保留）。

#### 2.3 聊天区

- `.message.user .message-bubble` 改 `--surface` 背景 + 左侧 2px 砖红条；或保持浅底但加 `box-shadow: inset 2px 0 0 0 var(--accent)` 让用户气泡"自带身份"。
- `.message.assistant .message-bubble` 加 `box-shadow: 0 1px 0 rgba(20,18,16,.02);`（极轻浮起）。
- `.message.assistant.selected .message-bubble` 改 `border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-ring);`。
- `.messages` padding 桌面 `24/28` → `28/40`。
- `.empty-state-icon` 54×54 描边方框 → 56×56 圆形（`border-radius: 50%`）+ `--accent` 1px 描边 + 内部图标 size 22 → 24。
- composer input 圆角 12 → 10；高度 44 保持；focus 加 4px `--accent-ring` 环。
- 运行中动效：增加 `@keyframes accent-breathe`（1800ms，砖红 6×6 圆点），应用到 `.header-dot.is-running`；并在 `.messages` 顶部加 1px 渐变细线 `@keyframes accent-shimmer`（2400ms，linear-gradient(90deg, transparent, var(--accent), transparent)）。

#### 2.4 智能体过程面板

- `.status-pill` 改为药丸（radius 999）；status-* 背景/文字/描边统一用 `var(--status-*-bg/fg/border)`。
- running/evidence_insufficient/root_cause_ready 状态 pill：左侧 6×6 圆点呼吸（同 login 复用 `@keyframes accent-breathe`，但用 `--running` 色）。
- `.timeline li + li::before` 实线 → `border-left: 1px dashed var(--border-subtle);`。
- `.timeline-icon` 22×22 圆 + 1px `--line` 描边；delegate 节点颜色 `--accent`、背景 `--surface`、保留极浅高亮（去 `--signal-soft` 整行底色，只改节点）。
- `.report-card pre` 高度 360→420；加 CSS counter 行号：
  ```
  .report-card pre { counter-reset: line; }
  .report-card pre { white-space: pre; }
  .report-card pre::before { counter-increment: line; content: counter(line); ...}
  ```
  注意：pre 内容是 `run.answer`（`white-space: pre-wrap`），加行号要保留换行，建议把 `white-space: pre` 改为 `pre-wrap` + 用 `background-image: linear-gradient(...)` 模拟行号背景（更稳），第一版先用极简方案：`<pre>` 不加行号，但加 `::before` 显示"共 N 行 / 报告"提示信息（在 panel header 旁显示），等 P3 验证后再决定是否真正加行号。
- `.event-detail-fields` `92px` → `104px`。
- `.feedback-card` 加 `box-shadow: inset 2px 0 0 0 var(--accent);`。
- 字段键（`.event-detail-block span`、`.event-detail-fields dt`）改为 eyebrow 11/600/wider/uppercase/tertiary。

#### 2.5 服务基线

- 表头 eyebrow；列对齐（数字右 + tabular-nums）；行高 12/13；active 行加左侧 2px 砖红条。
- 列表项选中态同 2.2 思路（active 加 `inset 2px 0 0 0 var(--accent)`）。
- 表单 5 列在中屏（900-1280）最小宽 120 → 130。
- "暂无服务"、"暂无基线" 改用 `.empty-state` 统一组件（54×56 圆图标 + 标题 + hint）。

#### 2.6 统一空状态

- 抽出 `.empty-state` 的"标准变体"：图标盒统一 56×56 圆 + 1px `--accent` 描边，标题 15/600，hint 13 muted。
- 应用：聊天空、基线空（2 处）、未来其他空态。

### P3 — 微动效与可达性

1. **动效 keyframes**（追加到 styles.css 末尾）：
   - `@keyframes accent-breathe` 0% { transform: scale(1); opacity: .7 } 50% { scale 1.15; opacity: 1 } 100% { scale 1; opacity .7 }
   - `@keyframes accent-shimmer` 0% { transform: translateX(-100%) } 100% { translateX(100%) }
   - 应用：`.header-dot.is-running`、`.status-pill.running::before`、`.messages::before` 顶部线。
2. **focus 环**：`:focus-visible` 已是 2px `--accent`，保留；额外给输入框 `:focus` 加 `box-shadow: 0 0 0 4px var(--accent-ring);`。
3. **prefers-reduced-motion**：现有 0.01ms 媒体查询保留；keyframes 在该媒体查询下停止（`animation: none;`）。
4. **a11y**：
   - 主按钮文字对比度：白字 vs 砖红 `#cc7c5a`，对比度约 3.7:1 — 大字体 14+/600 满足 WCAG AA（4.5 是普通文本标准，大字 3:1 即可通过）。**已通过**，但加 `text-shadow: 0 0 1px rgba(0,0,0,.08)` 提升 1-2% 可读性。
   - 状态 pill running 文字 `--running #d98324` 在 `--running-soft #fbefd9` 上 — 约 3.2:1，大字 12/600 满足大字 3:1 标准，但**严格场景下偏紧**。改为 `--running` 颜色加深到 `#a8621a`（仅 status pill 文字色），保持对比度 4.5+。

### P4 — 验证与回归

1. **构建**：`cd frontend && npm run build`。
2. **单测**：`cd frontend && npm test`（如有）。不写新测。
3. **视觉抽样**：登录页 / 主聊天（空 + 满 + 运行中）/ 智能体过程（completed + 报告 + 反馈）/ 服务基线（空 + 列表 + 详情）共 8 个截图，肉眼对比。
4. **grep 残留**：
   - 硬编码 hex 在组件类里（应在 `:root` 之外几乎为 0）：
     `grep -nE '#[0-9a-fA-F]{3,8}' frontend/src/styles.css | grep -v ':root' | grep -v '/*' | grep -v -- '--accent\|--ink\|--surface\|--line\|--bg\|--muted\|--paper\|--signal\|--success\|--running\|--risk\|--badge\|--hover'`
     应为空（或仅 token 定义）。
   - 硬编码 font-size / font-weight / line-height：
     `grep -nE 'font-size: [0-9]+|font-weight: [0-9]+' frontend/src/styles.css`
     应仅 `:root` 区块出现。
   - 硬编码像素圆角：
     `grep -nE 'border-radius: [0-9]+px' frontend/src/styles.css`
     允许值：0 / 999px（药丸）/ 50%（圆）；其他出现需替换为 `var(--radius-*)`。
5. **响应式**：
   - 1280×800：主聊天 / 基线管理 正常。
   - 900×800：基线管理单列、表格内部滚动。
   - 760×600：登录页 320 卡片、表单单列。
6. **键盘**：`tab` 走查登录页（4 焦点：用户名/密码/提交，1 焦点：登出在侧边栏）。
7. **reduced-motion**：开系统"减少动效" → 关键帧不再播放、transition 0.01ms。

## 4. 关键前置条件

- 用户授权进入实施（CLAUDE.md 边界要求）。
- PRD §8.2 的 4 个待确认项至少完成 2、4（即药丸 pill + 保留旧名 alias），其余 1、3 可在 P0 内做合理默认并在 commit message 中标明。
- `frontend/` 工作区干净（无未提交改动，或已说明哪些是前置依赖——本轮不会触碰已修改的 styles.css 等文件中"未提交"区域，**本次改动需要把现有 M 状态也纳入新设计**）。

## 5. 验证方式

1. **构建验证**：`npm run build` 0 error 0 warning（warning 允许来自 vite-bundle 大小提示）。
2. **单测**：`npm test` 通过；如新增 `.empty-state` 类导致快照测试更新，**更新快照并人工核对**（不绕过测试）。
3. **视觉对照**：以 PRD §4 为准逐项核对；不符的回到对应小阶段处理。
4. **设计师直觉抽查**：
   - 把 PRD §6.1 的 6 条"应当 / 不允许"作为 checklist 走一遍。
   - 任意打开一个组件，先看色板是否来自 token；再看 transition 时长是否一致；再看数字是否对齐。

## 6. 回滚与降级

- **完全回滚**：`git revert <commit>`，单文件 CSS 影响范围可控。
- **局部回滚**：styles.css 顶部加注释 `/* v2 design system — 2026-06-25 */`，旧值保留为 alias 注释（不删），未来需要时一行 uncomment 即可回退。
- **P3 单独回滚**：注释 keyframes 块 / `prefers-reduced-motion` 媒体查询可单独关。

## 7. 风险点

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 改 styles.css 大量规则，一次性 diff 巨大 | 评审/回滚成本高 | 分阶段提交：P0/P1/P2 各自一个 commit；P3 单独 commit |
| 报告 pre 行号（CSS counter + white-space: pre-wrap）实现复杂、易引入显示问题 | 报告卡可读性反而下降 | P3 默认不加行号，先用 header 旁的"共 N 行"做最小信息；后续优化 |
| 状态 pill 改药丸 + 颜色加深与现有测试快照冲突 | 测试更新量大 | 优先把改动落到 styles.css，组件 markup 不变，jest snapshot 影响小 |
| 用户/助手消息气泡加左侧 2px 砖红条，Markdown 标题第一行被压 | 内容阅读体验略变 | padding 同步加 2px（12→14/14→16 已包含此补偿） |
| 中文字体在 Windows 默认 13px 时，table 数字右对齐 + tabular-nums 仍可能微抖动 | 1-2px 视觉偏移 | 表格数字列加 `padding-right: 12px` 给到 4-位数字 + 单位空间 |
| 修改现有 M 状态文件，可能与别人分支冲突 | 合并成本 | 在 commit message 顶部写明"design system v2 — 重新合入时优先此版本" |

## 8. 阻塞项

- 暂无（待用户授权实施 + 决策 4 个待确认项中的 1、3）。

## 9. 实施后需更新的文档

- 本 PRD / Plan 标题旁加 ✅ 完成标记与日期。
- [README.md](../../README.md) 顶部"产品截图"区块（若存在）替换为新版本截图。
- 若新增 `.empty-state` 抽象组件，在 [frontend/src/components/](../frontend/src/components/) 注释里简短说明用法（不写 README 之外新文档，避免文档膨胀）。

## 10. 不在本次范围

- 浅色 / 深色主题切换、主题切换器 UI。
- 引入 CSS-in-JS / Tailwind / UnoCSS 等新方案。
- 服务基线的 API 改造、表单校验增强、键盘快捷键。
- 智能体过程面板的搜索 / 过滤。
- 国际化 i18n 框架引入。
- 引入 e2e 测试（Playwright 等）。
