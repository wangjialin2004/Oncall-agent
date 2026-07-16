# Agent Project Index

This file is the canonical project instruction and navigation entry for every coding agent working in this repository.

**所有模型共用的工作流与强制约定都在本文件。** 模型专属适配见 [CLAUDE.md](./CLAUDE.md)（Claude Code）和 [CODEX.md](./CODEX.md)（Codex）。

## Code Discovery

This project uses codebase-memory-mcp to maintain a knowledge graph of the codebase.
Always prefer MCP graph tools over grep/glob/file-search for code discovery.

Priority order:

1. `search_graph` - find functions, classes, routes, variables by pattern.
2. `trace_path` - trace who calls a function or what it calls.
3. `get_code_snippet` - read specific function/class source code.
4. `query_graph` - run Cypher queries for complex patterns.
5. `search_code` - graph-augmented code text search.
6. `get_architecture` - high-level project summary when available.

Fallback to `rg`/file reads for string literals, config values, non-code files, or when MCP results are insufficient.

## Instruction Governance

### Source of truth and precedence

Repository instructions are applied in this order: host/system and explicit user instructions, then this `AGENTS.md`, then a model adapter. A model adapter may explain host-specific workflow, but must not relax or replace the requirements in this file.

| Agent host | Read | Responsibility |
|---|---|---|
| Claude Code | `AGENTS.md` then `CLAUDE.md` | Apply shared policy; use only Claude-specific execution guidance from the adapter. |
| Codex | `AGENTS.md` then `CODEX.md` | Apply shared policy; use only Codex-specific execution guidance from the adapter. |
| Other models / IDE agents | `AGENTS.md` | Treat this file as complete policy; do not infer generic rules from a different model's adapter. |

### Shared mandatory workflow

- Before any non-trivial implementation, write an executable plan in `plan/` (or `docs/superpowers/plans/` where explicitly required) and add it to **Current Plan Index** below. A task is non-trivial when it adds a feature or flag, changes the harness main loop, changes API/event contracts, alters evaluations or CI, or spans a multi-file refactor.
- A plan must state the problem, decisions and defaults, scope and non-goals, affected files, flags, verification commands and exit criteria, risks, and rollback path. Ask for direction before a decision that changes defaults, has destructive impact, or changes a data-source strategy.
- During implementation, record material deviations. On completion, record verification evidence in a progress document or the plan, then update the index status.
- A single typo/comment correction, read-only investigation, or a user-provided one-file change under 20 lines may skip a formal plan; still state intent and validate proportionately.

### Shared engineering and safety boundaries

- Default to read-only diagnosis. Do not implement automatic restart, rollback, scaling, or other production remediation executors.
- New capabilities require an environment-variable degradation switch; document the default in the plan. Keep checkpoint replay conservative unless a reviewed plan explicitly changes it.
- Preserve compatible SSE event shapes. Prefer existing `agent_event`, `tool_event`, and `decision_event`; adding a `stage` is allowed, but do not casually change `type` semantics.
- Keep `.env`, real tokens, passwords, and pilot pass markers out of commits and documentation. Do not weaken authentication, CORS, or owner authorization.
- Keep `app/agent/harness/*.py` files at or below 1000 lines. Do not add new flag logic to the `loop.py` facade when an existing focused mixin/module is appropriate.
- New behaviour needs focused tests. Harness changes require the relevant unit tests plus one existing soft-path regression. Record reusable evaluation commands and results in the progress record.

### Documentation responsibilities

| Content | Canonical location |
|---|---|
| Shared rules, safety boundaries, navigation, active plan links | `AGENTS.md` |
| Claude Code host guidance only | `CLAUDE.md` |
| Codex host guidance only | `CODEX.md` |
| Executable implementation plans | `plan/` |
| Milestone progress and validation evidence | `plan/*-progress.md` |
| Pilot decisions and handoffs | `docs/pilot/` |

Model adapters must remain short references to this policy. They must not duplicate project roadmaps, architecture inventories, safety boundaries, or plan indexes.

## Current Plan Index

- [本地 BGE-M3 Embedding 替换 DashScope](plan/2026-07-16-local-bge-m3-embedding.md) - **已实现并验证**；默认 `local_bge_m3`，DashScope 可回退；ci-smoke 56 passed（无 key）
- [工作区差异分流与审查](plan/2026-07-16-working-tree-triage.md) - **已完成并验证**；隔离本地恢复/临时文件，格式检查通过，索引保持为空
- [Git 元数据恢复](plan/2026-07-16-git-metadata-recovery.md) - **已完成并验证**；重建 Git 元数据并保留 252 项工作区差异，旧指针与备份可审计
- [模型无关 Agent 治理文档](plan/2026-07-16-model-agent-governance.md) - **已完成并验证**；共享规则已收敛至本文件，`CLAUDE.md` / `CODEX.md` 仅保留模型适配

- [交接：Harness loop 拆分 · 测试硬化](docs/pilot/handoff-2026-07-16-harness-loop-split.md) - **2026-07-16 工程债交接**；`loop.py` 门面化 + `test_harness_service` 67 passed；产品 L3 Conditional 仍以前序交接为准
- [过程栏关键结果乱码与符号噪声修复](plan/2026-07-16-process-key-result-mojibake.md) - **已实现并验证**；旧经验召回字段动态清洗，关键结果结构化显示摘要、置信度、相似度和症状；后端 76 passed、前端 76 tests + build 通过
- [助手消息工具协议泄漏与中文乱码修复](plan/2026-07-16-assistant-tool-protocol-and-mojibake-guard.md) - **已实现**；隔离 planner/re-evidence/replan 工具决策文本，修复运行时乱码并兼容历史脏消息；见 [进度](plan/2026-07-16-assistant-tool-protocol-and-mojibake-guard-progress.md)
- [智能体过程栏信息架构与步骤展示优化](plan/2026-07-15-agent-process-panel-information-architecture.md) - **已实现，待 Git 元数据恢复后提交**；原始 SSE 事件归并为业务步骤，修正状态/计数，详情分层并保留 V1 回退；前端 70 tests + build + 3 档浏览器 QA 通过
- [Harness loop 拆分与简化](plan/2026-07-15-harness-loop-split-simplify.md) - **已合入**；`loop.py` 3672→门面+mixin（单文件≤1000）；见 [进度](plan/2026-07-15-harness-loop-split-progress.md) · [交接](docs/pilot/handoff-2026-07-16-harness-loop-split.md)
- [CLAUDE.md — 项目协作约定](CLAUDE.md) - **先计划后编码**；M1 焦点；红线与文档规范
- [完整 Agent 系统差距 → 3 个月演进路线图](plan/2026-07-13-complete-agent-system-3-month-roadmap.md) - L1 Go 基线 → M1 闭环/时延/评测 → M2 并行委派/数据面/共享内核 → M3 自学习/HITL/预生产（L3）
- [M1 W1 实施计划：Close the Loop](plan/2026-07-13-m1-w1-close-the-loop-implementation.md) - re-evidence / force_delegation / 变更边界 / 评测；**2026-07-13 补录，Day1 已按此落地**
- [M1 W2 实施计划：Context / Checkpoint / Evidence / CI](plan/2026-07-13-m1-w2-context-checkpoint-evidence-ci.md) - 多轮白板、超时落盘、证据细匹配、ci-smoke
- [M1 W2 进度](plan/2026-07-13-m1-w2-progress.md) - W2 已合入与验证
- [M1 W3 实施计划：Replan / 时延 / 评测扩面](plan/2026-07-13-m1-w3-replan-latency-eval.md) - replan + 时延 + 评测 ≥18；**主干已合入**
- [M1 W3 进度](plan/2026-07-13-m1-w3-progress.md) - W3 已合入；10 题 eval 9/10、P50 165s
- [M1 W4 实施计划：时延攻坚 / L1.5 出口](plan/2026-07-13-m1-w4-latency-l15-exit.md) - P50 攻坚 + 回归 + L1.5 评审；**主干已合入**
- [M1 W4 进度](plan/2026-07-13-m1-w4-progress.md) - knowledge early close；对照 P50 165→110s
- [L1.5 出口评审](docs/pilot/l15-exit-review-2026-07-13.md) - **Conditional Go**
- [M3 L3 交接（2026-07-15）](docs/pilot/handoff-2026-07-15-m3-l3-conditional.md) - **当前接手主文档**；L3 Conditional；full `213510` 21/23
- [M3 W9 交接](docs/pilot/handoff-2026-07-14-m3-w9.md) - 历史；W9 full 23/23 强化证据
- [L1.5/M2/L2 交接](docs/pilot/handoff-2026-07-14-l15-conditional.md) - L2 full 基线细节（前序）
- [L2 出口评审](docs/pilot/l2-exit-review-2026-07-14.md) - **Conditional Go**；W9 后 full 强化证据 **23/23** / Core **5/5**
- [L3 出口评审](docs/pilot/l3-exit-review-2026-07-15.md) - **Conditional Go**；预生产白名单
- [M2 W5 实施计划：时延与评测硬化](plan/2026-07-13-m2-w5-latency-eval-hardening.md) - 压 P50/P95；RE1 强制 re-evidence、RE2 受控 replan；修正评测事件与降级评分
- [M2 W5 进度](plan/2026-07-13-m2-w5-latency-eval-hardening-progress.md) - 69 项主回归通过；真实触发已验证；最新 minimal P50/P95 改善但仍非出口通过
- [M2 W6 实施计划：并行委派 · Aux 真执行](plan/2026-07-14-m2-w6-parallel-delegation-aux.md) - `delegate_parallel` + aux probe + 评测 23 题 + 共享内核设计（文档）
- [M2 W6 进度](plan/2026-07-14-m2-w6-parallel-delegation-progress.md) - **主干已合入**；72 项主回归通过；live minimal 待健康环境重跑
- [M2 共享内核设计评审](plan/2026-07-14-m2-shared-kernel-design.md) - W6 预研；W7 已实现 sub_harness
- [M2 W7 实施计划：共享内核 · 变更源 B](plan/2026-07-14-m2-w7-shared-kernel-change-decision.md) - WP-B2/C4-B/B4/D1 草案
- [M2 W7 进度](plan/2026-07-14-m2-w7-shared-kernel-change-progress.md) - **主干已合入**；77 项主回归；变更源正式 option B
- [M2 自动蒸馏草案](plan/2026-07-14-m2-auto-distill-draft.md) - WP-D1 文档 only
- [M2 W8 实施计划：全量评测 · L2 出口](plan/2026-07-14-m2-w8-l2-exit-full-eval.md) - B4 白板合并 + suite=full + L2 评审
- [M2 W8 进度](plan/2026-07-14-m2-w8-l2-exit-progress.md) - **主干已合入**；full live **20/23**、P50 **64s**；L2 **Conditional Go**
- [L2 出口评审](docs/pilot/l2-exit-review-2026-07-14.md) - Conditional Go；full 基线已回填
- [M3 总览](plan/2026-07-14-m3-overview.md) - Learn & Platform；W9–W12 地图
- [M3 W9 实施计划：L2 残留 · HITL · 指标](plan/2026-07-14-m3-w9-l2-residuals-hitl-metrics.md) - W9 计划（live 已收口）
- [M3 W9 进度](plan/2026-07-14-m3-w9-progress.md) - selected 3/3 + full **23/23** Core **5/5**（`215932`）；下一棒 W10 计划
- [M3 W10 实施计划：蒸馏 · 反模式 · 澄清 · Compose](plan/2026-07-14-m3-w10-learn-clarify-compose.md) - 半自动蒸馏 confirm + 失败反模式 + 澄清 + compose 草案
- [M3 W10 进度](plan/2026-07-14-m3-w10-progress.md) - **主干已合入**；单测 16 passed；健康 live 蒸馏闭环
- [M3 W11 实施计划：观测飞轮 · Online · 双路径 · OTEL](plan/2026-07-15-m3-w11-observability-online.md) - 成本指标/看板、online 抽样、上下文收敛、OTEL 可选
- [M3 W11 进度](plan/2026-07-15-m3-w11-progress.md) - **主干已合入**；tokens/tools + sample rate + online 模板；**非 L3**
- [M3 W12 实施计划：全量回归 · 北极星 · L3 出口](plan/2026-07-15-m3-w12-l3-exit.md) - Phase 1–3 完成
- [M3 W12 进度](plan/2026-07-15-m3-w12-progress.md) - full `213510` **21/23**；**Conditional Go L3**
- [下季度 Backlog](plan/2026-07-15-next-quarter-backlog.md) - S1/N4 · P50/P95 · H3 · online 人工分
- [Q-Next W1 实施计划：L3 出口强化](plan/2026-07-15-q-next-w1-exit-hardening.md) - **当前下一棒**；S1 复跑定性 · N4 注入守卫/评分 · 时延基线（可选 Phase B）
- [接手记录 2026-07-13](plan/2026-07-13-m1-handoff-intake-note.md) - 接手第一小时
- [M1 Day1 进度](plan/2026-07-13-m1-day1-progress.md) - W1 已合入项与验证命令
- [状态化 Agent 上下文实施计划](plan/2026-07-08-stateful-agent-context.md) - State-based ContextState whiteboard; Redis primary + DB snapshot fallback; demote rolling summary/token window/memory cache from main path; trim checkpoint to recovery metadata only. **(待审批 — 见下方 2026-07-08 审批记录)**
- [Harness Loop 超时治理计划](plan/2026-07-07-harness-loop-timeout-mitigation.md) - Diagnose and mitigate Harness model-decision/tool/expert timeout loops.
- [Plan：升级短期记忆召回窗口（解决"只记住最近 6 轮"）](plan/2026-06-22-short-term-memory-recall-window.md) - Short-term memory recall window improvement.
- [Plan：路由层关键词分级 + 弱词进语义](plan/2026-06-21-router-keyword-tiering.md) - Router keyword tiering and semantic fallback.
- [Checkpoint Resume 修复总结](plan/checkpoint-resume-fix-summary.md) - Checkpoint resume fix summary.
- [Plan：前端石墨蓝 + 青色配色 / 排版与信息密度](plan/frontend-beautify-graphite-cyan.md) - Frontend visual polish plan.
- [Plan：前端极简纸感精品工作室升级](plan/frontend-polish-premium-paper.md) - Premium paper-style frontend polish plan.
- [Plan：记忆子系统缓存层规划](plan/memory-cache-layer.md) - Memory subsystem cache layer plan.
- [OnCall Agent 系统 Harness 化计划书（统一编排循环）](plan/oncall-agent-harness-unification.md) - Unified Harness orchestration plan.
- [Plan：智能体过程栏可拖拽调整宽度 + 文字自适应](plan/plan-resizable-agent-process-panel.md) - Resizable agent process panel plan.

## Superpowers Plans

- [Local RAG Evaluation Pipeline Implementation Plan](docs/superpowers/plans/2026-06-09-local-rag-evaluation-pipeline.md)
- [Long-Term Memory System Implementation Plan](docs/superpowers/plans/2026-06-17-long-term-memory-system.md)
- [长期记忆系统实施计划](docs/superpowers/plans/2026-06-17-long-term-memory-system.zh-CN.md)
- [Frontend Command Center Redesign Implementation Plan](docs/superpowers/plans/2026-06-23-frontend-command-center-redesign.md)
- [Frontend Paper Evidence Redesign Implementation Plan](docs/superpowers/plans/2026-06-24-frontend-paper-evidence-redesign.md)

## Specs And PRDs

- [PRD：智能 OnCall Agent 长期记忆系统](docs/superpowers/specs/2026-06-17-long-term-memory-system-prd.md)
- [Router Experts Simplification Design](docs/superpowers/specs/2026-06-17-router-experts-simplification-design.md)
- [Frontend Command Center Redesign Design](docs/superpowers/specs/2026-06-23-frontend-command-center-redesign-design.md)
- [Frontend Paper Evidence Redesign Design](docs/superpowers/specs/2026-06-24-frontend-paper-evidence-redesign-design.md)
- [PRD：前端极简纸感精品工作室升级](prd/frontend-polish-premium-paper.md)
- [PRD：智能体过程栏可拖拽调整宽度 + 文字自适应](prd/prd-resizable-agent-process-panel.md)

## Docs Index

- [docs/README.md](docs/README.md) — 文档分类索引（pilot / reviews / superpowers）

## L1 Pilot（当前）

- [M3 L3 交接（2026-07-15）](docs/pilot/handoff-2026-07-15-m3-l3-conditional.md) — **当前接手主文档**；L3 Conditional；full `213510` 21/23；H3 仍豁免
- [L3 出口评审（2026-07-15）](docs/pilot/l3-exit-review-2026-07-15.md) — **Conditional Go**；P50 89s / Core 4/5
- [北极星 N1–N10](docs/pilot/north-star-n1-n10-2026-07-15.md) — 已用 W12 full 回填
- [预生产 Runbook](docs/pilot/preprod-runbook.md) — 白名单 + 回滚 L4
- [M3 W9 交接](docs/pilot/handoff-2026-07-14-m3-w9.md) — 历史；W9 full 23/23 强化证据
- [L1.5/M2/L2 交接](docs/pilot/handoff-2026-07-14-l15-conditional.md) — L2 full 基线与 M2 开关细节
- [L2 出口评审](docs/pilot/l2-exit-review-2026-07-14.md) — Conditional Go；W9 后可附 `215932` 强化证据
- [M3 W9 计划/进度](plan/2026-07-14-m3-w9-l2-residuals-hitl-metrics.md) / [进度](plan/2026-07-14-m3-w9-progress.md) — selected 3/3 + full 23/23 已收口
- [M3 W10 计划/进度](plan/2026-07-14-m3-w10-learn-clarify-compose.md) / [进度](plan/2026-07-14-m3-w10-progress.md) — **主干已合入** + 健康 live
- [M3 W11 计划/进度](plan/2026-07-15-m3-w11-observability-online.md) / [进度](plan/2026-07-15-m3-w11-progress.md) — **主干已合入**
- [M3 W12 计划/进度](plan/2026-07-15-m3-w12-l3-exit.md) / [进度](plan/2026-07-15-m3-w12-progress.md) — full **21/23**；**Conditional L3**
- [L1.5 出口评审](docs/pilot/l15-exit-review-2026-07-13.md) — Conditional Go；P50 110s
- [L1 + M1 W1/W2 交接（2026-07-13）](docs/pilot/handoff-2026-07-13-l1-m1-w2.md) — W1/W2 演进细节
- [L1 试点交接（运维拉起）](docs/pilot/handoff-2026-07-12-l1-pilot.md) — 拉起、评测、H1–H6；**2026-07-13 正式 Go L1**
- [Go/No-Go 2026-07-12](docs/pilot/go-nogo-20260712.md) — 门禁表与签字栏（**正式 Go，H3 豁免**）
- [变更能力 option B](docs/pilot/change-capability-unavailable.md) — 变更源正式永久降权
- [模块差距清单（文件/开关对照）](docs/pilot/2026-07-10-module-gap-inventory.md)
- [试点验收 Checklist + 场景评测集](docs/pilot/2026-07-10-pilot-readiness-checklist-and-eval-suite.md)

## Completion Reviews

- [Completion Review Report: Agent 系统 Harness 化（统一编排循环）](docs/reviews/completion-review-2026-06-20-harness-unification.md)
- [Completion Review Report: 路由层关键词分级 + 弱词进语义](docs/reviews/completion-review-2026-06-21-router-keyword-tiering.md)
- 更多见 [docs/reviews/](docs/reviews/)

## 2026-07-08 计划审批记录

**计划:** `plan/2026-07-08-stateful-agent-context.md`(状态化 Agent 上下文)
**结论:** ✅ **已批准,可以进入实施阶段**,但需在执行 Task 1–10 时遵守以下补充约束。

### 必须遵守的强约束(实施前落实到代码与配置)

1. **新增 `app/agent/context/` 必须为全新目录,不能复用旧 `app/agent/harness/` 任何 state/builder 模块的命名**
   - 已核对:`app/agent/context/` 当前不存在 ✅ 新建不影响现有 import。
2. **Task 3 复用 `app/services/redis_client.py` 现成 async client,禁止新建连接池**
   - 该文件在仓库中存在(已核对)✅ 注意其内部已含 chat session 用法,接入前先评估 key 命名空间是否冲突。
3. **Task 4 DB snapshot 入口优先选用 `app/services/conversation_service.py` 现成 metadata 字段**,新增表/服务仅在 metadata 字段确实放不下时才允许。
4. **Task 6 降级路径必须保留**:`harness_stateful_context_enabled=False` 时与今天运行的行为完全一致 — 在切换开关前后做一次 diff 验证。
5. **Task 9 checkpoint 瘦身要先做 schema_version 兼容**,旧 checkpoint 的 `messages/context_snapshot` 只读,不写;写入路径直接去掉对应字段。`save_step` 必须新增 `context_version/context_snapshot_ref` 参数(默认值 `None`,向后兼容)。
6. **Task 10 必须写出显式断言/单测**:Harness 上下文构建链路中**任何代码路径都不应 import `memory_cache` 或 `experience_memory_service`**。建议做成 grep + AST 静态检查 + 单测三重防线。

### 必须保留的退出条件(避免功能回退)

- 第 12 节验收标准 10 条全部逐项勾选再合并。
- P50 / 超时率指标(验收标准第 10 条)用 `logs/` 里现成的 7/7 harness loop 数据做对照基线,而不是依赖合成数据。
- 三级回退开关(`enabled=false` / `db_snapshot=false` / `tools=false`)必须分别有独立 env 变量与生效断言,不能用一个开关覆盖三级。

### 批准的可选范围

- Task 2 Operations 模块允许把 `working.plan` / `working.completed_steps` / `working.pending_steps` 列入 LLM 可写字段(计划本身已声明),但 `evidence.observed_facts` 与 `identity` 必须 firm-only。
- Task 5 view 渲染允许自定义格式(只要满足 budget 约束即可)。
- Task 7 工具结果摘要 schema 可自行细化,但 `raw_ref` 字段必须保留并指向 `timeline_events` 中具体 event id。

### 暂缓/不接受的部分

- **不接受** "以进程内 Map 为主存储" 的解读,即使第一版可做 LRU,也要确保 Redis 写入是默认主路径。
- **不接受** 把 `context_patch` / `context_rollback` 暴露给 LLM。
- **不接受** LLM 写 `observed_facts`。

### 实施顺序建议

按文件改动半径从小到大:**Task 1 → 2 → 5 → 4 → 3 → 7 → 8 → 6 → 9 → 10**。每完成一个 Task 就跑一遍 `tests/test_harness_stateful_context.py` 增量测试,避免一次性引入大量变更。

---

## AIOps Knowledge Docs

- [CPU使用率过高告警处理方案](aiops-docs/cpu_high_usage.md)
- [磁盘使用率过高告警处理方案](aiops-docs/disk_high_usage.md)
- [内存使用率过高告警处理方案](aiops-docs/memory_high_usage.md)
- [服务不可用告警处理方案](aiops-docs/service_unavailable.md)
- [服务响应时间过长告警处理方案](aiops-docs/slow_response.md)
