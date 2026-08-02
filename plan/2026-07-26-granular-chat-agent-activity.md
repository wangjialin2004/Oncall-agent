# 对话内逐条工具 / Agent 活动消息实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将同一轮助手消息中的聚合过程卡改为按发生顺序追加的独立活动消息，使路由、计划、每次工具调用和每次 Agent 派遣都能原位显示运行与完成状态，最终回答单独显示在活动消息之后。

**Architecture:** 保留现有服务端公开事件投影、稳定 `activity-N` 标识和 `AgentRun` 数据结构，只扩展前端纯 reducer 的扁平展示投影。`ChatWorkspace` 将活动消息与最终回答渲染为同一轮中的兄弟节点；旧聚合式 `InlineAgentActivity` 保留为一级降级路径，旧右侧过程栏继续作为二级降级路径。

**Tech Stack:** React 18、TypeScript、Vitest、Testing Library、Lucide、现有 SSE `TimelineEvent` / `AgentRun` 契约。

**Progress:** [实施与验证记录](./2026-07-26-granular-chat-agent-activity-progress.md)

---

## 1. 问题与目标形态

当前 `InlineAgentActivity` 在助手气泡内部用一个总览按钮包住全部步骤，并在完成时整体折叠。虽然工具和专家生命周期已经可见，但用户仍只能看到“一张过程卡”，无法像 Codex 一样把每次命令、工具调用或 Agent 派遣识别为独立发生的动作。

本次目标不是增加更多内部细节，而是改变信息分组：

```text
用户消息

  [路由活动]  已识别为综合诊断                         已完成
  [计划活动]  已制定排查计划                           已完成
  [工具活动]  查询指标                                 进行中
  [Agent 活动] 指标专家                                 进行中
  [工具活动]  检索日志                                 已完成 · 1.3 秒
  [Agent 活动] 日志专家                                 已完成 · 4.8 秒

助手最终回答
```

每个活动是聊天时间线中的独立块，不放进一个可整体折叠的总卡。相同 `activity id` 的 start / terminal 事件原位更新，不新增重复块；新节点第一次出现时追加新块。最终 Markdown 回答是独立助手气泡，位于活动序列之后。

## 2. 决策与默认值

1. **默认开启逐条活动消息。** 新增 `VITE_GRANULAR_AGENT_ACTIVITY_ENABLED`，未配置时视为 `true`。
2. **两级降级。** `VITE_GRANULAR_AGENT_ACTIVITY_ENABLED=false` 回到当前聚合式内联过程卡；`VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` 回到旧右侧过程栏。
3. **前端投影，不改 SSE 语义。** 不新增或改变 `type` / `stage`；继续使用现有公开事件 allowlist 和稳定公开 ID。
4. **每个可观察动作独立显示。** route、plan/replan、普通 tool、每位 expert、expert 内部可见 tool、verify/re-evidence、report 都可形成独立活动消息。
5. **生命周期原位合并。** `running -> completed/degraded/failed` 只更新原活动；不得因为 terminal 事件产生第二条同名消息。
6. **按首次出现排序。** 并行执行的完成先后只更新状态，不重排已经出现的活动，避免列表跳动。
7. **不整体折叠，每个节点独立展开。** 完成后活动仍保留；历史恢复与 live 流展示一致。每条活动可独立展开安全详情，运行中新节点默认展开；用户手动切换后不再被状态更新覆盖。
8. **过程与正文分离。** 活动块不使用助手 Markdown 气泡背景；最终正文、建议动作、反馈/蒸馏仍属于该轮助手结果区。
9. **隐私边界不放宽。** 前端模型仍不得包含 arguments、result、prompt、context、subtask、trace/span/evidence、usage 或 raw error。

## 3. 设计方向

### 3.1 信息结构

- 活动消息使用一条低对比度左轨道连接，但每条拥有自己的状态图标、动作图标、标题和状态/耗时。
- Agent 派遣不再藏在“并行调用 N 位专家”的单一卡片中：父派遣可作为一条调度活动，每位专家和专家工具各自成为后续独立活动。
- 层级只通过 12px 缩进和短连接线表达，不使用嵌套卡片。
- 每条活动的整行标题是独立展开按钮；展开区显示产品化用途说明以及节点类型、状态、耗时、所属 Agent、并行方式等公开字段，不显示原始 payload。
- 运行中只让状态图标旋转；完成项不做入场位移动画，避免流式更新造成视觉跳动。

### 3.2 视觉令牌

沿用现有产品令牌，不引入新字体或新依赖：

- `Ink`：`var(--ink)`，活动标题。
- `Muted`：`var(--muted)`，状态和耗时。
- `Accent`：`var(--accent)`，运行态。
- `Risk`：`var(--risk)`，失败/降级态。
- `Line`：`var(--line)` / `var(--line-strong)`，时间线和层级连接。

本次唯一显著特征是“逐条长驻的执行轨迹”：让列表结构本身表达执行顺序，不新增装饰卡、色块或营销式视觉。

### 3.3 响应式与无障碍

- 桌面状态/耗时在行尾；390px 下换到标题下方，最长中文或英文工具名不得撑出容器。
- 每条运行状态通过文本和图标共同表达，不只依赖颜色。
- 每条活动使用 `aria-expanded` / `aria-controls` 关联自己的详情区域；键盘 Enter/Space 可独立切换。
- 活动序列使用 `role="list"`；新增活动由一个受控 `aria-live="polite"` 摘要播报，避免每次 terminal 更新重复朗读整个列表。
- `prefers-reduced-motion` 下关闭 spinner 动画。

## 4. 现有能力与外部方案核对

检查日期：2026-07-26。

| 候选 | 版本 / 状态 | 许可证与安全 | 结论 |
|---|---|---|---|
| 仓库现有 `InlineAgentActivity` + `buildInlineActivityModel` | 当前 dirty worktree，已具备公开 ID、start/terminal 合并、102 项前端回归证据 | 项目内部代码；现有服务端安全投影已覆盖 live/history | **采用并扩展。** 数据契约足够，只需增加扁平时间线投影和兄弟节点渲染。 |
| [OpenAI Codex](https://github.com/openai/codex) | `rust-v0.145.0`，2026-07-21 发布，仓库未归档且当日仍有提交 | Apache-2.0；仅参考用户提供截图中的交互行为，不复制代码 | **采用交互原则。** 每个工具/Agent 动作独立出现并原位完成；Codex 桌面 UI 本身不是可直接复用的 React 组件。 |
| [assistant-ui](https://github.com/assistant-ui/assistant-ui) | 最新 release `@assistant-ui/react-streamdown@0.3.7`，2026-07-26 发布，仓库未归档且活跃 | MIT；引入会扩大依赖与渲染抽象面 | **拒绝依赖。** 当前项目已有流、状态与历史恢复模型，为一个展示调整迁移线程框架不划算。 |
| [React Rendering Lists](https://react.dev/learn/rendering-lists) | 官方文档当前展示 React 19.2；本项目 React 18.3.1 | 官方文档；稳定 key 原则适用于 React 18 | **采用稳定 key 原则。** 使用公开 activity ID 保持 start/terminal 更新时的组件身份。 |

不复制外部代码、不增加 npm 依赖，因此不新增供应链或许可证风险。实施前执行 `npm audit --omit=dev` 只记录现有生产依赖状态，不以自动升级改变本任务范围。

## 5. 范围与非目标

### 范围

- 新增扁平、有序、稳定 ID 的活动消息投影。
- 新增逐条活动消息组件并接入聊天时间线。
- 每个活动节点支持独立展开安全、结构化详情。
- 把最终回答与活动序列拆为兄弟节点。
- 保留反馈、纠正、蒸馏、建议动作、checkpoint 提示。
- 新增默认开启的前端降级开关。
- 覆盖 live 更新、完成、失败、历史恢复、移动端和旧模式回退。

### 非目标

- 不展示工具参数、原始结果、专家 subtask 或内部 trace。
- 不改变后端 Harness 路由、委派策略、checkpoint 或持久化结构。
- 不把每个 token/chunk 变成独立消息。
- 不删除当前聚合式内联组件或旧右栏。
- 不引入 assistant-ui 或其他消息线程框架。

## 6. 文件边界

| 文件 | 责任 |
|---|---|
| `frontend/src/components/chat/inlineActivityModel.ts` | 生成稳定、扁平、有父子关系、首次出现顺序和安全详情的展示条目。 |
| `frontend/src/components/chat/AgentActivityFeed.tsx` | 新增；逐条渲染活动消息，不包含最终回答与反馈动作。 |
| `frontend/src/components/chat/InlineAgentActivity.tsx` | 保留当前聚合模式；提取可共享的 checkpoint / outcome 区域时保持行为不变。 |
| `frontend/src/components/chat/RunOutcome.tsx` | 新增；承载 checkpoint、建议动作、反馈与蒸馏，供新旧模式复用。 |
| `frontend/src/components/ChatWorkspace.tsx` | 将活动 feed、最终 Markdown 气泡和 outcome 渲染为同轮兄弟节点。 |
| `frontend/src/App.tsx` | 解析并下传粒度化活动开关。 |
| `frontend/src/vite-env.d.ts`、`.env.example` | 声明并记录默认 true 与两级回退。 |
| `frontend/src/styles.css` | 逐条活动布局、响应式、焦点和 reduced-motion。 |
| `frontend/src/components/chat/__tests__/inlineActivityModel.test.ts` | reducer 顺序、合并、层级与隐私断言。 |
| `frontend/src/components/chat/__tests__/AgentActivityFeed.test.tsx` | 新增；逐条渲染、状态原位更新、无整体卡/折叠和无障碍断言。 |
| `frontend/src/components/__tests__/ChatWorkspace.test.tsx`、`frontend/src/components/__tests__/App.test.tsx` | 活动与最终回答分离、三种开关模式和历史恢复回归。 |

## 7. 实施任务

### Task 1: 扩展纯活动模型

**Files:**
- Modify: `frontend/src/components/chat/inlineActivityModel.ts`
- Modify: `frontend/src/components/chat/__tests__/inlineActivityModel.test.ts`

- [x] **Step 1: 写失败测试**

构造 route -> plan -> tool start -> expert group -> expert tool -> terminal -> report 事件流，断言：

```ts
expect(model.feed.map((item) => item.id)).toEqual([
  "route",
  "plan",
  "tool:activity-1",
  "experts:activity-2",
  "experts:activity-2:metric",
  "tool:activity-3",
  "report",
]);
expect(model.feed.find((item) => item.id === "tool:activity-1")?.state).toBe("completed");
expect(model.feed.find((item) => item.id === "tool:activity-1")?.details).toEqual(
  expect.arrayContaining([{ label: "节点类型", value: "工具调用" }]),
);
```

另加断言：terminal 不增加长度、完成事件不重排、专家分别可见、历史 events 得到相同 feed、详情只来自公开 allowlist、敏感字段不进入模型或 details。

- [x] **Step 2: 运行测试确认失败**

```bash
cd frontend
npm test -- --run src/components/chat/__tests__/inlineActivityModel.test.ts
```

Expected: 因 `feed` 不存在或仍为树形聚合而失败。

- [x] **Step 3: 实现最小扁平投影**

为 `InlineActivityItem` 增加可选 `parentId`、`depth`、`description` 和 `details`；维护 `feedById` 与首次出现顺序。description/details 只能由 kind、产品化 label、state、duration、公开 agent/stage/parallel/count 派生，不把原始 event/payload 存入 item。保留现有 `items` / `summary` 供聚合降级模式使用。

- [x] **Step 4: 运行模型测试**

Expected: 新旧模型断言全部通过。

### Task 2: 新增逐条活动 feed，并提取结果动作

**Files:**
- Create: `frontend/src/components/chat/AgentActivityFeed.tsx`
- Create: `frontend/src/components/chat/RunOutcome.tsx`
- Create: `frontend/src/components/chat/__tests__/AgentActivityFeed.test.tsx`
- Modify: `frontend/src/components/chat/InlineAgentActivity.tsx`

- [x] **Step 1: 写逐条渲染失败测试**

断言每个 `feed` item 对应独立 `[data-agent-activity-message]`，页面不存在总览 toggle；每个节点按钮有自己的 `aria-controls`，键盘展开后只显示该节点的安全详情。rerender terminal run 后相同 DOM key 对应的活动状态变成“已完成”，数量不变。三位专家必须是三个独立活动消息，失败/降级状态可见。

- [x] **Step 2: 实现 `AgentActivityFeed`**

使用语义 list 渲染每个活动。每条固定为状态图标、kind 图标、产品化 label、状态和可选耗时；`parentId/depth` 只控制短连接线和缩进。每条活动由稳定 ID 管理独立展开状态；运行中新节点默认展开，用户手动操作优先。运行中新 item 追加时用单独 live 文本播报，不让整个 feed 成为 live region。

- [x] **Step 3: 提取 `RunOutcome`**

从 `InlineAgentActivity` 原样迁移 checkpoint、conservative close、suggested actions、feedback/correction/distill 控件与回调。新旧活动模式共同复用，避免行为分叉。

- [x] **Step 4: 运行组件测试**

```bash
cd frontend
npm test -- --run \
  src/components/chat/__tests__/AgentActivityFeed.test.tsx \
  src/components/chat/__tests__/InlineAgentActivity.test.tsx
```

Expected: 新 feed 和旧聚合模式均通过。

### Task 3: 将活动消息与最终回答拆为兄弟节点

**Files:**
- Modify: `frontend/src/components/ChatWorkspace.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/vite-env.d.ts`
- Modify: `.env.example`
- Modify: `frontend/src/components/__tests__/ChatWorkspace.test.tsx`
- Modify: `frontend/src/components/__tests__/App.test.tsx`

- [x] **Step 1: 写三模式失败测试**

默认模式断言活动块不是 `.message-bubble` 的后代，最终 Markdown 位于其后的独立 assistant bubble；粒度开关 false 时恢复 `InlineAgentActivity`；内联开关 false 时不渲染 feed 并恢复旧过程栏。

- [x] **Step 2: 接入同轮兄弟节点结构**

在 assistant message 分支使用带稳定 key 的 Fragment：先渲染 `AgentActivityFeed`，再按内容存在性渲染最终 Markdown bubble，最后渲染 `RunOutcome`。回答尚为空时不得创建空白正文气泡。保持 autoscroll 依赖为活动数量/状态版本和正文长度等原始值。

- [x] **Step 3: 接入降级开关**

```ts
const granularAgentActivityEnabled =
  import.meta.env.VITE_GRANULAR_AGENT_ACTIVITY_ENABLED !== "false";
```

在 `.env.example` 记录默认 true、一级/二级回退顺序；不得加入真实 `.env`。

- [x] **Step 4: 运行集成测试**

```bash
cd frontend
npm test -- --run \
  src/components/chat/__tests__/inlineActivityModel.test.ts \
  src/components/chat/__tests__/AgentActivityFeed.test.tsx \
  src/components/chat/__tests__/InlineAgentActivity.test.tsx \
  src/components/__tests__/ChatWorkspace.test.tsx \
  src/components/__tests__/App.test.tsx \
  src/components/__tests__/agentStream.test.ts
```

Expected: 默认粒度模式、聚合降级模式、旧右栏模式全部通过。

### Task 4: 完成视觉实现与响应式验证

**Files:**
- Modify: `frontend/src/styles.css`

- [x] **Step 1: 实现逐条布局**

活动 feed 使用样板中独立白色活动块 + 左侧执行轨迹；每条最多 8px 圆角。详情区是同一活动块的展开区域，通过分隔线连接，不创建内嵌卡片。顶层与子活动最大只允许一层视觉缩进。

- [x] **Step 2: 添加移动端和 reduced-motion 样式**

在 390px 下将 meta 放到 label 下方，保证无横向溢出；spinner 在 reduced-motion 下静止。所有图标继续使用 Lucide。

- [x] **Step 3: 运行静态与完整前端验证**

```bash
cd frontend
npm audit --omit=dev
npm test
npm run build
```

Expected: audit 结果被记录；全部 Vitest 通过；TypeScript/Vite production build 通过。

### Task 5: 自动化路径 E2E、live/browser E2E 与记录

**Files:**
- Create: `plan/2026-07-26-granular-chat-agent-activity-progress.md`
- Modify: `plan/2026-07-26-granular-chat-agent-activity.md`
- Modify: `AGENTS.md`

- [x] **Step 1: 运行后端契约回归**

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_public_agent_events.py \
  tests/test_public_progress_e2e.py \
  tests/test_api_authorization_matrix.py \
  tests/test_harness_service.py::test_harness_emits_ordinary_tool_start_before_terminal_event \
  -q --no-cov
```

Expected: 公开 ID、live/history 投影和工具生命周期继续通过。

- [x] **Step 2: 自动化前端 E2E**

用流式 fixture 依次追加 route、tool start、delegate start、tool terminal、complete，然后 reload 历史。断言活动块逐条增加、terminal 原位更新、最终回答另起气泡、reload 顺序一致、敏感字段不可见。

- [ ] **Step 3: WSL live/runtime E2E**

在同一 Ubuntu 发行版中通过已运行的 `5173` / `9900` 服务发送一个非秘密 session id 的诊断请求，记录首个活动时间、每次工具/Agent 的 running -> terminal、最终内容时间、complete 状态和 history reload。至少验证一个成功工具和一个降级/失败活动；如外部依赖不健康，记录降级证据但不宣称 healthy live 通过。

- [ ] **Step 4: 浏览器 QA**

在 1280x720 和 390x844 验证：新活动逐条出现、无总卡折叠、最终回答单独显示、键盘焦点正常、无横向溢出、无相关 console error。分别截图默认粒度模式，并 smoke test 两级回退模式。执行期间遵循 `browser:control-in-app-browser` / `build-web-apps:frontend-testing-debugging` 技能。

- [x] **Step 5: 记录证据并更新状态**

进度文档记录命令、测试计数、session/request id、截图路径和 pass/fail。只有自动化路径 E2E、健康 live、桌面/移动浏览器 QA、完整前端测试与 build 全通过后，才把 AGENTS 索引标为“已完成并验证”。

## 8. 退出标准

1. route、plan、每次普通工具、每位专家、专家工具、verify 和 report 均可作为独立活动消息出现。
2. start/terminal 使用同一个活动块，状态原位更新且不重复、不重排。
3. 活动消息不被一个总览对话框或总折叠控件包裹；完成后保持可见，每个节点可独立、键盘可访问地展开详情。
4. 最终 Markdown 是活动序列之后的独立助手气泡；正文为空时无空白气泡。
5. live 与历史恢复产生相同活动顺序和状态。
6. 三位并行专家分别可见，且 390px 不横向挤压或逐字换行。
7. arguments/result/prompt/context/subtask/trace/span/evidence/usage/raw error 不进入模型或 DOM。
8. checkpoint、建议动作、反馈、纠正和蒸馏行为保持可用。
9. `VITE_GRANULAR_AGENT_ACTIVITY_ENABLED=false` 恢复当前聚合内联卡；`VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` 恢复旧右栏。
10. focused、完整前端、build、API 契约、自动化多步 E2E、健康 live 与双视口浏览器 QA 均有记录证据。

## 9. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 并行事件结束顺序导致 UI 跳动 | 以首次出现顺序固定 feed；terminal 只更新对象。 |
| 同一专家多次委派被错误合并 | ID 使用 parent public ID + expert + 必要的序号；测试覆盖同专家重复委派。 |
| 活动与最终回答拆开后 autoscroll 抖动 | effect 只依赖活动版本和正文长度；不订阅整个 runs map。 |
| live region 在高频事件下重复朗读 | 仅播报最新新增活动，不把动态列表整体标为 live。 |
| 反馈/蒸馏从旧组件拆出后回调错绑 | `RunOutcome` 明确接收当前 message/run id 回调；App/ChatWorkspace 集成测试覆盖。 |
| dirty worktree 与当前未提交主题重叠 | 只编辑本计划列出的前端文件；实施前逐文件检查 diff，不执行 `git add -A` 或重置。 |

回滚顺序：

1. 设置 `VITE_GRANULAR_AGENT_ACTIVITY_ENABLED=false` 并重启 Vite，恢复当前聚合式内联活动卡。
2. 如内联模式整体异常，再设置 `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` 并重启 Vite，恢复旧右侧过程栏。
3. 服务端公开事件投影、稳定 ID 和内部持久化不回滚；本任务无数据库迁移。

## 10. 批准与实施记录

- 2026-07-26：用户确认采用 `agent-activity-mockup.html` 样板，并补充“每个节点可以展开显示详细信息”。本计划已将原“节点无展开按钮”调整为“节点独立展开安全详情”；服务端隐私 allowlist 不变。
- 2026-07-26：实现完成，自动化、build、公开事件契约与默认模式双视口 mock browser QA 通过。认证 live 与两级回退 browser smoke 未完成，详见进度文档，因此索引不标记“全部验证完成”。
