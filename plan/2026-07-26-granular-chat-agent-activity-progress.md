# 对话内逐条工具 / Agent 活动消息进度

**日期：** 2026-07-26  
**状态：** 实现完成；自动化、生产构建、公开事件契约和默认模式双视口 mock browser QA 通过。认证 live 请求与两级回退的浏览器 smoke 尚未补齐，因此不标记“全部验证完成”。

## 实现结果

- `buildInlineActivityModel` 在保留旧树形 `items` 的同时新增稳定、有序的扁平 `feed`；start / terminal 以公开 activity id 原位合并，不重复或重排。
- `AgentActivityFeed` 将 route、plan、tool、Agent 调度、每位专家、证据检查和 report 渲染为独立节点。每个节点有自己的展开状态、`aria-expanded` / `aria-controls` 和安全详情。
- 运行中节点默认展开；用户手动切换后状态更新不会覆盖选择。最终 Markdown 气泡与活动 feed 是同轮兄弟节点，空正文时不渲染空助手气泡。
- `RunOutcome` 复用 checkpoint、建议动作、反馈、纠正与蒸馏行为；旧聚合卡没有复制一套结果逻辑。
- 新开关 `VITE_GRANULAR_AGENT_ACTIVITY_ENABLED` 默认 true；false 恢复聚合内联卡。`VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` 继续恢复旧右栏。
- 详情只由 kind、产品化 label、状态、耗时、公开 Agent 归属和数量派生；未知工具降级为“执行检查”，不进入原始 payload。

## 自动化证据

### 前端 focused

```bash
source ~/.nvm/nvm.sh
cd frontend
npm test -- --run \
  src/components/__tests__/App.test.tsx \
  src/components/__tests__/ChatWorkspace.test.tsx \
  src/components/chat/__tests__/AgentActivityFeed.test.tsx \
  src/components/chat/__tests__/InlineAgentActivity.test.tsx \
  src/components/chat/__tests__/inlineActivityModel.test.ts
```

结果：`5 passed` files，`32 passed` tests。

覆盖：稳定顺序、terminal 原位更新、独立展开、安全详情、活动/最终回答分离、空正文、粒度开关回退和旧聚合模式。

历史恢复增量：

```bash
npm test -- --run src/components/__tests__/conversationHistory.test.tsx
```

结果：`1 passed` file，`4 passed` tests。历史 turn 的公开 events 恢复为逐条活动，最终回答仍是独立气泡。

### 前端全量与生产构建

```bash
npm test
npm run build
```

- Vitest：`16 passed` files，`109 passed` tests。
- TypeScript + Vite：通过；`1840 modules transformed`。
- 仅保留既有 `httpClient.ts` 动态/静态 import chunk warning；不影响构建。

`npm audit --omit=dev` 已执行，但 registry audit endpoint 返回错误，未得到漏洞清单；未自动升级或改写依赖。

### 后端公开事件与 API 路径

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_public_agent_events.py \
  tests/test_public_progress_e2e.py \
  tests/test_api_authorization_matrix.py \
  tests/test_harness_service.py::test_harness_emits_ordinary_tool_start_before_terminal_event \
  -q --no-cov
```

结果：`20 passed in 0.84s`。覆盖 authenticated API -> SSE/public projection -> persistence/history、公开 ID、敏感字段过滤和普通工具 start/terminal 生命周期。

## Browser QA

使用运行中的真实 React/Vite 页面 `http://127.0.0.1:5173/`，通过 Playwright 对认证和公开 SSE 响应做本地 mock；未绕过真实后端认证，也未读取或记录账号密码。QA session id：`granular-qa-session`。

公开 SSE fixture 依次包含 route、plan、tool start/terminal、3 位专家 start/terminal、degraded re-evidence、report、content 和 complete。最终页面结果：

- 9 个独立活动节点；工具节点原位显示 `已完成 · 920 毫秒`。
- 3 位专家分别显示；变更专家与补证节点显示“已降级继续”。
- “查询指标”单独展开后只显示用途、节点类型、执行状态和耗时；其他节点保持收起。
- 最终 Markdown 回答在活动 feed 之后，且不是活动节点或活动 feed 的后代。
- 1280x720 桌面快照：`C:/Users/wangjialin/.codex/visualizations/2026/07/26/019f9ef2-c332-71b0-a9d5-f0a0f73720b0/granular-agent-activity-desktop.png`。
- 390x844 移动快照：`C:/Users/wangjialin/.codex/visualizations/2026/07/26/019f9ef2-c332-71b0-a9d5-f0a0f73720b0/granular-agent-activity-mobile.png`。
- 移动端测量：document `scrollWidth=390`，messages `clientWidth=390 / scrollWidth=390`，9 个活动块均在视口内，展开详情数为 1。
- QA 完成后的 Playwright console 查询：0 errors，0 warnings。

## 未完成与边界

- 本次没有真实登录凭据，因此没有重跑 authenticated live LLM 请求；上一版 inline 基础能力已有一次健康 live，但不能代替本次粒度化 UI 的认证 live 证据。
- 两级回退已有 App/ChatWorkspace 自动化覆盖；尚未分别重启 Vite 做浏览器 smoke。
- npm registry audit endpoint 不可用，依赖审计结果待网络恢复后重跑。

## 偏差与回滚

- 计划写“带稳定 key 的 Fragment”；实现使用稳定 key 的 `.assistant-turn` section，以便建立可约束宽度和结果区间距，语义与兄弟节点目标一致。
- 未修改后端 SSE、Harness、持久化或数据 schema。
- 一级回滚：`VITE_GRANULAR_AGENT_ACTIVITY_ENABLED=false`；二级回滚：`VITE_INLINE_AGENT_ACTIVITY_ENABLED=false`。两者均需重启 Vite。
