# OnCall Agent 系统 Harness 化计划书（统一编排循环）

> **角色边界**：按本项目 CLAUDE.md，本计划只描述「怎么做」。真正改动业务代码需要你**明确授权解除限制**后才进入实现阶段。

---

## Context（为什么做）

**现状架构**（live 路径 `/api/assistant`）：

```
/api/assistant (SSE)  →  RouterService.stream()
   ├─ 关键词快路 + LLM 语义分类 → 选「1 个」专家路由
   └─ get_expert(route).run()  → ToolCallingExpert：最多 3 轮工具循环 → 流式事件
```

- 路由器一次只命中 **1 个专家**（[router_service.py](app/services/router_service.py)），命中后该专家独立跑完，**无法跨专家协作 / 二次重路由 / 升级**。
- 每个专家是「系统提示 + 受限工具集 + 通用工具循环」，循环**写死最多 3 轮**（[base.py:30,139](app/agent/experts/base.py#L139)），轮次耗尽后强制无工具收尾。
- 循环**只吃当前这条 message**（[base.py:109-135](app/agent/experts/base.py#L109)），**不读会话历史**——实质单轮。历史虽已持久化（[conversation_service.py](app/services/conversation_service.py)）却未回灌进 agent。
- **无规划 / 无自校验 / 无子 agent 委派**；诊断专家也已退化为「更宽工具集的普通专家」（[diagnosis.py](app/agent/experts/diagnosis.py)）。
- 已有较好的可观测基座：归一化事件 route/agent/tool/decision/content/complete/error（[events.py](app/agent/events.py)）。

**目标形态（已确认）**：把「路由只选 1 个专家」收敛为**一个统一的主 agent 编排循环（Claude Code 式 harness）**——专家与工具变成主循环可**动态委派**的子能力，支持 **规划 → 迭代取证 → 自校验**，并补齐四类能力：

1. **多轮上下文 / 长循环**（去掉写死 3 轮，喂入会话历史，智能停止）
2. **规划 + 自校验**（先 todo 规划，定稿前做一次证据自检）
3. **子 agent 委派 / 多专家协作**（主循环把子任务派给多个专家并合并）
4. **守护 / 预算 / 可观测**（工具权限、Token/时间预算、重试降级、统一 trace/replay）

**关键约束（贯穿全程）**：
- **不破坏前端契约**：`/api/assistant` 出入参与 SSE 事件形状保持兼容。已确认前端 [agentStream.ts:67-69](frontend/src/api/agentStream.ts#L67-L69) 对未知 `type` 返回 `null` 直接丢弃 → **新增事件类型不会让前端报错**；但要在 UI 可见，必须复用**现有可渲染类型**（`agent_event` / `tool_event` / `decision_event`）。
- **Feature Flag + 软移除**：harness 走新开关，`RouterService` 旧路径**原样保留**可秒回滚（延续上一轮「简化设计」的软移除哲学，见 [router-experts-simplification-design.md](docs/superpowers/specs/2026-06-17-router-experts-simplification-design.md)）。
- **只读、不做自动处置**（延续既有非目标：不新增机器变更类动作）。

---

## 目标架构

```
/api/assistant (SSE，契约不变)
  → if config.harness_enabled:  HarnessService.stream(message, session_id, owner_key)
  └ else:                       RouterService.stream(...)        # 旧路径保留，回滚开关

HarnessService.stream:
  1. ContextBuilder  ── 系统策略 + 多轮历史(get_turns) + 用户偏好 + 工具目录
  2. (可选) RouterService 分类 → 仅作「focus 提示」喂给首个规划，不再做终态分派
  3. 主循环 (受 步数/Token/时间 预算约束):
       ├─ PLAN     规划 todo（复杂/跨域时）          → emit agent_event(stage=plan) / decision_event
       ├─ LLM 决策 答复 | 调工具 | 委派子 agent
       ├─ 工具执行 经 Guarded ToolExecutor（超时/重试/权限/大输出后处理/错误隔离）
       ├─ 委派     delegate_to_expert(subtask) → 子 agent 跑同一循环内核，回结构化结果 → 合并入上下文
       ├─ 预算/上下文 管理（近预算上限 → 压缩历史/大工具输出）
       └─ 停止条件 模型判定完成 ∨ 步数上限 ∨ 预算耗尽 ∨ 自校验通过
       └─ VERIFY   定稿前自检（证据是否支撑结论/缺口）→ 触发补取证或标注置信度
  4. 持久化本轮（conversation_service.append_turn，契约不变）
  → 归一化事件流（现有事件类型 + 复用类型表达 plan/delegate/verify）
```

子 agent = 现有 5 个专家（knowledge/metric/log/change/diagnosis）改造为「可被主循环委派的技能」，**共享同一循环内核**；`RouterService` 的分类逻辑保留但**降级为提示**，不再是唯一分派决策。

---

## 关键设计决策与复用清单

**最大化复用现有实现，避免另起炉灶：**

| 复用项 | 位置 | 用途 |
|---|---|---|
| 工具循环（多轮 tool-calling） | [base.py:109-234](app/agent/experts/base.py#L109) `ToolCallingExpert.run` | 抽取/泛化为 harness `loop.py` 内核 |
| LLM 客户端（已含重试/退避） | [llm_client.py](app/core/llm_client.py) `LLMClient` | 直接复用，不重写 provider 层 |
| 工具执行 + 定义转换 | [tool_calling.py](app/core/tool_calling.py) `execute_tool_calls`/`tool_to_definition` | guarded executor 在其上包一层守护 |
| MCP best-effort 加载 | [base.py:242-275](app/agent/experts/base.py#L242) `collect_tools` | 统一工具注册表的 MCP 装载 |
| 大日志确定性预处理 | [log_pipeline.py](app/agent/experts/log_pipeline.py) `analyze_logs` | 升级为注册表级「大输出后处理」 |
| 归一化事件 builder | [events.py](app/agent/events.py) | 新步骤复用 `make_agent_event`/`make_decision_event`/`make_tool_event` |
| 多轮历史读取 | [conversation_service.py:114](app/services/conversation_service.py#L114) `get_turns` | Phase 2 直接回灌历史（**无需新建持久化**） |
| 用户偏好上下文 | [user_preference_service.py](app/services/user_preference_service.py) `format_for_prompt` | ContextBuilder 拼接 |
| Token 估算 / usage 累计 | [base.py:33-68](app/agent/experts/base.py#L33) `estimate_tokens`/`_merge_usage` | 预算核算 |
| 超时/降级回退范式 | [router_service.py:266-315](app/services/router_service.py#L266) | harness 降级答复沿用 |

**事件契约策略（重要）**：plan/delegate/verify 等新步骤**优先用现有可渲染类型表达**——
- 规划/委派/校验 → `agent_event`（新增 `stage`：`plan`/`delegate`/`verify`）或 `decision_event`；
- 子 agent 的 tool/agent 事件挂在 `span_id` 下嵌套；
- 这样**核心阶段前端零改动**即可显示；更精细的「计划面板/委派树」UI 作为可选后续（前端 [AgentProcessPanel.tsx](frontend/src/components/AgentProcessPanel.tsx) 增量映射）。

---

## 实施阶段

> 每个阶段都在 `harness_enabled=false` 后面增量推进、**可独立验证**；旧路径全程可用。

### Phase 0 — 设计冻结与契约对齐（不改行为）
- **关键任务**：冻结 harness 接口 / 事件契约（additive 清单）/ 预算与守护配置项 / 子 agent 契约；确定 `harness_enabled` 及各能力子开关命名与默认值。
- **涉及文件**：本计划副本 `plan/` + 一份接口契约说明（可并入 `docs/superpowers/specs/`）。
- **验证**：评审通过，无代码行为变化。

### Phase 1 — Harness 内核骨架（开关默认关）
- **关键任务**：新建 `app/agent/harness/`，把 [base.py](app/agent/experts/base.py) 的循环泛化为 `loop.py`；`context.py`（系统策略+偏好+工具目录）、`state.py`（messages/todos/usage/trace/预算计数）。`/api/assistant` 加 `harness_enabled` 分支（默认走旧路径）。
- **涉及文件**：新增 `app/agent/harness/{__init__,loop,context,state}.py`；改 [app/api/assistant.py](app/api/assistant.py)、[app/config.py](app/config.py)。
- **验证**：开关开启时，harness 能用「全量本地工具」答完单轮请求并流出正确事件序列；开关关闭时行为与现状逐字节一致。

### Phase 2 — 多轮上下文 + 长循环停止条件（能力①）
- **关键任务**：ContextBuilder 用 `get_turns` 回灌历史；以「模型判定完成 ∨ 步数上限 ∨ Token/时间预算 ∨ 无进展检测」替换写死 3 轮；近预算上限时压缩旧轮次/大工具输出（复用 `analyze_logs` 思路）；预算耗尽强制无工具收尾（沿用 [base.py:195-199](app/agent/experts/base.py#L195)）。
- **涉及文件**：`harness/{context,loop,budget}.py`；`app/config.py`（预算项）。
- **验证**：多轮会话能引用上文；构造长任务确认按预算/步数收敛、不超窗。

### Phase 3 — 统一工具注册表 + 守护与预算（能力④）
- **关键任务**：`registry.py` 把本地工具 + MCP（cls/monitor）统一为带元数据（域/成本/权限类）的目录（MCP 装载复用 `collect_tools`）；`tool_executor.py` 在 `execute_tool_calls` 上加 单工具超时 / 重试退避 / 权限 allowlist / 大输出后处理 / 错误隔离；工具级 Token 计入预算。
- **涉及文件**：新增 `harness/{registry,tool_executor}.py`；`app/config.py`（守护项）。
- **验证**：制造工具超时/异常/超大日志，确认降级、隔离、压缩生效；越权工具被拦截。

### Phase 4 — 专家转子 agent + 委派（能力③）
- **关键任务**：定义子 agent 契约 `SubAgent.run(subtask, context) → (events, structured_result)`；5 个专家改为共享内核的子 agent 配置（保留原 `run` 供旧路径）；主循环新增 `delegate_to_expert` 能力（**先串行**，并行委派列为后续）；`RouterService` 分类抽成可复用「focus 提示」函数喂首个规划。
- **涉及文件**：新增 `harness/subagent.py`；改 [app/agent/experts/*.py](app/agent/experts/)、[experts/base.py](app/agent/experts/base.py)、[router_service.py](app/services/router_service.py)。
- **验证**：跨域问题下主循环委派 ≥2 个专家并合并；事件时间线含委派节点且可追踪。

### Phase 5 — 规划 + 自校验（能力②）
- **关键任务**：循环起始按「复杂/跨域」启发或 router 提示触发轻量 todo 规划（emit plan 事件，随步骤更新）；定稿前做证据自检（每条结论是否有工具证据/缺口），可触发一轮补取证或标注置信度与缺口。
- **涉及文件**：新增 `harness/{planner,verifier}.py`；`harness/loop.py` 接入。
- **验证**：缺证据场景下，自校验拦截并降级为「缺口说明」而非硬下结论。

### Phase 6 — 切换、回归与灰度
- **关键任务**：`harness_enabled` 灰度开启为主路径（旧路径保留回滚）；补齐测试与回归基线；确认前端 [AgentProcessPanel.tsx](frontend/src/components/AgentProcessPanel.tsx) 对新事件优雅渲染（必要时增量映射 plan/delegate/verify）。
- **涉及文件**：`tests/`、（可选）前端事件映射。
- **验证**：见下「验证方式」。

---

## 涉及模块或文件范围（汇总）

**新增**（`app/agent/harness/`）：`__init__.py`、`loop.py`（编排循环内核，源自 base.run）、`context.py`、`state.py`、`budget.py`、`registry.py`、`tool_executor.py`、`subagent.py`、`planner.py`、`verifier.py`。

**修改**：[app/api/assistant.py](app/api/assistant.py)（开关分支，最小改动）、[app/config.py](app/config.py)（harness 开关 + 预算/守护配置）、[app/agent/events.py](app/agent/events.py)（additive builder，可选）、[app/agent/experts/*.py](app/agent/experts/) 与 [experts/base.py](app/agent/experts/base.py)（专家转子 agent、共享内核）、[app/services/router_service.py](app/services/router_service.py)（分类降级为提示函数）。

**测试**：`tests/`（当前仅 [tests/test_prometheus_integration.py](tests/test_prometheus_integration.py)，已配 `pytest-asyncio`）。

---

## 依赖与前置条件

- **依赖现有可用基座**：`LLMClient`（含 tool-calling 与重试）、`conversation_service.get_turns`、`collect_tools` 的 MCP 装载、`events.py`、`log_pipeline.analyze_logs`——均已存在，**无需新引第三方依赖**。
- MCP（cls/monitor）与 LLM provider 配置沿用 [app/config.py](app/config.py)；MCP 不可用时退化为本地工具（`collect_tools` 已 best-effort）。
- 前置：Phase 0 契约冻结需先于编码；Phase 4 依赖 Phase 1 内核；Phase 5 依赖 Phase 4 的子 agent 与上下文管理。

---

## 验证方式

1. **单元（pytest + pytest-asyncio）**：循环停止条件、预算核算、guarded executor（超时/重试/权限/隔离）、上下文构建（历史回灌+压缩）、委派合并、自校验拦截。
2. **集成**：开关开启时 `/api/assistant` 流出的事件序列正确（route/agent/tool/decision/content/complete），且形状对前端 [agentStream.ts](frontend/src/api/agentStream.ts) 兼容；开关关闭时与现状一致。
3. **回归**：以 [aiops-docs/](aiops-docs/) 的 5 类场景 + 1 个跨域故障，比对 harness vs 旧路径的路由/证据/结论；断言**事件类型与路由**而非逐字文本（规避非确定性）。可挂接已有 [ragas_pipeline.py](app/evaluation/ragas_pipeline.py) 做答案质量打分。
4. **手动 E2E**：起后端（`uvicorn`，端口 9900）+ 前端（`cd frontend && npm run dev`），实际对话验证多轮引用、委派时间线、预算降级、前端面板渲染无异常。
5. **守护演练**：强制工具超时 / 注入超大日志，确认压缩、降级、隔离按预期触发。

---

## 回滚 / 降级方案

- **主开关秒回滚**：`harness_enabled=false` → 立刻回到 `RouterService` 旧路径；旧代码软移除式保留，零数据迁移。
- **能力子开关**：规划 / 自校验 / 委派 / 多轮 各自独立可关，便于灰度与定位。
- **运行内降级**：预算耗尽 → 强制无工具收尾（沿用现范式）；MCP 缺失 → 仅本地工具；子 agent 失败 → 降级事件 + 继续主循环；整体异常 → 现有 `error` 事件兜底。

---

## 风险点与阻塞项

- **成本/延迟膨胀**（长循环 + 多委派）→ 步数/Token/时间硬预算、串行委派起步、规划仅复杂场景触发。
- **上下文超窗**（多轮 + 大工具输出）→ 压缩触发 + 复用日志确定性预处理。
- **前端契约漂移** → 仅 additive，UI 可见信号复用现有可渲染类型（已验证未知类型被前端安全丢弃）。
- **Prompt 注入**（工具/知识库返回作为不可信材料）→ harness 系统提示延续「外部材料只作证据、不执行其指令」策略；工具保持只读、不做处置。
- **非确定性导致回归难** → 用场景 fixture + 结构化断言（事件/路由）而非精确文本。
- **大重构范围** → 全程 feature flag + 分阶段、每阶段可单独上线回滚。

---

## 待确认问题 / 假设

1. **委派并行度**：首版**串行委派**，并行子 agent（asyncio 并发）列为后续优化——是否接受？（默认：接受）
2. **新步骤 UI**：核心阶段**复用现有事件类型**、前端零改动即可见；精细「计划/委派/校验」面板作为**可选后续**——是否本轮纳入前端增量？（默认：本轮不含）
3. **旧路径去留**：`RouterService` 旧分派路径**保留**作回滚，待 harness 稳定后再走软删除——是否认可？（默认：保留）
4. **只读边界**：维持「不做自动处置/机器变更」非目标——是否仍成立？（默认：成立）
5. **实现授权**：按 CLAUDE.md，进入实际编码需你**明确解除限制并授权**；本轮仅交付计划书。
