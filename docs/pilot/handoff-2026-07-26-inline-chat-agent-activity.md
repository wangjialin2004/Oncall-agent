# OnCall Agent 交接文档 — 对话内实时工具 / 专家过程流

> **交接日期**：2026-07-26  
> **工作树**：`codex/publish-current-worktree`  
> **当前 HEAD**：`c4aabef`；本功能及其他主题改动仍在 dirty worktree，尚未按主题提交  
> **本棒主题**：将路由、工具调用和专家委派过程移入助手消息；解决正文生成前的空白等待；在服务端建立浏览器公开事件边界  
> **产品状态**：实现完成；自动化、生产构建和一次健康 live E2E 通过；登录后浏览器视觉验收与多专家健康 live 收口待补  
> **目标读者**：接手登录后 UI 验收、历史 Harness 回归分流、主题化提交与旧右栏后续退役的工程同学  
> **计划 / 进度**：[实施计划](../../plan/2026-07-26-inline-chat-agent-activity.md) · [进度证据](../../plan/2026-07-26-inline-chat-agent-activity-progress.md)  
> **相关运行时交接**：[统一上下文 live canary](./handoff-2026-07-23-unified-context-runtime-canary.md)

---

## 0. 30 秒结论

| 项 | 状态 |
|---|---|
| 默认聊天体验 | ✅ 工具 / 专家过程显示在对应助手消息内，最终正文位于过程下方 |
| 空白等待 | ✅ 创建空助手消息时立即显示“正在准备排查”，首个 SSE 到达后更新真实阶段 |
| 普通工具生命周期 | ✅ 执行前 `tool_start`，执行后合并 terminal `tool_event` |
| 并行专家布局 | ✅ 2 / 3 / 8 位专家均为纵向分支，不再横向挤压成窄卡片 |
| 浏览器数据边界 | ✅ 参数、原始结果、prompt/context、trace/span/evidence、usage、raw error 均在服务端过滤 |
| 历史恢复 | ✅ 与 live SSE 使用同一公开投影；内部持久化仍保留原事件 |
| 完成 / 失败状态 | ✅ 完成后自动折叠；失败或取消保持展开并显示正确摘要 |
| 旧右栏回滚 | ✅ `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false`，重启 Vite 后恢复 |
| 自动化 | ✅ 后端 20 focused；前端 102 tests；Ruff / TypeScript / Vite build 通过 |
| Live | ✅ 一次完整闭环；⚠️ 第二次多专家请求遇到 Prometheus 502 / chunk 中断 |
| Browser QA | ⚠️ 登录页可达；无当前登录会话，未绕过认证；登录后桌面 / 移动验收待补 |
| 工作树 | ⚠️ 混有上下文、旧过程栏和本功能改动；禁止 `reset --hard` / `git add -A` |

**接手人一句话**：功能已经可用并运行在 `http://localhost:5173/`；下一棒先用真实账号完成登录后的双尺寸视觉验收，再跑一次健康多专家 live，最后按本文文件边界拆主题提交。

---

## 1. 用户问题与最终交互

原独立右侧“并行专家”卡片在有限宽度内横向并排，中文被压成逐字换行；同时聊天正文在取证完成前为空，用户会误以为系统没有响应。

现在的默认流程：

```text
用户消息
助手消息
  正在排查 · 已进行 8 秒
  ├─ 已识别为综合诊断
  ├─ 已制定排查计划
  ├─ 并行调用 3 位专家
  │  ├─ 指标专家 · 进行中
  │  ├─ 日志专家 · 已完成
  │  └─ 变更专家 · 已降级继续
  └─ 正在核对证据

  最终回答开始流式出现……
```

- 过程与答案属于同一条助手消息，不再依赖用户选择右侧回合。
- 运行时默认展开；完成后折叠成步数、专家数和工具调用数摘要。
- 专家分支始终单列纵向展示；专家内部工具可作为下一层子行。
- 未知工具或专家使用“执行检查”/“领域专家”，不把内部函数名直接放进 UI。
- 反馈、纠正、经验草稿和建议动作保留在对应消息底部。

---

## 2. 架构与事件流

```text
Harness internal event
  -> API request-scoped PublicEventProjector
       allowlist fields
       raw call id -> activity-N
       nested complete.events projection
  -> SSE /api/assistant
  -> frontend defensive sanitizer
  -> AgentRun.events
  -> buildInlineActivityModel()
  -> InlineAgentActivity + streamed Markdown

Internal complete event
  -> original event persisted
  -> GET /api/conversations/{id}
  -> same PublicEventProjector
  -> restored inline activity
```

### 2.1 兼容约束

- 保留 `route_event`、`agent_event`、`tool_event`、`decision_event` 的 `type` 语义。
- 普通工具开始使用 `agent_event(stage="tool_start", status="in_progress")`，不新增一个会污染工具终态指标的 `tool_event`。
- `delegate_start` / `delegate_parallel_start` 沿用现有事件；start、terminal、child 通过同一请求内的 opaque activity ID 合并。
- `complete.events` 会与已实时收到的事件重复，前端按公开 activity ID 和阶段去重。

### 2.2 数据最小化边界

浏览器允许看到：

- 事件类型、agent/stage/status、产品化工具名、route、耗时；
- 专家角色、并行状态、每个专家的 completed / failed / degraded 状态；
- opaque `activity-N` 及父子关系；
- 用户本来就应看到的最终回答、澄清、建议动作和经验草稿状态。

浏览器禁止收到：

- 工具 `arguments` / raw `result`；
- expert `subtask`、prompt、context、模型草稿；
- trace/span/evidence ID、usage、原始调用 ID；
- 异常堆栈、内部 URL、查询参数和 raw error。

**注意**：关闭 inline UI 只恢复旧布局，不关闭服务端公开投影。公开投影是安全边界，不是视觉开关。

---

## 3. 关键文件地图

| 区域 | 路径 | 接手关注 |
|---|---|---|
| 公开事件投影 | `app/agent/public_events.py` | allowlist、嵌套 complete/history、opaque activity ID |
| SSE 边界 | `app/api/assistant.py` | 对外投影；内部 complete 原样提交/持久化 |
| 历史边界 | `app/api/conversations.py` | 每个 turn 独立 projector，恢复关联 ID |
| 工具首事件 | `app/agent/harness/tools_runtime.py` | ordinary `tool_start` 无 arguments |
| 前端流解析 | `frontend/src/api/agentStream.ts` | defensive payload allowlist |
| 活动 reducer | `frontend/src/components/chat/inlineActivityModel.ts` | 去重、纵向专家、产品标签、摘要 |
| 活动 UI | `frontend/src/components/chat/InlineAgentActivity.tsx` | 展开策略、状态、反馈/建议动作 |
| 消息集成 | `frontend/src/components/ChatWorkspace.tsx` | 空正文也显示活动；原语依赖自动滚动 |
| 布局/开关 | `frontend/src/App.tsx`、`AppShell.tsx` | inline 默认；legacy panel 回退 |
| 样式 | `frontend/src/styles.css` | 单列 rail、移动端、44 px、reduced-motion |
| 后端测试 | `tests/test_public_agent_events.py`、`tests/test_public_progress_e2e.py` | live/complete/history 私密字段断言 |
| 前端测试 | `frontend/src/components/chat/__tests__/` | 2/3/8 专家、失败状态、键盘和隐私 |

---

## 4. 开关与回滚

`.env.example`：

```text
# VITE_INLINE_AGENT_ACTIVITY_ENABLED=true
```

默认未设置即启用 inline。恢复旧右侧过程栏：

```bash
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
VITE_INLINE_AGENT_ACTIVITY_ENABLED=false npm run dev -- --host 0.0.0.0 --port 5173
```

如果由 `super-biz-vite` user service 管理，修改其环境后重启服务；不要同时再拉一个 5174/5175 的 Vite 副本。

回滚只改变布局：

1. 旧 `AgentProcessPanel`、splitter 和 resizable width 代码仍保留。
2. 服务端公开投影继续启用。
3. 无 schema 迁移，无历史事件重写，无数据回滚。

---

## 5. 验证证据（2026-07-26）

### 5.1 Frontend

```bash
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
cd frontend
npm test
npm run build
```

- Vitest：`15 passed` files，`102 passed` tests。
- TypeScript + Vite production build：通过，1838 modules transformed。
- Flag on/off、空正文实时过程、历史恢复、反馈/HITL、2/3/8 专家、失败/取消摘要均有覆盖。
- 仍有一个既有 `httpClient.ts` 动/静态 import chunk warning，不是构建失败。

### 5.2 Backend focused + automated E2E

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_public_agent_events.py \
  tests/test_public_progress_e2e.py \
  tests/test_api_authorization_matrix.py \
  tests/test_harness_service.py::test_harness_emits_ordinary_tool_start_before_terminal_event \
  -q --no-cov

.venv/bin/ruff check \
  app/agent/public_events.py app/api/assistant.py app/api/conversations.py \
  app/agent/harness/tools_runtime.py \
  tests/test_public_agent_events.py tests/test_public_progress_e2e.py
```

- Pytest：`20 passed in 0.78s`。
- Ruff：`All checks passed`。
- 自动化 E2E：authenticated API → SSE → original internal persistence → public history reload。
- Harness 单文件上限：`stream_inner.py` 992 行；`tools_runtime.py` 699 行。

### 5.3 Live/runtime

健康 session：`inline-activity-e2e-1785073504`

| 指标 | 结果 |
|---|---:|
| 首个公开过程事件 | 0.053 s |
| complete | 27.788 s |
| stream event | 151 |
| history turn / public timeline | 1 / 18 |
| live 私密字段扫描 | 空 |
| history 私密字段扫描 | 空 |

事件覆盖 route、context、plan、delegate start/terminal、content、verify、report、complete。

第二次 session `inline-activity-final-1785073934` 进入指标/日志/变更工具路径，但 Prometheus 返回 502，随后 93.4 秒处 chunk 中断；该次不计为通过。API 已重启。

### 5.4 当前运行快照（时间性证据）

| 服务 | 地址 / PID | 状态 |
|---|---|---|
| Vite | `http://localhost:5173/` / 7227 | listening |
| FastAPI | `http://localhost:9900/` / 124743 | listening |
| readiness | `/health/readiness` | `200 ready`，issues=[] |

进程状态会漂移；接手时重新执行：

```bash
ss -ltnp | grep -E ':5173|:9900'
curl --max-time 10 -fsS http://127.0.0.1:9900/health/readiness
```

---

## 6. 未完成项与已知风险

### P0：登录后浏览器 QA

In-app Browser 已验证登录页可达；390×844 下 `scrollWidth=390`，无 console warning/error。当前没有可用登录会话，配置账号也不是仓库默认账号，因此没有绕过认证或注入 token。

仍需在 1280×720 与 390×844 验证：

- 发送诊断后，过程先于最终正文出现；
- 3 位专家纵向分支无逐字换行、无横向溢出；
- complete 后自动折叠，键盘可展开；
- 历史会话恢复同一摘要；
- `VITE_INLINE_AGENT_ACTIVITY_ENABLED=false` 的旧右栏 smoke；
- 截图和 console 证据。

### P0：健康多专家 live

第一次健康请求只完成一个 delegated expert；第二次确实进入多工具/专家路径，但 Prometheus 502 后 chunk 中断。需要在 monitor/Prometheus 健康时补一条完整多专家请求，不能用前端 fixture 替代 live 语义验收。

### P1：历史 Harness 回归

较宽命令结果：`83 passed, 10 failed`（约 166 秒）。失败集中在：

- checkpoint next-step / forced conservative close；
- missing metric / delayed clarification；
- soft / forced delegation；
- large log pipeline；
- re-evidence on/off fixture。

这些测试受当前 dirty worktree 中动态 plan、checkpoint 和 unified context 改动影响。本功能不改变上述策略，也未把 10 项标成已解决。接手人应先按主题分流，不要为“变绿”回退其他人的上下文工作。

### P1：工作树归属

当前 dirty worktree 同时包含：

- 统一上下文 / dynamic plan / whiteboard 工具改动；
- 07-23 旧并行专家过程栏改动；
- 本次 inline activity 与 public event boundary；
- 计划、进度、交接文档。

提交时只按明确文件/patch 分组，禁止 `git add -A`，禁止覆盖或回退不属于本功能的变化。

---

## 7. 下一棒 SOP

1. **重新确认运行状态**
   - `ss` 检查 5173 / 9900；readiness 必须为 ready。
   - 非交互 WSL 前端命令先 source `~/.nvm/nvm.sh`，避免误用 Windows Node。

2. **用户正常登录**
   - 使用用户提供或已登录的真实会话。
   - 不读取/打印 `.env` 密码，不把 token 写进脚本、文档或截图。

3. **完成双尺寸 Browser QA**
   - 桌面 1280×720、移动 390×844。
   - 记录首过程、专家分支、complete 折叠、历史恢复和 rollback 截图。

4. **补健康多专家 live**
   - 先确认 Prometheus/Monitor MCP 健康。
   - 使用非秘密 session ID；记录 first progress / first content / complete。
   - 扫描 live SSE 与 history 的禁止字段集合。

5. **分流宽回归**
   - 先复现 10 项并标注由哪个未提交主题影响。
   - 本功能 focused 20 项必须持续全绿。

6. **按主题提交**
   - public event boundary + server tests；
   - inline React/UI + frontend tests；
   - plan/progress/handoff/index。
   - 排除 `.env`、`server.log`、pid、`.vite/`、数据库和运行产物。

---

## 8. 红线

- 不向 UI 暴露工具参数、原始结果、expert subtask、prompt/context、trace/span/evidence ID 或 raw error。
- 不为兼容旧右栏关闭服务端公开投影。
- 不改变 route、工具选择、并行策略、证据判断或 checkpoint 策略来修 UI 测试。
- 不把 `tool_start` 改成第二个 terminal-style `tool_event`，避免观测计数翻倍。
- 不绕过登录、弱化鉴权/CORS/owner scope，也不记录真实账号、密码或 token。
- 不删除旧面板源码；正式退役需另立计划。
- 不用 Windows Node 处理 WSL `node_modules`。
- 不执行 `git reset --hard`、整树 checkout 或 `git add -A`。

---

## 9. 交接检查清单

- [x] 服务端公开事件投影
- [x] live SSE / nested complete / history 同一投影
- [x] 普通工具执行前 lifecycle marker
- [x] 对话内 route / plan / tool / expert / verify / report
- [x] 2 / 3 / 8 专家纵向分支测试
- [x] complete 折叠、失败/取消展开、键盘和 reduced-motion
- [x] feedback / distill / suggested actions 迁入对应消息
- [x] legacy panel 环境变量回滚
- [x] backend focused + automated E2E
- [x] frontend full tests + production build
- [x] 一次健康 live stream + history 隐私扫描
- [x] 服务重启并恢复 readiness
- [x] 本交接文档与索引
- [ ] 登录后桌面 / 移动 Browser QA + 截图
- [ ] 健康 3 专家 live complete
- [ ] 10 项历史 Harness 失败分流
- [ ] 按主题提交 / 推送（按用户要求）

---

## 10. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-23 | 旧右侧并行专家卡片按专家拆分；现作为 legacy rollback 基线 |
| 2026-07-26 | 新增服务端公开事件边界和普通工具 start marker |
| 2026-07-26 | 默认切换为助手消息内单列活动流，旧右栏由 Vite flag 回退 |
| 2026-07-26 | backend 20 / frontend 102 / build / 一次 live E2E 通过 |
| 2026-07-26 | 记录浏览器认证阻塞、第二次 live 降级与历史宽回归缺口 |
| 2026-07-26 | 形成本交接文档作为当前 UI / 公开事件契约接手入口 |
