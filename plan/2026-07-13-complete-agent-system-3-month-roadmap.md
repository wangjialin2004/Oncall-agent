# 完整 Agent 系统差距 → 3 个月演进路线图

> **角色边界**：本文件只描述「怎么演进」。真正改业务代码需明确授权后再实施。  
> **基线日期**：2026-07-13  
> **基线状态**：正式 **L1 Go**（只读 OnCall 诊断副驾）；H3 真人 OnCall 豁免  
> **目标终点（M3 末）**：**L3 值班副驾 / 预生产就绪**（仍默认只读；HITL 建议动作可选）  
> **配套**：
> - [模块差距清单](../docs/pilot/2026-07-10-module-gap-inventory.md)
> - [L1 交接](../docs/pilot/handoff-2026-07-12-l1-pilot.md)
> - [Go/No-Go](../docs/pilot/go-nogo-20260712.md)
> - [状态化上下文计划](./2026-07-08-stateful-agent-context.md)
> - [Harness 统一计划](./oncall-agent-harness-unification.md)
> - [M1 W1 实施计划](./2026-07-13-m1-w1-close-the-loop-implementation.md)（已开工；含补录）
> - [CLAUDE.md](../CLAUDE.md)（先计划后编码）

---

## 0. 一页摘要

### 0.1 从哪到哪

| 维度 | 现在（L1，2026-07-13） | 3 个月后（L3 目标） |
|---|---|---|
| 产品定位 | 只读诊断副驾（技术试点） | 可进预生产的值班副驾 |
| 编排闭环 | Plan 一次 + Verify 标注 | Plan → Act → Verify → **Re-evidence** → **Replan** |
| 多 Agent | 串行委派 + 双循环 | 并行 fan-out + **共享 harness 内核** |
| 数据面 | Prom 种子 + local log + 变更骨架 | 稳定 Prom/日志 + **只读变更源或正式降权** |
| 记忆 | 可召回，自动学习弱 | 成功/失败 case **自动蒸馏** |
| 评测 | 12/23 题；最小 8 题 | **23/23 + CI 门禁 + 周基线** |
| 时延 | P50 ≈ 138s（已书面接受） | **P50 ≤ 75s，P95 ≤ 150s** |
| HITL | 澄清为主；无真人升级 | 澄清 + **建议动作确认** + 升级路径 |
| 平台 | 本机 bat/nohup | Compose 一键 + 质量/成本指标 |

### 0.2 三阶段命名

| 月 | 代号 | 主题 | 出口等级 |
|---|---|---|---|
| **M1**（W1–W4） | **Close the Loop** | 闭环加深 + 时延 + 评测扩面 | **L1.5 可靠副驾** |
| **M2**（W5–W8） | **Collaborate & Connect** | 多专家并行 + 数据面 + 内核统一 | **L2 协作诊断** |
| **M3**（W9–W12） | **Learn & Platform** | 自学习 + HITL + 可观测 + 预生产 | **L3 预生产就绪** |

### 0.3 非目标（3 个月内明确不做）

1. **自动处置 / 自动回滚 / 自动扩缩容**（写操作仍禁止；M3 最多到「建议 + 人工确认」）
2. 全面替换 LLM provider 架构或自建模型训练
3. 多区域多活 / 企业级 IAM / SSO 完整落地（仅预留接口）
4. 重写前端为全新产品（只做必要增量）
5. 把旧 `RouterService` 物理删除（保留回滚债清理到 M3 末再评估）

### 0.4 成功北极星（M3 末必须同时满足）

| ID | 指标 | 基线 | M3 目标 |
|---|---|---|---|
| N1 | 全量评测 `evals/oncall` | 12 题 / 严格 8/8 | **≥21/23 通过**；Core 5/5 |
| N2 | P50 端到端时延 | 138s | **≤ 75s** |
| N3 | P95 端到端时延 | 158s | **≤ 150s** |
| N4 | complete 率 | 100% | **≥ 99%** |
| N5 | 低置信后补取证触发率（有缺口 case） | ~0 | **≥ 80% 触发且可观测** |
| N6 | 跨域 case 并行委派占比 | 0 | **≥ 60%** 使用 parallel path |
| N7 | 自动经验沉淀（成功诊断） | 弱/手工 | **≥ 50% 成功 run 可自动/半自动入库** |
| N8 | CI 门禁 | 无强制流水线 | **PR 必跑 pytest + 前端 + 冒烟** |
| N9 | Checkpoint 深恢复 | tool 后 kill 可 resume | timeout 落盘 + ContextState rehydrate |
| N10 | 只读安全 | N3 过 | 负例全过 + 无「已执行变更」幻觉 |

---

## 1. 差距 → 工作包映射

| 差距域 | 当前痛点 | 工作包 | 主月 |
|---|---|---|---|
| A 编排闭环 | 无 re-evidence / replan | WP-A1~A4 | M1 |
| B 多 Agent | 串行、双循环、aux 不执行 | WP-B1~B4 | M2（B1 可 M1 预热） |
| C 数据面 | 变更骨架、日志/监控默认弱 | WP-C1~C4 | M1–M2 |
| D 记忆学习 | 存有学弱 | WP-D1~D3 | M2–M3 |
| E HITL | 无确认/升级 | WP-E1~E3 | M3 |
| F 评测飞轮 | 题不全、无 CI | WP-F1~F4 | M1 起持续 |
| G 可观测成本 | 无 APM/质量看板 | WP-G1~G3 | M2–M3 |
| H 平台化 | 本机级、配置漂移 | WP-H1~H4 | 贯穿；M3 收口 |
| I 时延 | P50 过高 | 横切 WP-L* | M1 主攻 |

---

## 2. Month 1 — Close the Loop（L1 → L1.5）

**主题**：把「会答」变成「会查到够再答」；把「能跑」变成「可回归」。  
**节奏**：4 周；每周必须有可演示增量 + 评测对照。

### 2.1 目标与出口

| 出口项 | 标准 |
|---|---|
| Re-evidence | 低置信 / 有 gaps 时，默认最多 **1 轮**强制补取证，再 final |
| 时延 | 严格 8 题连续：P50 **≤ 100s**（中期里程碑）；冲刺 **≤ 90s** |
| 评测 | cases **≥ 18/23**；最小 8 题仍 8/8；新增 re-evidence 专项 ≥2 题 |
| Checkpoint | 外层 timeout **best-effort 落盘**；resume 能 rehydrate ContextState ref |
| 配置债 | `HARNESS_*` 注释/默认/入口三方一致；force_delegation 落地或删除 |
| 变更 | 保持「明确不可用」产品边界；路由与话术一致（N6 类 case 稳定过） |

### 2.2 工作包明细

#### WP-A1 · Verify → Re-evidence（P0，W1–W2）

| 项 | 内容 |
|---|---|
| 问题 | `EvidenceVerifier` 只标 status/gaps，`corrective_verify` 只改展示文案 |
| 做法 | 在 `HarnessService` 主循环定稿前：若 `status in {degraded,failed}` 或 `confidence=low` 且步数/预算仍有余量 → 注入「补取证」系统消息 + 允许再跑 **1** 步 tool loop；第二次仍不足则缺口前缀 final |
| 关键文件 | `app/agent/harness/loop.py`、`verifier.py`、`state.py`、`app/config.py` |
| 新开关 | `HARNESS_RE_EVIDENCE_ENABLED`（默认 `true`）<br>`HARNESS_RE_EVIDENCE_MAX_ROUNDS`（默认 `1`） |
| 验收 | 单测：无工具证据 → 触发 re-evidence 事件；预算耗尽不重入<br>评测：构造缺证据 fixture，时间线出现 `stage=re_evidence` |
| 风险 | 时延变差 → 与 WP-L1 绑定；默认只 1 轮 |

#### WP-A2 · Plan 与 required_evidence 细匹配（P1，W2–W3）

| 项 | 内容 |
|---|---|
| 问题 | `required_evidence` 不约束 tool 选择，也不参与 verify 细匹配 |
| 做法 | 规则映射：证据类型 → 工具名集合；verify 检查「计划要求的类是否至少 1 次成功」；planner 输出保持兼容 |
| 关键文件 | `planner.py`、`verifier.py`、`loop.py` |
| 开关 | 复用 `HARNESS_CORRECTIVE_VERIFY_ENABLED`；可选 `HARNESS_EVIDENCE_MATCH_STRICT`（默认 `false`，M1 观测） |
| 验收 | 计划要求 metric+log 时，仅 knowledge 成功 → degraded + 明确 gap |

#### WP-A3 · 轻量 mid-loop replan（P1，W3–W4）

| 项 | 内容 |
|---|---|
| 问题 | plan 只在开头一次 |
| 做法 | 触发条件（满足其一）：① 主工具失败 ② re-evidence 后仍 gap ③ 路由 aux 暗示跨域但未委派。输出更新 `todos/required_evidence`，emit `stage=replan`。**默认规则 replan**；LLM replan 挂 `HARNESS_LLM_PLANNING_ENABLED` |
| 关键文件 | `planner.py`、`loop.py` |
| 新开关 | `HARNESS_REPLAN_ENABLED`（默认 `true`）<br>`HARNESS_REPLAN_MAX_TIMES`（默认 `1`） |
| 验收 | 单测触发条件；评测时间线可见 replan ≤1 次 |

#### WP-A4 · force_expert_delegation 落地或删除（P0，W1）

| 项 | 内容 |
|---|---|
| 问题 | 配置/注释存在，主路径效果可疑（差距清单「漂移」） |
| 做法 | **二选一写进 ADR**：A) 路由后确定性 seed `delegate_to_expert`；B) 删除配置与文档。推荐 **A**，默认仍 `false`，试点可开 |
| 关键文件 | `loop.py`、`subagent.py`、`config.py`、`.env.example` |
| 验收 | 开关 true 时时间线必有首委派；false 与现网一致 |

#### WP-C1 · 变更能力边界硬化（P0，W1）

| 项 | 内容 |
|---|---|
| 问题 | 骨架工具易导致幻觉版本/操作人 |
| 做法 | 保持 `CHANGE_SOURCE_AVAILABLE=False`；统一「无变更源」结构化返回；路由对纯变更追问优先 gap 话术；N6 进最小回归集 |
| 关键文件 | `change_tool.py`、`experts/change.py`、`docs/pilot/change-capability-unavailable.md` |
| 验收 | N3/N6 稳定过；不得出现伪造版本号 |

#### WP-L1 · 时延快赢（P0，贯穿 M1）

| 杠杆 | 动作 | 文件/配置 |
|---|---|---|
| 模型分层 | planner/step 用轻量 `LLM_PLANNER_MODEL`；收尾/verify 用 reasoner | `config.py`、`loop.py` |
| 减少空转 | 收紧 no_progress；重复 tool 签名更早停 | `HARNESS_NO_PROGRESS_LIMIT` |
| 工具并行（同一步多 tool_call） | 若 LLM 一次返回多 tool_call，**并行执行**（注意幂等） | `agent_loop.py` / `tool_calling.py` |
| 步数策略 | 简单 knowledge 路由 `max_steps` 动态降到 3 | `loop.py` |
| 观测 | 每 case 记录 route/steps/tool_count/token/latency | `evaluate_oncall_local.py` |

**M1 时延门禁**：严格 8 题 P50 ≤ 100s；争取 ≤ 90s。不达标则 M2 并行委派优先。

#### WP-F1 · 评测扩到 ≥18 题 + 自动化（P0，W1–W3）

| 项 | 内容 |
|---|---|
| 补题 | 优先：K2/K3、N2/N4/N5、M2、R1–R3、re-evidence 专项 2 题 |
| fixtures | `evals/oncall/fixtures/` 放 mock 告警/日志片段，减少对 live Prom 依赖 |
| 脚本 | `scripts/evaluate_oncall_local.py` 支持 suite 过滤、JUnit/JSON、失败重跑策略 |
| 验收 | README 更新覆盖表；一键跑最小 8 + 扩展 18 |

#### WP-F2 · CI 门禁骨架（P0，W2–W4）

| 项 | 内容 |
|---|---|
| 内容 | GitHub Actions / 本地 `make ci`：`pytest`（无外部依赖子集）+ `frontend npm test` + 可选 SSE 冒烟（mock LLM） |
| 文件 | `.github/workflows/ci.yml` 或 `Makefile` 目标 `ci`；测试隔离 Milvus/Redis |
| 验收 | 干净环境绿；文档写清「全量 oncall eval 仍需本机依赖」 |

#### WP-H1 · 配置与注释去漂移（P1，W1）

| 项 | 内容 |
|---|---|
| 修 | `harness_enabled` 注释、stateful context「Disabled by default」、health LLM 检查与 `LLM_*` 对齐 |
| 文件 | `app/config.py`、`app/api/health.py`、`.env.example`、README 配置表 |
| 验收 | 差距清单 §11 项清零或标注「有意保留」 |

#### WP-I1 · Checkpoint 可靠性补洞（P0，W2–W3）

| 项 | 内容 |
|---|---|
| ① | 外层 `TimeoutError` 路径 best-effort `save_step` |
| ② | resume 时按 `context_snapshot_ref` **rehydrate ContextState** |
| ③ | 扩大幂等工具白名单（只读 query 类）；保持 replay 默认 false |
| 文件 | `harness/loop.py`、`harness_checkpoint.py`、`context/integration.py` |
| 验收 | `checkpoint_resume_drill.py` 增加 timeout 场景；H6 回归不回退 |

#### WP-CTX1 · ContextState happy path 多轮（P0，W2）

| 项 | 内容 |
|---|---|
| 问题 | `stamp_recent_turns` 主要在 cold rebuild；多轮白板空心 |
| 做法 | 每轮 stream 结束刷新 `recent_turns` + 答案摘要；Redis hit 也增量 patch |
| 文件 | `context/integration.py`、`operations.py`、`loop.py` |
| 验收 | M1/M2 多轮 case；计划 §12 相关条可勾选 |

### 2.3 M1 周计划

| 周 | 交付 | 演示 |
|---|---|---|
| **W1** | WP-A4 决策落地、WP-C1、WP-H1、WP-A1 设计+骨架、评测补 3 题 | 开关矩阵 + N6 稳定 |
| **W2** | WP-A1 合入、WP-CTX1、WP-I1 ①、WP-F2 骨架、WP-L1 分层 | re-evidence 时间线 demo |
| **W3** | WP-A2、WP-I1 ②③、评测 ≥18、时延优化 | 18 题报告 + P50 对照 |
| **W4** | WP-A3、M1 回归、文档 Go L1.5、债清理 | **L1.5 出口评审** |

### 2.4 M1 出口评审（Go / Conditional / No-Go）

| 门禁 | Go | Conditional | No-Go |
|---|---|---|---|
| 严格 8/8 | 通过 | 通过 | 失败 |
| re-evidence | 有事件 + 单测 | 仅开关可关的观测模式 | 无实现 |
| P50 | ≤100s | ≤120s 且有优化 backlog | >120s 无解释 |
| Checkpoint | timeout 落盘 + rehydrate | 仅其一 | 回退 |
| CI | pytest+frontend 可自动跑 | 仅 Makefile 本地 | 无 |

---

## 3. Month 2 — Collaborate & Connect（L1.5 → L2）

**主题**：跨域真正协作；数据面可信；去掉双循环复杂度。

### 3.1 目标与出口

| 出口项 | 标准 |
|---|---|
| 并行委派 | `delegate_parallel` 或 registry 支持 fan-out；跨域 case ≥60% 走并行 |
| 共享内核 | 专家委派默认走 harness 子循环配置，而不是独立 3 轮 `ToolCallingExpert`（或 ADR 接受双循环并冻结边界） |
| aux_routes | 主路由 + 最多 2 个 aux **可执行**（串行补充或并行） |
| 数据面 | Prom 生产指向文档化；日志 provider 可切；变更源 **接入只读 OR 正式永久降权** |
| 评测 | **23/23 全量可跑**；通过 ≥19/23 |
| 时延 | P50 **≤ 85s**（并行收益兑现） |

### 3.2 工作包明细

#### WP-B1 · 并行委派 fan-out（P0，W5–W6）

| 项 | 内容 |
|---|---|
| 做法 | 新增工具 `delegate_parallel`（experts[] + subtasks[]）或扩展 `delegate_to_expert` 支持 `mode=parallel`；`asyncio.gather` + 每专家独立 timeout；合并结构化结果进 context |
| 文件 | `subagent.py`、`registry.py`、`loop.py`、前端过程面板（可选展示并行节点） |
| 开关 | `HARNESS_PARALLEL_DELEGATION_ENABLED`（默认 `true`）<br>`HARNESS_PARALLEL_MAX_EXPERTS`（默认 `3`） |
| 验收 | 单测 2 专家并行 wall-clock < 串行之和的 70%；S4/S5 类 case 时间线可见并行 |

#### WP-B2 · 专家共享 harness 内核（P0，W6–W7）

| 项 | 内容 |
|---|---|
| 问题 | `experts/base.py` 与 `harness/loop.py` 双实现 |
| 做法 | 子 agent = harness 配置（system、tools、max_steps、timeout），复用 `GuardedToolExecutor` + 同一 event 形状；旧 `ToolCallingExpert.run` 保留给回滚 feature flag |
| 文件 | `subagent.py`、`experts/*.py`、`base.py`、`loop.py` |
| 开关 | `HARNESS_SHARED_KERNEL_DELEGATION`（默认 `true`，可回滚 false） |
| 验收 | 委派路径单测不 import 旧 3 轮逻辑（或 AST 检查）；行为黄金集不回退 |

#### WP-B3 · aux_routes 执行策略（P1，W5–W6）

| 项 | 内容 |
|---|---|
| 做法 | Router 返回 aux 后，planner 写入 `pending_expert_probes`；主循环在 step0/1 自动并行或串行 probe；结果进入 evidence |
| 文件 | `router_service.py`、`planner.py`、`loop.py` |
| 开关 | `ROUTER_AUX_EXECUTION_MODE=off\|serial\|parallel`（默认 `parallel`，max 2） |
| 验收 | multilabel 请求时间线含 aux 专家；可关闭回退只标注 |

#### WP-B4 · 委派结果合并与去重（P1，W7）

| 项 | 内容 |
|---|---|
| 做法 | 合并答案/证据/冲突标记；同一工具重复成功结果折叠；写入 ContextState `observed_facts`（framework only） |
| 文件 | `subagent.py`、`context/operations.py`、`tools_evidence.py` |

#### WP-C2 · 监控数据面生产化（P0，W5–W6）

| 项 | 内容 |
|---|---|
| 做法 | runbook：`MONITOR_TARGET_MODE=prometheus` + 真实 `PROMETHEUS_BASE_URL`；告警名/服务标签约定；健康检查暴露 mode |
| 文件 | `mcp_servers/monitor_server.py`、`query_metrics_alerts.py`、`docs/pilot/*`、`.env.example` |
| 验收 | 非 seed 环境可查真实 alerts；失败时 gap 明确 |

#### WP-C3 · 日志数据面（P0，W6–W7）

| 项 | 内容 |
|---|---|
| 做法 | `LOG_PROVIDER` 抽象保持；增加「文件/目录 tail」与「HTTP mock」fixtures；若有真实 CLS，文档化凭证与租户；harness MCP 默认策略写清（试点 true / 库默认 false 的原因） |
| 文件 | `mcp_servers/cls_server.py`、`log.py`、`log_pipeline.py` |
| 验收 | log 路由 case 不依赖手工塞 log 文件到错误路径 |

#### WP-C4 · 只读变更源（P1，W7–W8，可选项）

| 项 | 内容 |
|---|---|
| 选项 A | 接一个最小只读源（Git tag / 发布表 / 工单 CSV MCP）→ `CHANGE_SOURCE_AVAILABLE=True` |
| 选项 B | 确认永久不做 → 路由降权 change、工具从 diagnosis 宽集中移除或强提示 |
| 决策点 | **W7 周一必须二选一**，禁止再悬空 |
| 验收 | A：能返回真实变更记录；B：产品文档 + 评测 N6/R-change 全绿 |

#### WP-D1 · 成功 run 半自动经验沉淀（P1，W7–W8）

| 项 | 内容 |
|---|---|
| 做法 | complete 且 confidence≥medium 且有工具证据 → 生成 draft experience（需用户确认或 `AUTO_DISTILL=true` 才入库） |
| 文件 | `experience_memory_service.py`、`loop.py`、`api/memory.py`、前端可选「采纳经验」按钮 |
| 开关 | `LONG_TERM_MEMORY_AUTO_DISTILL`（默认 `false`）<br>`LONG_TERM_MEMORY_AUTO_DISTILL_MIN_CONFIDENCE=medium` |
| 验收 | 手动/自动各 1 条 e2e；PII 策略不回退 |

#### WP-F3 · 全量 23 题 + 周基线（P0，W5–W8）

| 项 | 内容 |
|---|---|
| 做法 | 补齐剩余 case；每周固定跑 `oncall_weekly_YYYYMMDD`；结果进 `evals/results/`（无密钥） |
| 验收 | M2 末 ≥19/23；报告含与 M1 对照 |

#### WP-G1 · Run 级结构化轨迹导出（P1，W6–W7）

| 项 | 内容 |
|---|---|
| 做法 | 每次 complete 写 `volumes/traces/{session_id}.json`（timeline、usage、plan、verify、tools）；可选采样率 |
| 开关 | `HARNESS_TRACE_EXPORT_ENABLED`（默认 `true` 本地 / 生产采样） |
| 验收 | 任意评测 case 可回放轨迹 |

#### WP-L2 · 并行带来的时延兑现（P0，W6–W8）

| 项 | 内容 |
|---|---|
| 做法 | 跨域默认并行；knowledge 短路；缓存 service_knowledge；避免重复 retrieve |
| 验收 | P50 ≤ 85s |

### 3.3 M2 周计划

| 周 | 交付 |
|---|---|
| **W5** | WP-B1 骨架、WP-B3、WP-C2、23 题补全启动 |
| **W6** | 并行委派可用、WP-G1、WP-C3、共享内核设计评审 |
| **W7** | WP-B2 合入、变更源决策落地、WP-D1 草案 |
| **W8** | 合并去重、全量评测、**L2 出口评审** |

### 3.4 M2 出口评审

| 门禁 | Go |
|---|---|
| 并行委派 | 开关可开 + 单测 + ≥1 跨域 demo |
| 共享内核 | true 默认或 ADR 冻结 |
| 全量评测 | ≥19/23，Core 5/5 |
| P50 | ≤85s |
| 变更决策 | A 或 B 已落地，无悬空 |

---

## 4. Month 3 — Learn & Platform（L2 → L3 预生产）

**主题**：会学习、可协作人、可运维、可进预生产白名单服务。

### 4.1 目标与出口

| 出口项 | 标准 |
|---|---|
| 自学习 | 成功/失败模式可沉淀；召回命中可度量 |
| HITL | 建议动作卡片 + 确认事件（仍不自动执行） |
| 可观测 | 质量指标进 `/metrics` 或导出；成本 token 可汇总 |
| 平台 | compose/一键脚本覆盖 app+依赖；密钥检查清单 |
| 预生产 | 白名单服务 + runbook + 回滚 L4；**仍非无人值守唯一主路径** 除非指定真人 OnCall |
| 北极星 | N1–N10 达标或书面豁免 |

### 4.2 工作包明细

#### WP-D2 · 失败记忆与反模式（P1，W9–W10）

| 项 | 内容 |
|---|---|
| 做法 | 记录「无效工具序列 / 幻觉被拦 / 超时」为 weak memory 或单独 `anti_pattern` 表；召回时降权重复死路 |
| 文件 | `experience_memory_service.py`、memory schema、`recall_experience.py` |
| 验收 | 同类失败第二次更早 gap 或换工具 |

#### WP-D3 · 偏好与策略迁移（P2，W10–W11）

| 项 | 内容 |
|---|---|
| 做法 | 用户偏好（详略、语言、常用服务）自动抽取可选；服务级默认排查顺序写入 service_knowledge |
| 文件 | `user_preference_service.py`、`service_knowledge_service.py` |

#### WP-E1 · 建议动作 HITL（P0，W9–W10）

| 项 | 内容 |
|---|---|
| 做法 | 最终答案结构化字段 `suggested_actions[]`（只读建议）；前端展示「待确认」；确认仅写审计日志，**不触发执行器** |
| 文件 | `loop.py`、事件 schema、`frontend` ChatWorkspace、`conversation_service` |
| 开关 | `HITL_SUGGESTED_ACTIONS_ENABLED`（默认 `true`） |
| 验收 | N3 仍不得「已执行」；确认事件可查询 |

#### WP-E2 · 升级路径 / 值班人（P0，W9，产品）

| 项 | 内容 |
|---|---|
| 做法 | 配置 `ONCALL_ESCALATION_CONTACTS`；答案底部标准升级块；补齐 L1 豁免的 H3 |
| 验收 | 文档有姓名/渠道；Agent 输出含升级提示 |

#### WP-E3 · 澄清体验升级（P1，W10）

| 项 | 内容 |
|---|---|
| 做法 | 结构化缺参（服务/时间窗/环境）；前端快捷填槽；减少启发式误判 |
| 文件 | `clarifier.py`、前端 |

#### WP-G2 · Agent 质量与成本指标（P0，W9–W11）

| 指标 | 说明 |
|---|---|
| `agent_runs_total{status}` | complete/degraded/failed |
| `agent_latency_seconds` | histogram |
| `agent_tool_calls_total{tool,status}` | 工具 |
| `agent_re_evidence_total` | 补取证 |
| `agent_delegate_parallel_total` | 并行 |
| `agent_tokens_total{role}` | usage |
| 文件 | `app/core/metrics.py`、`loop.py`、`deploy/prometheus` |

#### WP-G3 · 可选 OpenTelemetry / 外部 trace（P2，W11）

| 项 | 内容 |
|---|---|
| 做法 | 导出 OTLP 或对接 Langfuse 任选其一；无则保持 JSON trace 文件 |
| 开关 | `OTEL_EXPORTER_OTLP_ENDPOINT` 空则关闭 |

#### WP-F4 · Online 抽样评测（P1，W11–W12）

| 项 | 内容 |
|---|---|
| 做法 | 对生产/预生产 run 按比例抽样人工打分表；与 offline 23 题对照 |
| 文件 | `scripts/` + 简易表格模板 `docs/pilot/online-eval-template.md` |

#### WP-H2 · 部署与密钥清单（P0，W9–W10）

| 项 | 内容 |
|---|---|
| 做法 | `docker compose` 或强化 `start-all`：backend/frontend/mcp/redis/milvus/prom；启动前 check 默认密钥 |
| 文件 | `deploy/`、`Makefile`、runbook |
| 验收 | 新机器按文档 30–60min 内可复现 L2 评测 |

#### WP-H3 · 多租户与审计浅层（P1，W10–W11）

| 项 | 内容 |
|---|---|
| 做法 | owner_key 贯穿 memory/checkpoint/trace；审计日志「谁在何时跑了何 session」 |
| 验收 | 跨用户不可读 checkpoint/memory 写 |

#### WP-H4 · 双路径复杂度收敛（P1，W11–W12）

| 项 | 内容 |
|---|---|
| 做法 | ContextBuilder 滚动摘要正式降为 rebuild-only；文档化；删除死开关或标 deprecated |
| 文件 | `harness/context.py`、`config.py`、ADR |

#### WP-A5 · 可选第二轮 re-evidence / 反思（P2，W12）

| 项 | 内容 |
|---|---|
| 做法 | 对 high-severity 故障允许 `RE_EVIDENCE_MAX_ROUNDS=2`；短 reflection 提示「换工具」 |
| 默认 | 仍 1 轮，避免时延回退 |

### 4.3 M3 周计划

| 周 | 交付 |
|---|---|
| **W9** | HITL 建议动作、升级联系人、质量指标、部署清单启动 |
| **W10** | 失败记忆、澄清升级、密钥/compose、审计 |
| **W11** | Online 抽样、双路径收敛、成本看板、OTEL 可选 |
| **W12** | 全量回归、北极星验收、**L3 Go/No-Go**、下季度 backlog |

### 4.4 M3 出口评审（L3 预生产）

| 门禁 | 必须 |
|---|---|
| 北极星 N1–N10 | 全过或签字豁免 |
| 负例安全 | N* 全过 |
| HITL | 建议动作可见且不可自动执行 |
| 真人升级 | 联系人配置非空（取消 L1 H3 豁免）或产品书面接受继续豁免 |
| 部署 | 他人可按 runbook 复现 |
| 回滚 | MCP off、shared_kernel off、re_evidence off、并行 off 均可一键降级 |

---

## 5. 横切能力与开关总表（演进期新增/强化）

| 开关 | 默认建议 | 引入月 | 作用 |
|---|---|---|---|
| `HARNESS_RE_EVIDENCE_ENABLED` | true | M1 | 低置信补取证 |
| `HARNESS_RE_EVIDENCE_MAX_ROUNDS` | 1 | M1 | 补取证轮数 |
| `HARNESS_REPLAN_ENABLED` | true | M1 | mid-loop replan |
| `HARNESS_REPLAN_MAX_TIMES` | 1 | M1 | replan 次数 |
| `HARNESS_FORCE_EXPERT_DELEGATION` | false | M1 修 | 确定性首委派 |
| `HARNESS_PARALLEL_DELEGATION_ENABLED` | true | M2 | 并行委派 |
| `HARNESS_PARALLEL_MAX_EXPERTS` | 3 | M2 | 并行上限 |
| `HARNESS_SHARED_KERNEL_DELEGATION` | true | M2 | 共享内核 |
| `ROUTER_AUX_EXECUTION_MODE` | parallel | M2 | aux 执行 |
| `LONG_TERM_MEMORY_AUTO_DISTILL` | false | M2 | 自动蒸馏 |
| `HARNESS_TRACE_EXPORT_ENABLED` | true | M2 | 轨迹导出 |
| `HITL_SUGGESTED_ACTIONS_ENABLED` | true | M3 | 建议动作 |
| `ONCALL_ESCALATION_CONTACTS` | "" | M3 | 升级人 |
| 既有 | 保持 | — | `HARNESS_MCP_ENABLED`、checkpoint、stateful context 等 |

**降级旋钮（生产必备）**：任一新能力必须可用 env 关闭且行为回到上一里程碑。

---

## 6. 文件改动热区（按风险）

| 热区 | 文件 | 主月 | 风险 |
|---|---|---|---|
| 主循环 | `app/agent/harness/loop.py` | M1–M2 | 高 |
| 规划/自检 | `planner.py` / `verifier.py` | M1 | 中 |
| 委派 | `subagent.py` / `registry.py` | M2 | 高 |
| 专家 | `app/agent/experts/*` | M2 | 中高 |
| 上下文 | `app/agent/context/*` | M1 | 中 |
| Checkpoint | `harness_checkpoint.py` | M1 | 中 |
| 工具/MCP | `app/tools/*`、`mcp_servers/*` | M1–M2 | 中 |
| 记忆 | `experience_memory_service.py` | M2–M3 | 中 |
| 评测 | `evals/`、`scripts/evaluate_oncall_local.py` | 全程 | 低 |
| 前端 | `AgentProcessPanel` / Chat | M2–M3 | 中 |
| 配置 | `app/config.py`、`.env.example` | 全程 | 低但易漂移 |
| 部署/CI | `Makefile`、`.github/`、`deploy/` | M1/M3 | 中 |

---

## 7. 团队分工建议（可一人多角）

| 角色 | 负责 WP | 关注 |
|---|---|---|
| Harness Owner | A*、L*、B2 | 闭环、时延、内核 |
| Data Plane Owner | C* | Prom/Log/Change |
| Memory Owner | D* | 蒸馏与召回 |
| Eval/CI Owner | F* | 23 题、周报、CI |
| Platform Owner | H*、G* | 部署、指标、密钥 |
| Product/FE | E*、过程面板 | HITL、升级、展示 |
| 值班接口人 | E2 | 真人 OnCall（取消豁免） |

---

## 8. 评测与发布节奏

```text
每周五：
  1) make ci（或等价）
  2) 最小 8 题严格连续（有 LLM 的环境）
  3) 记录 P50/P95/complete/re_evidence/parallel 计数
  4) 更新 evals/results 摘要一行到本路线图附录或 weekly note

每月末：
  1) 出口评审表（§2.4 / §3.4 / §4.4）
  2) 差距清单 diff（哪些从「部分」→「已落地」）
  3) 下月 WP 重排（只允许基于数据砍 scope，不静默扩 scope）
```

### 里程碑标签（建议 git tag）

| Tag | 含义 |
|---|---|
| `l1-go-2026-07-13` | 已达成（当前） |
| `l1.5-close-loop` | M1 出口 |
| `l2-collaborate` | M2 出口 |
| `l3-preprod` | M3 出口 |

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Re-evidence 拖慢 P50 | 时延回退 | 默认 1 轮；预算不足跳过；与模型分层绑定 |
| 并行委派放大 LLM 费用 | 成本 | max 3；knowledge 不并行；缓存 |
| 共享内核回归 | 质量波动 | feature flag；黄金集；双跑对比一周 |
| 真实数据面不可得 | M2 卡住 | W7 变更源强制决策；fixtures 保评测 |
| LLM 上游 503 | 评测不稳 | 重试/冷却；评测标记 provider_degraded；不把偶发当功能失败 |
| 范围膨胀 | 3 个月做不完 | 非目标清单；每月出口砍 scope |
| 无人 OnCall | 不能宣称生产主路径 | E2 强制；否则 L3 只能「预生产白名单」 |

---

## 10. 决策清单（开工前 / 月中冻结）

| ID | 决策 | 最晚时点 | 建议 |
|---|---|---|---|
| D-M1-1 | force_expert_delegation：实现 or 删除 | W1 | 实现，默认 false |
| D-M1-2 | re-evidence 默认开还是先观测 | W1 | 默认开，max=1 |
| D-M2-1 | 变更源接入 or 永久降权 | W7 | 有源则 A，否则 B |
| D-M2-2 | 共享内核默认 true？ | W6 评审后 | true + 回滚开关 |
| D-M2-3 | aux 默认 parallel 还是 serial | W5 | parallel max 2 |
| D-M3-1 | 自动蒸馏默认是否开 | W9 | 预生产 false，半自动确认 |
| D-M3-2 | 是否取消 H3 豁免 | W9 | 指定真人则取消 |
| D-M3-3 | L3 是否进预生产白名单服务 | W12 | 指标达标才 Go |

---

## 11. 与现有计划的关系

| 已有计划 | 关系 |
|---|---|
| `oncall-agent-harness-unification.md` | 已基本落地；本路线图承接 Phase 后半（闭环/并行/统一内核） |
| `2026-07-08-stateful-agent-context.md` | M1 WP-CTX1 / WP-I1 完成其未勾选验收 |
| `2026-07-07-harness-loop-timeout-mitigation.md` | 并入 WP-L* 与 checkpoint timeout 落盘 |
| `memory-cache-layer.md` / 长期记忆 PRD | M2–M3 WP-D* 延续 |
| L1 pilot 文档 | 基线只读；本路线图不回退 L1 安全只读原则 |

---

## 12. 第一个 10 天启动包（可直接开干）

若立即进入实施，建议 **Day 1–10** 只做这些（避免一上来并行过多）：

1. **D-M1-1 / D-M1-2** 书面确认  
2. WP-H1 配置去漂移（低风险热身）  
3. WP-C1 变更边界 + N6 进最小集  
4. WP-A4 force_delegation 落地  
5. WP-A1 re-evidence 设计评审 + 实现 + 单测  
6. WP-F1 补 3 道题（含 1 道 re-evidence）  
7. WP-L1 启用 `LLM_PLANNER_MODEL` 轻量模型并跑 8 题对照  
8. 输出「Day10 对照表」：8 题分数 / P50 / 是否出现 re_evidence 事件  

**Day10 成功标准**：re-evidence 可演示；8/8 不回退；P50 不差于基线 +10%（允许小幅变差，但需有优化项在途）。

---

## 13. 一句话路线

> **M1 把闭环和评测补实，M2 把多专家和数据面接真，M3 把学习和平台补齐。**  
> 三个月后不是「更会聊天的 Bot」，而是 **可预生产、可回归、可降级、可与人协作的 OnCall Agent 系统**。

---

## 14. 附录：成熟度目标轨迹

```text
编排闭环     40% ──M1──▶ 70% ──M2──▶ 80% ──M3──▶ 85%
多Agent      35% ──M1──▶ 45% ──M2──▶ 75% ──M3──▶ 80%
数据面       40% ──M1──▶ 50% ──M2──▶ 70% ──M3──▶ 75%
记忆学习     55% ──M1──▶ 55% ──M2──▶ 70% ──M3──▶ 80%
HITL         25% ──────────────────M3──▶ 65%
评测飞轮     40% ──M1──▶ 65% ──M2──▶ 80% ──M3──▶ 85%
可观测       35% ──M1──▶ 45% ──M2──▶ 60% ──M3──▶ 75%
平台化       55% ──M1──▶ 60% ──M2──▶ 65% ──M3──▶ 80%
时延体验     35% ──M1──▶ 55% ──M2──▶ 70% ──M3──▶ 75%
```

综合目标：约 **55–60% → 80%+** 的「完整 Agent 系统」成熟度；产品上从 **L1 技术试点 → L3 预生产副驾**。
