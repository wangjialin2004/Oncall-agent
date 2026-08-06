# 对话内实时工具 / 专家过程流 Progress

> **交接入口**：[对话内实时工具 / 专家过程流交接](../docs/pilot/handoff-2026-07-26-inline-chat-agent-activity.md)

## 状态

实现已完成。自动化 API→SSE→持久化→历史恢复、完整前端回归、生产构建和一次健康 live 请求已通过；登录后浏览器视觉验收仍受当前 in-app Browser 无登录会话限制，因此计划不标记为“全部验证完成”。

## 已实现

- `PublicEventProjector` 在 SSE 单事件、`complete.events` 和历史会话响应处执行同一 allowlist 投影；内部持久化仍保留原事件。
- 原始工具/父调用 ID 被替换为每次请求内稳定的 `activity-N`，参数、原始结果、prompt/context、trace/span/evidence、usage 和原始 error 不出浏览器边界。
- 普通工具在执行前发送 `agent_event(stage="tool_start")`；专家委派继续使用原有 start 事件，避免把终态 `tool_event` 指标重复计数。
- 助手消息在正文为空时立即显示运行过程；route、plan、tool、expert、verify、report 归并为单列活动流。
- 2/3/8 位并行专家均显示为父活动下的纵向分支；未知工具/专家只显示产品化兜底名称。
- 完成后默认折叠，失败/取消保持展开并显示正确摘要；支持键盘切换、44 px 控件和 reduced-motion。
- 默认移除聊天右侧过程栏；`VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` 可恢复旧布局，服务基线帮助栏不受影响。
- 反馈、纠正、经验草稿和建议动作改为每条助手消息自己的紧凑控制区，回调使用对应 run ID。

## 实施偏差

1. 普通工具 start 使用 `agent_event` 而非 `tool_event`，保护现有工具终态计数和观测指标。
2. 公开 activity ID 由请求级 projector 生成，保证 start、terminal、nested complete 与历史投影可合并。
3. 新 reducer 使用独立安全名称 allowlist，不导入旧过程栏会读取 raw payload 的详情模块；这避免新组件重新获得参数/结果访问面。
4. `app/config.py` 未增加设置。回滚项是 Vite 构建环境变量，只记录在 `.env.example`，避免建立一个不会控制前端构建的后端同名配置。

## 验证证据

### Frontend

```bash
source ~/.nvm/nvm.sh
cd frontend
npm test
npm run build
```

- Vitest: `15 passed` files, `102 passed` tests.
- Vite/TypeScript production build: passed, 1838 modules transformed.
- 已有 `httpClient.ts` 动/静态混用 chunk warning 仍存在，不是本次新增失败。

### Backend focused and contract E2E

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_public_agent_events.py \
  tests/test_public_progress_e2e.py \
  tests/test_api_authorization_matrix.py \
  tests/test_harness_service.py::test_harness_emits_ordinary_tool_start_before_terminal_event \
  -q --no-cov
```

- Result: `20 passed in 0.78s`.
- Automated path covers authenticated `/api/assistant` event source → public stream → original internal persistence → `/api/conversations/{id}` public reload.
- Ruff on changed production files and new server tests: `All checks passed`.
- Harness file limit: `stream_inner.py` 992 lines, `tools_runtime.py` 699 lines; both remain at or below 1000.

### Wider historical harness regression

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_public_agent_events.py tests/test_public_progress_e2e.py \
  tests/test_harness_service.py tests/test_m1_close_the_loop.py \
  tests/test_api_authorization_matrix.py -q --no-cov
```

- Result: `83 passed, 10 failed` in about 166 seconds.
- Failures remain concentrated in existing dynamic-plan/checkpoint call-count expectations, soft/forced delegation expectations, and re-evidence fixtures; tenant-scoped RAG also reached unavailable live dependencies in those fixtures.
- This task does not mark those 10 failures resolved and does not change router, checkpoint, delegation policy or re-evidence semantics.

### Live/runtime E2E

Healthy session: `inline-activity-e2e-1785073504`

- first public activity: `0.053s`;
- complete: `27.788s`;
- stream: 151 events, including route, context, plan, delegate start/terminal, streamed content, verify and report;
- history: 1 persisted turn, 18 public timeline events;
- forbidden-key scan on live stream and history: empty for arguments/result/subtask/prompt/context/trace/span/evidence/usage.

A second timing probe, `inline-activity-final-1785073934`, reached the three expert/tool path but Prometheus returned 502 and the server closed the chunked response after 93.4 seconds. This degraded run is not counted as a pass. The API was restarted as PID `124743`; final readiness returned `200 ready` and both `5173`/`9900` were listening.

### Browser QA

- In-app Browser reached `http://localhost:5173/` and rendered the login page without a framework error overlay.
- At 390×844, the login entry had `scrollWidth=390`, matched the viewport, and produced no captured warning/error console logs; viewport override was reset afterward.
- No authenticated user tab/session was available, and the configured account is not the repository default account.
- Authentication was not bypassed and no token/password was exposed. Consequently, desktop/mobile screenshots, logged-in keyboard interaction and horizontal-overflow inspection for the new chat activity remain pending.

## Remaining acceptance work

1. Sign in through the in-app Browser, then validate the live inline feed at 1280×720 and 390×844, including screenshots and keyboard collapse/expand.
2. Run one healthy live request that actually completes multiple expert branches; the first healthy request completed one delegated expert, while the multi-expert retry degraded on upstream dependencies.
3. Triage or refresh the 10 historical Harness assertions separately before claiming the repository-wide focused suite is green.

## Rollback

Set `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` and restart Vite. This restores the legacy right process panel. The server-side public projection remains enabled as the security/data-minimization boundary; no database rollback is needed.
