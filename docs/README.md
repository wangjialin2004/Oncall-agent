# 文档索引（docs/）

> 更新：2026-07-13 — 正式 L1 Go + **M1 W1/W2 演进交接**；文档分类见下。  
> **接手请先看** → [`pilot/handoff-2026-07-13-l1-m1-w2.md`](./pilot/handoff-2026-07-13-l1-m1-w2.md)  
> （仅运维拉起/账号仍可看 [`pilot/handoff-2026-07-12-l1-pilot.md`](./pilot/handoff-2026-07-12-l1-pilot.md)）

---

## 目录结构

```text
docs/
├── README.md                 # 本索引
├── pilot/                    # L1 试点：决策 / 交接 / 验收 / 任务板
├── reviews/                  # 完成度评审 / 过程纪要（历史）
└── superpowers/
    ├── plans/                # 实施计划
    └── specs/                # 设计 / PRD
```

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| **`pilot/`** | Go/No-Go、交接、验收 checklist、任务板、产品边界声明 | 长篇过程日记 |
| **`reviews/`** | completion-review、阶段性进度纪要 | 当前有效决策（决策在 pilot） |
| **`superpowers/plans/`** | 可执行实施计划 | 运行态门禁 |
| **`superpowers/specs/`** | 设计说明 / PRD | 评测结果 JSON |

仓库根另有 `plan/`、`prd/`（历史计划与 PRD），与 `docs/superpowers/` 并存；索引以本目录为准。

### 演进主计划（当前）

| 优先级 | 文档 | 说明 |
|---|---|---|
| **P0** | [CLAUDE.md](../CLAUDE.md) | **Agent 协作约定：先计划后编码** |
| **P0** | [plan/2026-07-13-complete-agent-system-3-month-roadmap.md](../plan/2026-07-13-complete-agent-system-3-month-roadmap.md) | **L1 → L3 三个月路线图**：闭环 / 并行 / 数据面 / 学习 / HITL / 平台 |
| **P0** | [plan/2026-07-13-m1-w1-close-the-loop-implementation.md](../plan/2026-07-13-m1-w1-close-the-loop-implementation.md) | **M1 W1 实施计划**（re-evidence 等；含补录说明） |
| **P0** | [plan/2026-07-13-m1-w2-context-checkpoint-evidence-ci.md](../plan/2026-07-13-m1-w2-context-checkpoint-evidence-ci.md) | **M1 W2 实施计划** |
| P1 | [plan/2026-07-13-m1-day1-progress.md](../plan/2026-07-13-m1-day1-progress.md) / [m1-w2-progress](../plan/2026-07-13-m1-w2-progress.md) | W1/W2 进度 |

---

## 1. L1 试点 / 演进交接（`pilot/`）— 优先读

| 优先级 | 文档 | 说明 |
|---|---|---|
| **P0** | [handoff-2026-07-13-l1-m1-w2.md](./pilot/handoff-2026-07-13-l1-m1-w2.md) | **当前交接（L1 Go + M1 W1/W2）** |
| **P0** | [handoff-2026-07-12-l1-pilot.md](./pilot/handoff-2026-07-12-l1-pilot.md) | L1 运维拉起 / 账号 / H1–H6（仍有效） |
| **P0** | [go-nogo-20260712.md](./pilot/go-nogo-20260712.md) | **L1 决策与门禁表 / 签字栏** |
| P0 | [2026-07-10-pilot-readiness-checklist-and-eval-suite.md](./pilot/2026-07-10-pilot-readiness-checklist-and-eval-suite.md) | 验收标准 + 23 题定义 |
| P1 | [2026-07-11-pilot-readiness-task-board.md](./pilot/2026-07-11-pilot-readiness-task-board.md) | 执行任务板（状态可能落后，以 go-nogo 为准） |
| P1 | [2026-07-11-pilot-day0-baseline.md](./pilot/2026-07-11-pilot-day0-baseline.md) | Day0 基线快照 |
| P1 | [2026-07-10-module-gap-inventory.md](./pilot/2026-07-10-module-gap-inventory.md) | 模块差距清单（部分过时，以 07-13 交接为准） |
| P1 | [change-capability-unavailable.md](./pilot/change-capability-unavailable.md) | 变更能力不可用（D2） |

**当前决策摘要**：**Go L1 技术试点（正式，2026-07-13）**；H1 严格 8/8 ✅；H2 P50 书面接受 ✅；H6 tool 后 checkpoint ✅；H3 真人 OnCall **项目方豁免** ⚠️。工程侧 **M1 W1/W2 已合入**（见 07-13 交接）。

---

## 2. 完成度评审 / 过程纪要（`reviews/`）

| 文档 | 说明 |
|---|---|
| [completion-review-2026-07-18-security-tenant-reliability.md](./reviews/completion-review-2026-07-18-security-tenant-reliability.md) | **系统安全/租户隔离/可靠性收尾审查；代码条件通过，live migration、readiness/metrics、WP-8 仍阻塞** |
| [completion-review-2026-07-13-project-score.md](./reviews/completion-review-2026-07-13-project-score.md) | **07-13 全项目审查与综合打分（7.9/10）** |
| [completion-review-2026-07-13-formal-l1-go.md](./reviews/completion-review-2026-07-13-formal-l1-go.md) | **07-13 正式 L1 Go 决策落档** |
| [completion-review-2026-07-12-evening.md](./reviews/completion-review-2026-07-12-evening.md) | 07-12 晚间连续评测 + checkpoint |
| [completion-review-2026-07-12-afternoon.md](./reviews/completion-review-2026-07-12-afternoon.md) | 07-12 下午告警/凭据/合并评测 |
| [completion-review-2026-07-12-progress.md](./reviews/completion-review-2026-07-12-progress.md) | 07-12 进度摘记 |
| [completion-review-2026-07-11.md](./reviews/completion-review-2026-07-11.md) | 07-11 基线 / 早期 No-Go |
| [completion-review-2026-06-21-router-keyword-tiering.md](./reviews/completion-review-2026-06-21-router-keyword-tiering.md) | 路由关键词分级完成评审 |
| [completion-review-2026-06-20-harness-unification.md](./reviews/completion-review-2026-06-20-harness-unification.md) | Harness 统一编排完成评审 |

> 新的 completion-review 建议直接写到 `docs/reviews/completion-review-YYYY-MM-DD[-scope].md`。

---

## 3. Superpowers 计划（`superpowers/plans/`）

| 文档 |
|---|
| [2026-06-09-local-rag-evaluation-pipeline.md](./superpowers/plans/2026-06-09-local-rag-evaluation-pipeline.md) |
| [2026-06-17-long-term-memory-system.md](./superpowers/plans/2026-06-17-long-term-memory-system.md) |
| [2026-06-17-long-term-memory-system.zh-CN.md](./superpowers/plans/2026-06-17-long-term-memory-system.zh-CN.md) |
| [2026-06-23-frontend-command-center-redesign.md](./superpowers/plans/2026-06-23-frontend-command-center-redesign.md) |
| [2026-06-24-frontend-paper-evidence-redesign.md](./superpowers/plans/2026-06-24-frontend-paper-evidence-redesign.md) |

---

## 4. Superpowers 规格 / PRD（`superpowers/specs/`）

| 文档 |
|---|
| [2026-06-17-long-term-memory-system-prd.md](./superpowers/specs/2026-06-17-long-term-memory-system-prd.md) |
| [2026-06-17-router-experts-simplification-design.md](./superpowers/specs/2026-06-17-router-experts-simplification-design.md) |
| [2026-06-23-frontend-command-center-redesign-design.md](./superpowers/specs/2026-06-23-frontend-command-center-redesign-design.md) |
| [2026-06-24-frontend-paper-evidence-redesign-design.md](./superpowers/specs/2026-06-24-frontend-paper-evidence-redesign-design.md) |

---

## 5. 相关仓库路径（非 docs，但常一起看）

| 路径 | 说明 |
|---|---|
| `evals/oncall/` | 评测用例与 README |
| `evals/results/` | 评测 JSON/CSV 产物 |
| `logs/` | 运行日志、checkpoint 演练结果、`.pilot_pass`（敏感，勿提交） |
| `scripts/evaluate_oncall_local.py` | 本地 OnCall 评测 |
| `scripts/checkpoint_resume_drill.py` | Checkpoint kill/resume 演练 |
| `plan/` | 仓库根历史计划 |
| `prd/` | 仓库根 PRD |

---

## 6. 分类约定（以后怎么放）

| 你在写… | 放到 |
|---|---|
| 试点 Go/No-Go、交接、验收门槛 | `docs/pilot/` |
| 完成度评审、某日过程纪要 | `docs/reviews/` |
| 多周实施计划 | `docs/superpowers/plans/` |
| 设计 / PRD | `docs/superpowers/specs/` |
| 评测机器结果 | `evals/results/`（不要塞进 docs） |
| 运行日志 / 演练 JSON | `logs/` |

新增文档后请回写本 README 对应表格一行。
