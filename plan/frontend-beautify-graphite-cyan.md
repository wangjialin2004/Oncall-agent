# 前端美化 Plan — 石墨蓝 + 青色配色 / 排版与信息密度

> 注：本项目 CLAUDE.md 约定 Plan 文档应放在 `plan/` 目录。当前处于 plan 模式只能写本计划文件；如你批准实施，我会在动手前先把本计划落一份副本到 `plan/frontend-beautify-graphite-cyan.md`（遵循项目约定），再进入 CSS 改动。

## Context（为什么做）

现有前端（React 18 + TS + Vite）已是一套较完整、**完全 token 驱动**的暗色 + 紫色设计，单文件样式 [frontend/src/styles.css](frontend/src/styles.css)（约 1320 行）。你希望：

- **全新配色风格** → 放弃紫色，改用「石墨蓝中性底色 + 青色单一强调色」（暗色，可观测平台质感，状态色语义更清晰）。
- **覆盖全部页面**：登录页、主聊天界面（侧边栏 / 聊天区 / 智能体过程面板）、服务基线管理页。
- **最优先：排版与信息密度** → 字体层级、行高、数字对齐、表格/时间线密度、内容可读性。

关键有利条件：几乎所有颜色都来自 `:root` 的 CSS 变量，因此换肤主要是**重映射变量**，再修掉少数硬编码紫色。改动集中、风险可控、易回滚（一个文件 + index.html）。

## 推荐方案

分三个阶段，**阶段 1、2 为核心**，阶段 3（聊天 Markdown 渲染）较重、引入依赖，标记为可选，可在批准时决定是否包含。

---

### 阶段 1 — 全新配色（石墨蓝 + 青色），覆盖全部页面

**1.1 重映射 `:root` 设计 token**（[styles.css:16-95](frontend/src/styles.css#L16)）。这一步即可覆盖约 80% 的页面元素（侧边栏、聊天、过程面板、基线页、登录页均引用这些变量）。目标值：

```
背景（中性石墨/板岩，冷而近中性）
--bg-void:#06090d  --bg-sidebar:#0a0e13  --bg-base:#0b0f14
--bg-surface:#11161d  --bg-raised:#161d27  --bg-high:#1f2732  --bg-input:#0d1218

边框（去掉蓝紫倾向，改中性板岩）
--border-0:rgba(150,170,190,.06) / -1:.12 / -2:.20 / -3:.34

文字
--text-0:#e8eef4  --text-1:#aebac8  --text-2:#6f7e8e  --text-3:#46535f

品牌强调（青色；额外加深色填充变量以保证对比度）
--brand:#22d3ee  --brand-light:#67e8f9  --brand-dim:#0891b2  (深青/teal，用于填充)
--brand-glow:rgba(34,211,238,.20)  --brand-subtle:rgba(34,211,238,.09)  --brand-border:rgba(34,211,238,.28)

状态色（保留语义，与青色品牌色拉开；run 仍为琥珀，避免冲突）
--s-run:#f59e0b（不变） --s-ok:#34d399 --s-err:#f87171（不变） --s-warn:#fb923c（不变）
```

**1.2 修掉绕过 token 的硬编码紫色**（必须改，否则换肤后仍露紫）：
- 侧边栏品牌图标渐变 [styles.css:203](frontend/src/styles.css#L203) `linear-gradient(135deg,#7c3aed,#a855f7)`
- 用户消息气泡渐变 [styles.css:502](frontend/src/styles.css#L502) `…var(--brand) 0%, #4f46e5 100%`
- 登录品牌图标渐变 + 阴影 [styles.css:976-977](frontend/src/styles.css#L976)
- 登录按钮渐变 [styles.css:1040](frontend/src/styles.css#L1040)
- 侧边栏环境光 [styles.css:176](frontend/src/styles.css#L176) `rgba(59,130,246,…)` → 青色 `rgba(34,211,238,.10)`
- 登录页环境光 [styles.css:947](frontend/src/styles.css#L947) `rgba(124,58,237,…)` → 青色

**1.3 填充元素对比度策略（重要）**：青色 `#22d3ee` 偏亮，白字直接放其上对比不足。原则：
- **填充型主操作**（登录按钮、发送按钮 `.icon-button.primary`、用户消息气泡）：用深青渐变 `linear-gradient(135deg,#0891b2,#0e7490)` + 白字，对比安全且仍属青色家族。
- **小尺寸品牌图标盒**（侧边栏 / 登录的 Activity 图标，组件里写死 `color="#fff"`，见 [Sidebar.tsx:36](frontend/src/components/Sidebar.tsx#L36)、[LoginPage.tsx:43](frontend/src/components/LoginPage.tsx#L43)）：用较亮渐变 `#22d3ee→#06b6d4`，细描边白图标可接受；实现时目测确认。
- 其余「描边 / 文字 / subtle 背景」类用法（`--brand-border`、`--brand-light`、`--brand-subtle`）直接随 token 走，无需逐处改。

**1.4 其它**：`index.html` 的 `<meta name="theme-color">`（[index.html:6](frontend/index.html#L6)）由 `#090f1e` 改为新 `--bg-base`。

---

### 阶段 2 — 排版与信息密度（你的最高优先项）

**2.1 加载等宽字体**：当前 `--font-mono` 引用 JetBrains Mono 但**实际未加载**（[styles.css:4](frontend/src/styles.css#L4) 与 index.html 只引入了 Inter）。在 Google Fonts import 与 [index.html:10-13](frontend/index.html#L10) 增加 JetBrains Mono，让报告块、时间线 `code`、基线表格真正用上等宽，提升数据可读性。

**2.2 引入字号 / 行高 token 并落地**：当前字号散落硬编码（14/13/12/11/10px）。在 `:root` 增加：
```
--fs-xs:11  --fs-sm:12  --fs-13:13  --fs-base:14  --fs-md:15  --fs-lg:16  --fs-xl:18  --fs-2xl:22
--lh-tight:1.3  --lh-snug:1.45  --lh-normal:1.6  --lh-relaxed:1.7
```
先在高频区域（聊天消息、过程面板、时间线、卡片、表格、标题）替换为 token，统一层级；其余硬编码字号作为一致性收尾逐步迁移（可分批，不阻塞）。

**2.3 数字对齐与表格密度**：服务基线表 [styles.css:1272-1292](frontend/src/styles.css#L1272) 的指标列加 `font-variant-numeric: tabular-nums`，列右对齐数值列；适度收紧行内边距并加轻微斑马纹/行 hover，提升密集信息扫读效率。

**2.4 间距与可读性微调**：在不破坏呼吸感前提下，收紧消息列表 `gap`、面板 padding 等高密度区域；优化空状态（[styles.css:444-481](frontend/src/styles.css#L444)）与标题层级对比；消息气泡行高用 `--lh-normal`。

---

### 阶段 3 —（可选，较重）聊天内容 Markdown 渲染

当前助手消息为纯文本 `white-space: pre-wrap`（[styles.css:497](frontend/src/styles.css#L497)、[ChatWorkspace.tsx](frontend/src/components/ChatWorkspace.tsx)）。运维诊断结论常含标题/列表/代码/表格，渲染 Markdown 能显著提升信息密度与可读性。

- 需新增依赖：`react-markdown` + `remark-gfm`（表格/任务列表），并做 XSS 防护（默认不渲染原始 HTML）。
- 改动 [ChatWorkspace.tsx](frontend/src/components/ChatWorkspace.tsx) 消息渲染，并在 styles.css 增加 `.message-bubble` 内 Markdown 元素（标题/列表/`pre`/`table`/`code`）样式。
- **权衡**：引入运行时依赖 + 组件改动 + 少量测试更新，超出「纯换肤」范畴。**默认不含**，按你意愿纳入。

---

## 涉及文件

- [frontend/src/styles.css](frontend/src/styles.css)：阶段 1、2 全部改动（token + 硬编码修正 + 排版/密度）。
- [frontend/index.html](frontend/index.html)：字体加载、`theme-color`。
- 阶段 3 才涉及：[frontend/src/components/ChatWorkspace.tsx](frontend/src/components/ChatWorkspace.tsx)、`frontend/package.json`。
- 组件中两处 `color="#fff"` 图标无需改色（随图标盒渐变即可），仅在阶段 1.3 目测确认对比度。

## 不在范围内（非目标）

- 不改信息架构 / 交互流程 / 业务逻辑 / API。
- 不新增浅色主题或主题切换（你选的是「全新配色」而非「浅色 + 切换」）。
- 不重写组件结构（除阶段 3 的消息渲染外）。

## 验证方式

1. `cd frontend && npm run dev`，逐页核对：登录页 → 主聊天（侧边栏/聊天/过程面板，含运行中状态、时间线、状态 pill、空状态）→ 服务基线管理页。
2. 全局搜索确认无残留紫色：`grep -rEi '#7c3aed|#a855f7|#a78bfa|#6d28d9|#4f46e5|124, *58, *237' frontend/src frontend/index.html` 应为空。
3. 对比度抽查：登录按钮、发送按钮、用户消息气泡（白字 vs 深青底）；品牌图标盒（白图标 vs 青底）。
4. 响应式：1100px / 760px 两个断点布局正常（[styles.css:896-923](frontend/src/styles.css#L896)）。
5. `npm run build` 通过 TS 检查与打包；`npm test` 现有用例不回归（阶段 3 若纳入需同步更新涉及的快照/渲染测试）。

## 风险与待确认

- **风险（低）**：改动集中在 1 个 CSS 文件 + index.html，易回滚（git diff / 还原即可）。
- **待确认**：阶段 3（Markdown 渲染，引入依赖）是否纳入本次？默认不纳入。
- **待确认**：阶段 2.2 的硬编码字号是否要求**本次全部迁移**到 token，还是先覆盖高频区域、其余渐进迁移（推荐后者，降低一次性改动量）。
