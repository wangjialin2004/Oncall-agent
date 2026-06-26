# PRD — 智能 OnCall 运维平台前端「极简纸感精品工作室」升级

> 目标：在现有米色纸张 + 砖红强调色（[styles.css:1-53](../frontend/src/styles.css#L1)）的温暖底色基础上，把前端打磨到"顶级设计师水准"——克制的层级、精确的排版、细腻的微动效、统一的视觉语言。
>
> 方向锚点：**极简纸感（精品工作室）**。对标 Linear / Notion / Apple Notes / Stripe Press 的克制感与密度感。
>
> 不推翻：保留米色纸张 + 砖红强调这一整套色板与组件结构。

## 1. 背景与目标

### 1.1 背景

- 已有"会话 + 聊天 + 智能体过程 + 服务基线管理"四大场景，组件结构基本齐全（[frontend/src/components/](frontend/src/components/)）。
- 上一轮"石墨蓝 + 青色"Plan（[plan/frontend-beautify-graphite-cyan.md](../plan/frontend-beautify-graphite-cyan.md)）已被推翻并替换为当前米色纸张主题。
- 用户希望继续在前端上"按照顶级设计师的标准完成"。结合现状，本轮的核心问题是 **"已经有了不错的底子，但缺少设计师级的一致性与微细节"**，而非"换个配色"。

### 1.2 目标

1. **视觉系统化**：把字号、行高、字重、间距、数字字距、动效曲线全部 token 化，全站一处定义、所有组件共用。
2. **排版精致化**：消息层级、时间线、表格、表单、空状态、状态 pill 的可读性与密度再上一档。
3. **微动效**：让所有交互都"有反馈、有节奏"，统一 transition 时长与曲线，避免半成品动画。
4. **质感统一**：阴影 / 描边 / 圆角 / 焦点态 全站一致；卡片、按钮、输入、徽标形成清晰"四件套"。

### 1.3 非目标

- 不推翻现有米色 + 砖红配色。
- 不引入浅色/深色双主题切换。
- 不引入新依赖（保持 `react-markdown` + `remark-gfm` 已是上限）。
- 不改业务逻辑、API、信息架构、交互流程。
- 不为追求"效果"而牺牲响应式与可访问性。

## 2. 用户与业务场景

| 场景 | 用户 | 关心什么 |
| --- | --- | --- |
| 登录 | 内部运维 / SRE | 一次看清、能立即动手；登录失败时不慌 |
| 排查告警 | 值班工程师 | 消息层级清晰、报告可信、过程可回放 |
| 跨轮次诊断 | 高级 SRE / TL | 时间线、专家委派、证据一目了然 |
| 维护基线 | 平台 owner | 列表与表单密度合适、指标数字一眼对齐 |

## 3. 设计语言（视觉系统）

### 3.1 色彩（沿用，仅精细化）

> 保留 `styles.css:1-53` 的 token，仅追加"token 命名层"和"语义层"。

- 已有"颜色层"：背景 `--bg / --bg-panel / --surface / --surface-muted / --hover-bg`；描边 `--line / --line-strong`；强调 `--accent / --accent-hover`；状态 `--success / --running / --risk`；文字 `--ink / --ink-soft / --muted / --muted-2`。
- 追加"语义层"（在 `:root` 集中定义），把组件从硬编码颜色中解放：
  ```
  --text-primary / --text-secondary / --text-tertiary / --text-disabled
  --surface-canvas / --surface-card / --surface-muted / --surface-input
  --border-subtle / --border-default / --border-strong
  --accent-fg / --accent-bg / --accent-ring
  --status-ok-fg/bg/border / --status-run-fg/bg/border / --status-warn-fg/bg/border / --status-err-fg/bg/border
  ```
- **不动现有 hex 值**，只把组件里的硬编码颜色替换为语义 token；这样后续主题切换只改 `:root`。
- 强调色 `--accent #cc7c5a` 已是砖红橙，作为唯一的"行动"色。状态色继续用 绿/琥珀/红，不与砖红撞色。

### 3.2 排版系统（核心新增）

新增 token，全站唯一来源：

```
/* 字号 */
--fs-10: 10px;  --fs-11: 11px;  --fs-12: 12px;  --fs-13: 13px;
--fs-14: 14px;  --fs-15: 15px;  --fs-16: 16px;  --fs-18: 18px;
--fs-20: 20px;  --fs-24: 24px;  --fs-28: 28px;

/* 行高 */
--lh-tight: 1.25;  --lh-snug: 1.4;  --lh-normal: 1.55;  --lh-loose: 1.75;

/* 字重 */
--fw-regular: 400; --fw-medium: 500; --fw-semibold: 600; --fw-bold: 700;

/* 字距 */
--tracking-tight: -0.01em; --tracking-normal: 0; --tracking-wide: 0.04em; --tracking-wider: 0.08em;

/* 数字字距（用于指标、版本号、时间、置信度等） */
--tnum: "tnum" on, "lnum" on;
```

层级对照（统一应用到组件）：

| 层级 | 字号 / 行高 / 字重 / 字距 | 用于 |
| --- | --- | --- |
| Display | 24-28 / tight / 600 / tight | 登录页大标题（很少用） |
| H1（页面标题） | 18-20 / snug / 600 / tight | 暂无，目前是 login h1=20 |
| H2（区块标题） | 15 / snug / 600 / normal | chat/baseline/agent header h2 |
| H3（卡片标题） | 14 / snug / 600 / normal | baseline-detail-head h3 |
| Body | 13 / normal / 400 / normal | 绝大多数正文 |
| Body Strong | 13 / normal / 600 / normal | 报告高亮、关键名词 |
| Caption | 12 / snug / 400 / normal | 副标题、辅助说明 |
| Eyebrow | 11 / tight / 600 / wider / uppercase | `.label`、section-label |
| Mono Caption | 11 / normal / 400 / wide | 报告 `<pre>`、timeline code、event-detail dd（用 `var(--tnum)` + `font-variant-numeric`） |

> 数字一律 `font-variant-numeric: tabular-nums`（基线表、指标、轮数、毫秒），保证纵向对齐。

### 3.3 间距与栅格

- 引入 **4px 基线网格**：所有 `padding / gap / margin` 只用 4 的倍数（4/8/12/16/20/24/32）。
- 已有 `--space-1..7` 数值不变，但要求每个组件不允许出现 5px、6px、7px、9px、11px 这类"零头"。
- 区块内部 padding：`12-16`；区块之间间距：`16-24`；页面边缘留白：`28`（桌面）/`16`（移动）。

### 3.4 圆角与描边

- 圆角三档：`--radius-sm: 6` / `--radius-md: 8` / `--radius-lg: 12`（已存在，保持）。
- 描边 1 档：`1px solid var(--border-default)`；分隔线 1 档：`1px solid var(--border-subtle)`。
- **禁止**用 `2px` 描边；**禁止**同一个组件出现两种以上圆角混用。

### 3.5 阴影与层

纸感不靠阴影，**默认无阴影**（`--shadow-sm/md: none` 已设定）。强调层级靠"卡片 vs 画布"的对比 + 极细 1px 描边。

例外允许场景（仅一处用极轻阴影）：
- 弹出层（feedback 卡片悬停态、模式切换菜单等）。允许 `0 1px 2px rgba(20,18,16,.04)`。

### 3.6 动效曲线与时长

集中定义在 `:root`：

```
--ease: cubic-bezier(0.2, 0, 0, 1);     /* 默认 */
--ease-out: cubic-bezier(0, 0, 0.2, 1); /* 元素出现 */
--ease-in: cubic-bezier(0.4, 0, 1, 1);  /* 元素消失 */
--dur-fast: 120ms;   /* 颜色、边框、图标 */
--dur-base: 180ms;   /* 卡片、按钮 */
--dur-slow: 280ms;   /* 弹层、抽屉 */
```

- 全站 transition 一律 `var(--dur-base) var(--ease)`，颜色 / 边框 / 阴影 / transform 类属性。
- 数字 / 进度类变化用 `var(--dur-slow)`。
- 已有的 `prefers-reduced-motion` 媒体查询保留。

### 3.7 焦点态

- 默认 `:focus-visible`：2px outline，使用 `--accent-ring`，offset 2px。
- 输入框聚焦：边框改 `--accent`，外圈 4px `--accent-ring` 半透明环（柔和光晕，不抢戏）。

## 4. 组件精修（按页面落地）

### 4.1 全站控件（"四件套"统一）

| 类型 | 尺寸 | 圆角 | 描边 | 背景 | 文字 |
| --- | --- | --- | --- | --- | --- |
| Primary button（登录、发送、采纳、新建） | 40h | 8 | transparent | `--accent` | white 600 |
| Secondary button（取消、ghost） | 36h | 8 | `--line` | `--surface` | `--ink-soft` 500 |
| Tertiary button（图标按钮） | 36×36 | 8 | `--line` | `--surface` | `--ink-soft` |
| Input / Select / Textarea | 40h | 10 | `--line` | `--surface` | `--ink` |

要点：
- 全部按钮 `transition: background-color var(--dur-base) var(--ease), border-color var(--dur-base) var(--ease)`。
- 禁用态：opacity .5、cursor not-allowed、不响应 hover。
- 主按钮 hover：背景改为 `--accent-hover`；按下 transform: translateY(0.5px)（极轻"按下"反馈）。
- 所有 icon button 加 `aria-label`，无障碍必须通过。

### 4.2 登录页

现状（[styles.css:281-376](../frontend/src/styles.css#L281)，[LoginPage.tsx](../frontend/src/components/LoginPage.tsx)）：

- 已是干净的米色卡 + 砖红品牌图标。差距：
  1. 标题字号 / 字重 略大（20/700），与"克制纸感"略冲突——下调到 18 / 600 / tracking tight。
  2. 副标题用 `--muted` 颜色 + 13px，**与正文同级**，层级不明显——降到 caption 12。
  3. 输入框 placeholder 颜色 `--muted-2` 偏淡，对比度不足——提到 `--muted`。
  4. 错误提示框用 `--risk-soft` 浅红，**已经很好**，但 `border` 是写死的 `rgba(217, 95, 68, 0.28)`，替换为 `var(--status-err-border)`。
  5. 缺一个"登录中" 的微动效（旋转 spinner / 砖红脉冲点），目前是纯文字"登录中..."——加 6×6 砖红方点呼吸。

### 4.3 侧边栏

现状（[styles.css:378-592](../frontend/src/styles.css#L378)，[Sidebar.tsx](../frontend/src/components/Sidebar.tsx)）：

- 结构与密度都很好。差距：
  1. `.session-item.active` 仅靠背景区分，对扫读不够——加左侧 2px 砖红"标记条"（inset 0 0 0 0 → 2px 0 0 0 var(--accent)）。
  2. 会话标题 13/500，悬停态没有"渐进颜色变化"，交互反馈弱——加上 `color: var(--ink)` 的 120ms 过渡。
  3. 删除按钮 13px hover 变红，但**只在 hover 才出现红色**——首次渲染如果用户已经想删，找不到入口。改进：把删除按钮默认透明度 .55、hover 1，让它在视觉里始终可见但弱化。
  4. 侧边栏底部"退出登录" 34h 比其他控件矮 2px——统一为 36h。
  5. 顶部 brand 与"新建会话"按钮之间缺少视觉锚点——加 6px 高度细分隔。

### 4.4 聊天区（ChatWorkspace）

现状（[styles.css:594-867](../frontend/src/styles.css#L594)，[ChatWorkspace.tsx](../frontend/src/components/ChatWorkspace.tsx)）：

- 已经支持 Markdown 渲染（`react-markdown` + `remark-gfm`）。差距：
  1. **消息行高 1.72 偏松**，对照 Linear/Notion 标准正文密度，建议收到 1.62（`--lh-loose` 的小邻居，留出长句可读空间）。
  2. 用户气泡用 `--hover-bg` 浅米底，**与画布对比不够**，信息层级弱——改为 `--surface` + 左侧 2px 砖红边条；或保持浅底但加深 border 到 `--line-strong`。
  3. 助手消息气泡 `border` `--line`，与画布区分弱——改为 `--border-subtle`，配合微妙的"卡片浮起"：`box-shadow: 0 1px 0 rgba(20,18,16,.02)`（极轻）。
  4. 选中态目前只改 `border-color` 为 `--accent`，缺少层级感——加 `border-color: var(--accent)` + `box-shadow: 0 0 0 3px var(--accent-ring)`（柔和外圈）。
  5. `.messages` padding 24/28：桌面端改为 28/40（左右再放宽），让正文列更"舒展"。
  6. `.empty-state-icon` 是 54×54 描边方框，**显得"工具化"**，不符合精品感——改为 56×56 圆形 + `--accent` 1px 描边、内部 Activity 图标 size 24。
  7. composer input 圆角 12（`--radius-lg`），但其他 input 是 10——统一为 10。
  8. 输入框 focus 改 border 到 `--accent` + 4px `--accent-ring` 环，与全站一致。
  9. **运行中动效**：`is-running` 状态目前只有"小绿点 + 文字"。改为：dot 用砖红呼吸（`@keyframes accent-breathe`，1800ms 循环）；同时消息区顶部加 1px 渐变"推理进行中"细线（`linear-gradient(90deg, transparent, var(--accent), transparent)` 滑动 2400ms）。

### 4.5 智能体过程面板（AgentProcessPanel）

现状（[styles.css:869-1155](../frontend/src/styles.css#L869)，[AgentProcessPanel.tsx](../frontend/src/components/AgentProcessPanel.tsx)）：

- 排版与密度都偏"工具"。差距：
  1. **状态 pill**（`.status-pill`）：22h、`border-radius: 5`、硬编码描边色——重做为统一"chip"组件：22h、radius 999（药丸状）、背景 `--status-*-bg`、文字 `--status-*-fg`、无 border 或 1px `--status-*-border`。running 状态加左侧 6×6 同色圆点呼吸。
  2. **时间线**：
     - 节点图标 28×28 描边方框——改为 22×22 描边圆，悬停态高亮 `--accent`。
     - 连接线 1px `--line` —— 改为 1px dashed `--border-subtle`，让"过程"在视觉上"虚线进行"。
     - delegate 节点的 `signal-soft` 高亮保留，但**整体弱化**：只把节点图标改 `--accent`，不再整行高亮底色，避免"主次颠倒"。
  3. **报告卡 `<pre>`**：白底等宽——增加最大高度 420、行高 1.7、行号（用 CSS counter 实现，不依赖 JS），首行加 `code-counter` 显示 `1`、每 5 行加粗。
  4. **字段网格** `.event-detail-fields`：当前两列 `92px 1fr`，左标签略挤——改为 `104px 1fr`，且左标签用 eyebrow 11/600/wider/uppercase/muted，让"键" 视觉上明显低于"值"。
  5. **`feedback-card`** 信号高亮 + 圆角保留——加一个 1px 砖红左边条（inset 0 0 0 0 2px 0 0 var(--accent)），与品牌色呼应。

### 4.6 服务基线管理

现状（[styles.css:1173-1407](../frontend/src/styles.css#L1173)，[ServiceBaselineManager.tsx](../frontend/src/components/ServiceBaselineManager.tsx)）：

- 表格密度合适，但**表头与正文层级不清**。差距：
  1. 表头 12/600 muted，**对比度与正文接近**——改为 eyebrow 11/600/wider/uppercase + `--text-tertiary`；并加底部分隔线粗度。
  2. **指标数字列**（下限/上限/单位/采样窗口）：用 `font-variant-numeric: tabular-nums`，让上下限"垂直对齐"。单位列右对齐，文字列左对齐。
  3. 行高 10/11 padding 偏紧——升到 12/13，呼吸感更好。
  4. 行 hover 当前是 `--hover-bg`——保留，但 active 行（已选中服务）加左侧 2px 砖红边条，与侧边栏选中态一致。
  5. 表单 5 列 + 按钮 — 桌面端 1280+ 列宽足够；中屏 1280↓ 时 5 列会挤，需要检查现有 120px 最小宽是否够，**如果不够扩到 130**。
  6. "暂无基线" 空状态：用 `.empty-state` 同款（图标 + 标题 + hint），统一空状态语言。

### 4.7 服务基线列表 / 表单统一空状态

- 全站空状态统一为 `.empty-state`（圆图标 + H3 + caption hint），聊天区、过程面板"暂无事件"、基线"暂无基线"、基线"暂无服务" 全部用这一组件。

## 5. 关键流程（无改动，仅确认）

- 登录 → 新建会话 → 提问 → 查看过程 → 采纳/纠正 → 删除会话（流程不变，本次只打磨视觉）。
- 服务基线：列表 → 详情 → 表单新增/删除 → 错误提示（流程不变）。

## 6. 验收标准

### 6.1 视觉

1. 全站所有颜色都通过 `:root` 的语义 token 引用；`grep` 不到 `#[0-9a-fA-F]{3,8}` 在组件类选择器规则里（除 `:root`）。
2. 全站字号、行高、字重、字距 100% 来自 token；硬编码 font-size/font-weight 只允许出现在 `:root` 注释与 fallback。
3. 数字（指标、轮数、毫秒、Trace ID）一律 `font-variant-numeric: tabular-nums`。
4. 圆角仅 `6 / 8 / 10 / 12` 四档；其他值不允许。
5. 所有按钮 / 输入 / 卡片交互都有 transition；时长来自 `--dur-*`；曲线来自 `--ease*`。
6. `prefers-reduced-motion` 时所有动效缩短到 0.01ms（已实现，确认仍生效）。

### 6.2 体验

1. 登录页：标题/副标题/输入/按钮/错误 五层层级一眼能数清。
2. 聊天：用户/助手气泡层级分明；选中态有 3px 外圈砖红环；运行中有动效反馈。
3. 智能体过程：时间线 1px dashed 连接，delegate 节点高亮克制；报告 pre 有行号；字段键值层级清晰。
4. 服务基线：表格指标数字纵向对齐；空状态风格统一；hover/active 视觉一致。
5. 响应式（1280 / 900 / 760 三档）布局正常、不出现横向滚动条（基线表 680 最小宽除外）。

### 6.3 质量门

- `npm run build` 通过（TS 检查 + Vite 打包）。
- 现有 `npm test` 不回归（[tests/test_harness_service.py](../../tests/test_harness_service.py) 与前端单测若有）。
- 浏览器 console 无 warning（颜色对比、a11y）。
- Lighthouse Accessibility ≥ 95（仅核对，不强制）。

## 7. 指标与成功信号

- 视觉一致性：抽 10 个组件，肉眼对比 5 分钟内说不出"哪个像另一个项目"。
- 排版密度：在不滚动的情况下，聊天区可同时看到 1 条用户消息 + 1 条助手消息 + 部分时间线（1280 高度 ≥ 800）。
- 阅读体验：把基线表 4 行指标上下限数字用尺子量，应全部纵向对齐。
- 维护成本：新增组件时，设计师只需引用语义 token 与字号 token，不再决定 hex / px。

## 8. 风险与待确认

### 8.1 风险

- **低**：仅改 1 个 CSS 文件 + 极少组件类名替换，易回滚（git diff）。
- **中**：颜色 token 重命名（如 `--surface` → `--surface-card`）需要全文件批量替换；建议保留旧名为 `--surface-2` 兼容别名，避免一次改动过大。
- **中**：报告 pre 加 CSS counter 行号会改变 DOM 结构；如果未来有截图回归测试需要更新基线。

### 8.2 待确认（不确认前不进入实施）

1. **状态 pill 是否改为药丸形（border-radius 999）？** 与"克制纸感"匹配更好，但与现有 5px 圆角风格不同。本 PRD 默认改为药丸形。
2. **空状态图标是否从 54×54 方框改为 56×56 圆？** 本 PRD 默认改。
3. **运行中动效**用"砖红呼吸点 + 顶部细线滑动"二选一还是并存？本 PRD 默认并存（点 + 细线）。
4. **是否需要新增语义 token 的别名（保留旧名）？** 建议保留以降低一次改动量。

## 9. 文档与位置

- 本 PRD：`prd/frontend-polish-premium-paper.md`
- 配套实施 Plan：`plan/frontend-polish-premium-paper.md`
- 改动文件：[frontend/src/styles.css](../frontend/src/styles.css)（主战场）、极少量 [frontend/src/components/LoginPage.tsx](../frontend/src/components/LoginPage.tsx)（spinner 点）、[frontend/src/components/Sidebar.tsx](../frontend/src/components/Sidebar.tsx)（删除按钮透明度）、[frontend/src/components/ChatWorkspace.tsx](../frontend/src/components/ChatWorkspace.tsx)（基本不动）、[frontend/src/components/AgentProcessPanel.tsx](../frontend/src/components/AgentProcessPanel.tsx)（不动，类名对齐即可）。
- 不修改：[frontend/index.html](../frontend/index.html)（theme-color 保留当前米色值）、业务逻辑、API、tests 逻辑。
