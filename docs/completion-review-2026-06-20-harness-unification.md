# Completion Review Report: Agent 系统 Harness 化（统一编排循环）

- Review date: 2026-06-20
- Review scope: `app/agent/harness/`（`__init__.py` / `loop.py` / `context.py` / `state.py` / `registry.py` / `tool_executor.py` / `subagent.py`）+ 改动文件 `app/api/assistant.py`、`app/config.py` + 新增测试 `tests/test_harness_service.py`
- Source material: 计划书 [plan/oncall-agent-harness-unification.md](../plan/oncall-agent-harness-unification.md)；既有简化设计 [docs/superpowers/specs/2026-06-17-router-experts-simplification-design.md](superpowers/specs/2026-06-17-router-experts-simplification-design.md)；用户审查请求
- Verification commands:
  - `python -m pytest tests/test_harness_service.py -o addopts="" -q` → **4 passed in 1.45s**
  - `git status --short` → harness 目录与测试为未跟踪新增；`app/api/assistant.py`、`app/config.py` 为已修改

## Follow-up Implementation Update（2026-06-20）

本轮已按本报告优先级完成以下修复：

1. **P0 已修复**：`/api/assistant` 在 `harness_enabled=True` 时向 harness 传入原始 `request.id`，保证读取与 `conversation_service.append_turn(... session_id=request.id ...)` 的持久化键一致；旧 `RouterService` 分支继续使用 scoped session id，保持回滚路径行为。
2. **P0 回归测试已补齐**：新增真实临时 SQLite 的读写一致性测试，明确证明 raw session id 可读到历史、scoped session id 读不到该历史；同时新增 assistant API 层测试，覆盖 harness/raw 与 legacy/scoped 两条分支。
3. **P1 委派可观测性已部分修复**：`delegate_to_expert` 返回的子专家 `agent_event` / `tool_event` 会注入主 harness timeline，并补充 `span_id`、`parent_tool_call_id`、`delegated_expert`，前端可通过既有事件类型看到子步骤。
4. **P2 上下文重复注入已部分修复**：当前用户请求与历史全文不再重复塞进 system prompt；历史仍作为真实 chat messages 传入，当前请求仍作为最后一条 `user` message 传入。
5. **Phase 6 后端回归已部分补齐**：新增 `/api/assistant` handler 级两轮 harness 集成测试，使用真实临时 SQLite 持久化第一轮，再断言第二轮 harness LLM messages 含上一轮 user/assistant 历史；旧 Router 回滚分支已有 scoped session id 回归测试覆盖。
6. **Phase 6 前端 E2E 已做一次 stub LLM 联调**：启动 `harness_enabled=True` 后端、Vite 前端和临时 OpenAI-compatible stub LLM，使用 Playwright + 系统 Chrome 跑通登录、两轮对话、右侧 timeline 展示、历史会话恢复和移动首屏 smoke；stub 调用记录确认第二轮 LLM messages 为 `system,user,assistant,user`，含上一轮 CPU 结论。
7. **Phase 5 规划/自检已从固定外壳升级为轻量规则模块**：新增 `harness/planner.py` 和 `harness/verifier.py`。Planner 会根据 route、可用工具、历史轮数生成 todo 和 required evidence；Verifier 会基于成功/失败 tool evidence、最终答案和计划要求输出 `confidence`、`gaps`、`evidence_count`，无工具证据时明确降级为低置信度缺口说明。

Fresh verification:
- `python -m pytest tests/test_harness_service.py -o addopts="" -q` → **10 passed**
- `python -m pytest tests -o addopts="" -q` → **23 passed, 1 warning**
- `python -m ruff check app/agent/harness/planner.py app/agent/harness/verifier.py app/agent/harness/loop.py tests/test_harness_service.py` → **All checks passed**
- `cd frontend && npm test -- --run` → **17 passed across 6 files**
- Playwright E2E（Browser plugin 不可用，fallback 到普通 Playwright + system Chrome）→ **desktop two-turn chat + restore passed; mobile smoke passed**

仍未完成：LLM 驱动 planner/verifier（当前为轻量规则版）、专家收敛到共享 harness 内核、日志类工具 `analyze_logs` 后处理、工具瞬时错误重试、无进展检测、真实模型/真实 MCP 数据源下的灰度回归。

## Independent Re-Verification（复核，2026-06-20）

> 由 Claude Code 在 review-only 模式下对上面「Follow-up Implementation Update」逐条独立核验，未改动任何业务代码。

**复核结论：显著推进，P0 已清除；仍不具备生产灰度条件，但已无静默正确性阻断。**

独立复跑的验证命令（本轮实跑）：
- `python -m pytest tests/test_harness_service.py -o addopts="" -q` → **10 passed in 1.92s**
- `python -m pytest tests -o addopts="" -q` → **23 passed, 1 warning**
- `python -m ruff check app/agent/harness/ tests/test_harness_service.py app/api/assistant.py` → **All checks passed!**

逐项核验：

| 原发现 | 复核状态 | 独立证据 |
| --- | --- | --- |
| P0 多轮历史读写键不一致 | **已修复（强回归覆盖）** | [assistant.py](../app/api/assistant.py) harness 分支传原始 `request.id`，legacy 分支传 scoped id；`test_context_builder_reads_real_sqlite_history_by_raw_session_id` 用真 SQLite 证明 raw 可读、scoped 读空；`test_assistant_harness_two_turn_flow_persists_and_reloads_history` 两轮真持久化，断言第二轮 LLM messages = `system,user,assistant,user` 且含上一轮结论 |
| P2 上下文重复注入 | **已修复** | [context.py](../app/agent/harness/context.py) 不再把 user message / 历史全文塞进 system prompt；测试断言 `"继续看内存" not in system_prompt`、`"上一轮答 CPU 很高" not in system_prompt` |
| P1 委派子步骤不进时间线 | **已修复（基础）** | [loop.py:296-323](../app/agent/harness/loop.py#L296) `_delegate_child_events` 注入子事件 + `parent_tool_call_id`/`delegated_expert`/`span_id`；`test_harness_stream_injects_delegate_events_into_main_timeline` 验证子 `agent_event`/`tool_event` 带 `delegate:call-delegate:` 前缀进入主时间线 |
| P1 规划/自校验仅外壳 | **部分修复** | 新增规则版 [planner.py](../app/agent/harness/planner.py)（按 route 生成 todo + `required_evidence`）、[verifier.py](../app/agent/harness/verifier.py)（按成功/失败工具证据出 `confidence`/`gaps`）；`test_harness_verify_marks_answer_without_tool_evidence_as_degraded` 验证无证据时降级为 low/degraded。**但仍非 LLM 驱动，且校验为观测型——不触发补取证、不改写答案（见下新发现）** |
| Phase 6 回归 | **部分修复** | 后端集成/分支回归测试已补（真 SQLite 两轮 + API 双分支）；前端 17 passed 与 Playwright E2E 为自报，本轮未独立复跑（需起服务+stub LLM） |
| P1 专家收敛共享内核 | 未做（已知） | 委派仍调旧 `ToolCallingExpert.run`，双实现 |
| P1 大日志接 `analyze_logs` | 未做（已知） | 直连工具仍硬截断 |
| P2 工具重试退避 | 未做（已知） | `GuardedToolExecutor` 仍无重试 |
| P2 无进展检测 | 未做（已知） | 仅 `max_steps` 兜底 |

### 复核新发现（需提示）：自校验是「观测」不是「纠正」

- Evidence: [loop.py:218-223](../app/agent/harness/loop.py#L218) 中 `verify_event` 在时间线里标 `confidence`/`gaps` 后，仍**原样 `yield content` 流出未改写的答案**；verifier 也不回灌触发补取证（[verifier.py](../app/agent/harness/verifier.py) 仅返回 `VerificationResult`，无任何重取证/改写动作）。
- Impact: 模型若给出「自信但零证据」的答案，用户照样看到原文，低置信度只躺在侧栏时间线里。对 OnCall 场景这仍是质量风险——能力②「自校验」目前只完成「标注」，未完成计划 Phase 5 的「触发一轮补取证 / 影响最终输出」。
- Recommendation: verify 结果回灌——低置信度时追加一轮取证，或在答案前显式插入缺口声明，使自检成为影响输出的守护而非旁路注记。
- Verification: 构造「自信无证据」回答，断言最终 `content` 被插入缺口声明或触发补取证，而非逐字透传。

### 复核新发现（次要）：`context.py` 历史摘要为死字段

- Evidence: dedup 修复后历史只作真实 messages 注入，system prompt 不再使用历史摘要；如 `_summarize_turns` 结果仍被计算/保存但不再注入，则为可清理的死代码（P3，不影响功能）。
- Recommendation: 随后续清理移除未使用字段，保持 ContextBuilder 简洁。

### 完成度量（复核更新）

① 多轮上下文 **~95%（已可用且测试覆盖）**｜② 规划+自校验 **~45%（规则版+仅观测，较初评 ~15% 提升）**｜③ 委派 **~70%（串行+可观测，未收敛内核）**｜④ 守护/预算/可观测 **~75%（缺重试与日志管线）**。

### 复核后的就绪判断

- **P0 关键阻断已解除**，且有真实 SQLite 回归兜底，单测虚假绿灯问题已修复。
- 切换条件仍未满足：建议先补「LLM 驱动规划/校验 + 校验回灌（纠正型自检）」与「真实模型 + 真实 MCP 灰度回归」，再灰度开启 `harness_enabled`（当前默认 False，线上无即时风险，符合 Feature Flag 约束）。
- 整体结论由初评「部分完成（P0 阻断）」上调为 **「基本完成，P0 已解除；补齐 LLM 规划/校验与灰度回归后可开启 harness_enabled」**。

> 本节为独立复核归档，未改动任何业务代码（遵循项目 CLAUDE.md 工作边界）。下方原始报告（Overall Conclusion 及之后）保留首轮审查时的结论与证据，未回改，以保留评审轨迹。

## Follow-up Implementation Update 2（2026-06-20，四项缺口落地）

> 用户明确授权解除 CLAUDE.md 限制、进入实现阶段后，按用户多选确认落地以下四项；**专家收敛共享内核**与**真实模型/MCP 灰度回归**按事前说明本轮不做。

验证（本轮实跑）：
- `python -m pytest tests -o addopts="" -q` → **30 passed, 1 warning**（harness 专项 17 passed，新增 7 个测试）
- `python -m ruff check app/agent/harness/ app/config.py tests/test_harness_service.py` → **All checks passed!**（`app/` 其余 12 处 ruff 告警为既有未触碰文件的存量问题，未在本轮范围内处理）

| 原缺口 | 状态 | 实现要点与证据 |
| --- | --- | --- |
| 自检仅观测、不纠正 | **已落地** | [loop.py:251-269](../app/agent/harness/loop.py#L251) `averify` 后，`status∈{degraded,failed}` 且有 gaps 时 `_apply_corrective_notice` 在答案前插入缺口声明，流式与持久化一致；开关 `harness_corrective_verify_enabled`（默认 True）；测试 `test_harness_corrective_verify_prepends_gap_notice` |
| 工具瞬时错误无重试 | **已落地** | [tool_executor.py](../app/agent/harness/tool_executor.py) `_run_one` 指数退避重试，鉴权类（401/403/forbidden/permission/authentication）不重试；测试 `test_guarded_tool_executor_retries_transient_error` / `..._does_not_retry_auth_error` |
| 无进展检测缺失 | **已落地** | [loop.py:208-233](../app/agent/harness/loop.py#L208) 按 `_tool_signature` 检测重复调用，连续达 `harness_no_progress_limit`（默认 2）发 `no_progress` 降级并收尾；测试 `test_harness_stream_stops_on_no_progress` |
| 大日志未接 analyze_logs | **已落地** | [loop.py:301-365](../app/agent/harness/loop.py#L301) `_log_postprocess`：日志类工具超大输出走 `analyze_logs` 聚类摘要替代硬截断，`log_pipeline` 事件入主时间线；开关 `harness_log_pipeline_enabled`（默认 True）；测试 `test_harness_stream_runs_log_pipeline_for_large_log_output` |
| 规划/自检非 LLM 驱动 | **已落地（带回退）** | [planner.py](../app/agent/harness/planner.py) `acreate` / [verifier.py](../app/agent/harness/verifier.py) `averify`：开关开启时单次 LLM 产出结构化 JSON，解析失败/异常自动回退规则版；开关 `harness_llm_planning_enabled` / `harness_llm_verify_enabled`（默认 False）；测试 `test_harness_llm_planner_overrides_rule_plan` / `test_harness_llm_verifier_refines_status` |

新增配置（[app/config.py](../app/config.py)）：`harness_tool_max_retries`、`harness_tool_retry_backoff_seconds`、`harness_no_progress_limit`、`harness_log_pipeline_enabled`、`harness_corrective_verify_enabled`、`harness_llm_planning_enabled`、`harness_llm_verify_enabled`。

更新后的「仍未完成」：**专家收敛到共享 harness 内核**、**真实模型 + 真实 MCP 数据源下的灰度回归**（达标后再开 `harness_enabled`）。

## Overall Conclusion

**部分完成（conditionally not ready）。** Harness 骨架（计划 Phase 1）、长循环停止条件（Phase 2 主体）、工具守护与统一注册表（Phase 3 主体）已落地且单元测试通过；`harness_enabled` 默认关闭、旧 `RouterService` 路径零改动，可秒回滚——这部分符合计划的「Feature Flag + 软移除」约束。

但存在一个 **P0 级正确性问题**：用户最看重的「多轮上下文」能力在真实 `/api/assistant` 路径上因会话键不一致而**静默失效**，且现有单测因 mock 掉持久层而无法发现。此外「规划 + 自校验」（Phase 5 / 能力②）当前**仅为事件外壳**，委派可观测性与专家内核收敛（Phase 4）为半成品，切换/回归/前端联调（Phase 6）整体未做。

结论：**在修复 P0 并补齐回归前，不应灰度开启 `harness_enabled`。** 当前默认关闭，故对线上无即时风险。

## Findings

### P0 - 多轮历史读写会话键不一致，生产路径历史恒为空

- Evidence:
  - 写入用**未 scope** 的 `request.id`：[app/api/assistant.py:54](../app/api/assistant.py#L54) → `_persist_turn` [assistant.py:23-31](../app/api/assistant.py#L23)（`session_id=request.id`）。
  - 读取用 **scoped** id：harness 收到 `scoped_session_id`（[assistant.py:41,50](../app/api/assistant.py#L50)），`ContextBuilder._load_recent_turns` 以它调用 `get_turns`（[app/agent/harness/context.py:84](../app/agent/harness/context.py#L84)，经 [loop.py:88,121](../app/agent/harness/loop.py#L121) 传入）。
  - `scope_session_id` 会把 id 改写为 `owner:{owner_key}:{session_id}`（[app/services/session_scope_service.py:35](../app/services/session_scope_service.py#L35)）。
  - 会话列表/恢复 API 用**未 scope** id 读（[app/api/conversations.py:27](../app/api/conversations.py#L27)），证明 canonical 存储键为 `(owner_key, 未 scope id)`。
  - 单测 `test_context_builder_includes_recent_history` 用 `FakeConversationService` 忽略键直接返回（[tests/test_harness_service.py:21-22](../tests/test_harness_service.py#L21)），故**漏检**此不一致。
- Impact: harness 一旦灰度上线，多轮对话表现为「失忆」（历史永远读不到）；而该能力是本次 harness 化的 #1 优先项。更糟的是单测给出虚假绿灯，回归不可信。
- Recommendation: 让 harness 以**未 scope 的 `request.id`** 读历史（把未 scope id 一并传入 `stream`，最小改动，不触碰既有会话 API）；或反向把持久化统一为 scoped（需连带改 `conversations.py` 列表/恢复，影响面更大，不推荐）。
- Verification: 新增**真用 SQLite** 的读写一致集成测试：同一 `(owner_key, request.id)` 先 `append_turn` 再经 harness 路径读取，断言历史进入 `context.history_messages`；并跑通 `/api/assistant` 两轮对话观察上下文引用。

### P1 - 规划 + 自校验仅为事件外壳，无真实能力（Phase 5 / 能力②）

- Evidence: `_make_plan_event` 输出**写死的 3 条 todo**、`_make_verify_event` 只统计 `tool_event` 个数决定 completed/degraded（[app/agent/harness/loop.py:284-327](../app/agent/harness/loop.py#L284)）。无 LLM 规划，无「定稿前证据自检 / 触发补取证」；计划所列 `planner.py`、`verifier.py` 未创建。
- Impact: 对外呈现「有规划/有自检」的事件，但实际不影响决策，易造成能力误判。
- Recommendation: 落地 LLM 驱动的轻量规划与证据自检（自检不通过可触发一轮补取证或显式标注置信度/缺口）。
- Verification: 构造「证据不足」场景，断言自检拦截并降级为缺口说明而非硬下结论。

### P1 - 委派子步骤不进入流式时间线（Phase 4 可观测性）

- Evidence: 子专家的 `agent_event/tool_event` 仅塞入 delegate 工具返回 payload 的 `events`（[app/agent/harness/subagent.py:44-52](../app/agent/harness/subagent.py#L44)，`timeline[-12:]`），未并入 `state.timeline_events`。主循环只对 `delegate_to_expert` 产出**一个** `tool_event`。
- Impact: 前端时间线看不到被委派专家的嵌套排查过程，可追踪性弱于计划目标「子 agent 事件挂 span_id 嵌套」。
- Recommendation: 将子专家事件以 `span_id` 嵌套注入主时间线流出。
- Verification: 委派场景下断言流式事件含子专家 tool/agent 事件且带嵌套 span_id。

### P1 - 专家未收敛到共享内核，形成双实现（Phase 4）

- Evidence: 委派直接调用旧 `ToolCallingExpert.run`（[subagent.py:35-40](../app/agent/harness/subagent.py#L35)），专家仍是独立的 3 轮循环实现（[app/agent/experts/base.py](../app/agent/experts/base.py)），未按计划「改为共享 harness 内核的子 agent 配置」。
- Impact: 维护两套循环逻辑（harness loop 与 expert loop），长期偏离「统一编排循环」目标。
- Recommendation: 评估将专家收敛为共享内核的子 agent 配置；或明确接受「委派调旧专家」为阶段性现状并在计划中标注。
- Verification: 架构评审 + 回归确认专家行为不变。

### P1 - 大日志确定性预处理缺失（Phase 2/3 质量）

- Evidence: harness 直连工具输出仅做**硬截断**前 `harness_tool_max_output_chars`(默认 6000) 字符（[app/agent/harness/tool_executor.py:74-81](../app/agent/harness/tool_executor.py#L74)），未复用专家路径的 `analyze_logs` 聚类/摘要（[app/agent/experts/log_pipeline.py](../app/agent/experts/log_pipeline.py)）。
- Impact: 当 `harness_mcp_enabled=True` 让 harness 直接拉日志时，万行日志质量弱于专家路径，且「保留前段」截断可能丢弃最相关行（如末尾堆栈）。默认 `harness_mcp_enabled=False` 时靠委派给日志/诊断专家，专家路径仍有 pipeline，可部分缓解。
- Recommendation: harness 直连日志类工具时接入 `analyze_logs` 后处理。
- Verification: 注入超大日志，断言产出为聚类摘要而非首段截断。

### P2 - 工具级重试退避未实现（Phase 3）

- Evidence: `GuardedToolExecutor.execute` 对超时/异常直接置为 failed，无重试（[app/agent/harness/tool_executor.py:33-72](../app/agent/harness/tool_executor.py#L33)）。计划 Phase 3 含「重试退避」。
- Impact: MCP 瞬时抖动直接变硬失败。与旧路径持平（旧 `execute_tool_calls` 也无重试），非回归，但未达计划。
- Recommendation: 对瞬时错误（超时/5xx/网络）加有限指数退避重试，鉴权类不重试（可参照 `LLMClient` 既有范式）。

### P2 - 上下文重复注入，浪费 Token

- Evidence: 当前 user message 同时出现在 `system_prompt`（[context.py:73](../app/agent/harness/context.py#L73)）与末尾 `user` 消息（[loop.py:157](../app/agent/harness/loop.py#L157)）；历史既作真实 messages 注入，又在 system_prompt 内再摘要一遍（[context.py:67-71](../app/agent/harness/context.py#L67)）。
- Impact: 预算被重复内容占用，接近预算上限时更易过早触发无工具收尾；亦可能轻微干扰模型。
- Recommendation: 二选一表达 user message 与历史（真实 messages 优先，system 内不再重复全文）。

### P2 - 缺「无进展检测」停止条件（Phase 2）

- Evidence: 停止仅靠模型判定完成 / `max_steps` / 预算（[loop.py:162-206](../app/agent/harness/loop.py#L162)），无重复工具调用/无进展检测。
- Impact: 退化模型可能在预算内空转若干步。`max_steps`(默认 6) 兜底，风险有限。
- Recommendation: 加入重复 tool_call / 零新增证据的提前停止。

### P2 - Phase 6（切换/回归/前端联调）整体未做

- Evidence: `harness_enabled` 默认 False（[config.py 新增项](../app/config.py)）；无 `/api/assistant` 集成测试、无 harness vs 旧路径回归、无前端 [AgentProcessPanel.tsx](../frontend/src/components/AgentProcessPanel.tsx) 联调记录。
- Impact: 不具备灰度切换条件。
- Recommendation: 补集成+回归+前端联调后再灰度。

### P3 - 计划文件 `budget.py` / `planner.py` / `verifier.py` 未建

- Evidence: 预算逻辑内联进 `state.py`（[over_budget](../app/agent/harness/state.py#L50)）/`loop.py`，可接受；planner/verifier 缺失对应上面 P1。
- Impact: 仅结构与计划略有出入，非功能问题。
- Recommendation: 随 P1 规划/自校验落地时一并建立或在计划中更新模块划分。

## Completion Matrix

| 计划项 | 状态 | 证据/说明 |
| --- | --- | --- |
| Phase 1 内核骨架 + `harness_enabled` 分支 | Complete | loop/context/state/__init__、[assistant.py:46](../app/api/assistant.py#L46)、config 9 项 |
| Phase 2 多轮上下文（历史回灌） | Partial（生产失效） | 逻辑具备，但 P0 键不一致致真实路径读不到 |
| Phase 2 长循环停止条件 | Partial | 模型完成/步数/Token/时间/超时降级均有；缺无进展检测、缺近预算压缩（仅截断+无工具收尾） |
| Phase 3 统一工具注册表 | Complete | [registry.py](../app/agent/harness/registry.py) 本地+MCP（flag）+delegate+metadata |
| Phase 3 守护执行器 | Partial | 超时/allowlist/截断/隔离有；缺重试、缺 analyze_logs 后处理 |
| Phase 4 专家转子 agent | Partial | 直接复用旧 `run`，未收敛共享内核 |
| Phase 4 串行委派 | Complete（基础） | [subagent.py](../app/agent/harness/subagent.py)，含回退/空任务处理 |
| Phase 4 委派可观测 | Missing | 子事件未进时间线 |
| Phase 5 规划 | Missing（仅外壳） | 写死 todo |
| Phase 5 自校验 | Missing（仅外壳） | 仅数 tool_event |
| Phase 6 切换/回归/前端 | Missing | 默认关闭，无集成/回归/联调 |
| 单元测试 | Partial | 4 项通过；但 P0 因 mock 漏检，缺集成/回归 |
| 事件契约不破坏前端 | Complete | 复用 agent_event/tool_event，前端零改动可显示 |
| 旧路径可回滚 | Complete | 默认关，`RouterService` 未改 |

## Test And Verification Notes

- `python -m pytest tests/test_harness_service.py -o addopts="" -q` → 4 passed（需用 `-o addopts=""` 关闭 pyproject 中 `--cov` 默认参数，否则与 `-p no:cov` 冲突报错）。
- 覆盖范围：tool 执行+收尾事件序列、context 历史装配（**用 Fake 持久层**）、大输出截断、委派调用专家。
- 缺口：无真实 SQLite 读写一致测试（这正是 P0 漏检根因）；无 `/api/assistant` 端到端集成测试；无 harness vs 旧路径回归。
- 未跑全量 `pytest`（与本次范围弱相关，且默认 `--cov` 会拉长输出）；如需可补跑。

## Next Steps

1. **修复 P0**：harness 改用未 scope `request.id` 读历史 + 新增 SQLite 读写一致集成测试（最高优先，阻断 #1 能力）。
2. **补真规划/自校验**：落 LLM 规划 + 证据自检（可触发补取证），消除事件外壳。
3. **委派可观测**：子专家事件以 span_id 嵌套并入主时间线。
4. **质量补强**：直连日志接 `analyze_logs`；工具瞬时错误重试；去除上下文重复注入；加无进展检测。
5. **Phase 6**：补 `/api/assistant` 集成测试 + vs 旧路径回归 + 前端联调，达标后再灰度 `harness_enabled`。
6. （可选）评估专家收敛到共享内核，消除双实现。

> 说明：本报告仅为完成状态审查归档，未改动任何业务代码（遵循项目 CLAUDE.md 工作边界）。进入修复阶段需用户明确授权。
