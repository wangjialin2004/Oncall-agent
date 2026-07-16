# M1 W4 实施计划：时延攻坚 · M1 回归 · L1.5 出口评审

> **角色边界**：本文件描述「怎么做」。**用户明确授权「按 W4 计划实现 / 开始做」后**，再改 harness 业务代码。  
> **日期**：2026-07-13  
> **状态**：**主干已实施**（见 [progress](./2026-07-13-m1-w4-progress.md)）；L1.5 正式签字待对照重跑  
> **交接入口**：[docs/pilot/handoff-2026-07-13-l1-m1-w2.md](../docs/pilot/handoff-2026-07-13-l1-m1-w2.md)  
> **上级**：
> - [3 个月路线图](./2026-07-13-complete-agent-system-3-month-roadmap.md) §2.3 W4、§2.4 L1.5 出口  
> - [M1 W3 计划/进度](./2026-07-13-m1-w3-progress.md)（replan/评测已合入；P50 未达标）  
> - [CLAUDE.md](../CLAUDE.md)（先计划后编码）

---

## 0. 一页摘要

| 项 | 内容 |
|---|---|
| 主题 | 把 P50 从 **165s** 压到 L1.5 门禁；M1 全量回归；完成 **L1.5 Go/Conditional/No-Go** 书面评审 |
| 范围 WP | **WP-L1b** 时延深挖 · **WP-F1b** 评测回归/S5/扩展 · **WP-I1-d** rehydrate 收口 · **WP-H1** 配置债 · **WP-EXIT** L1.5 出口文档 |
| 明确不做 | 并行委派 fan-out（M2 WP-B1）、共享内核、aux 真执行、自动处置、HITL 执行器、变更源真接入 |
| 入口证据 | 最小 10 题 `oncall_minimal_20260713_205114`：**9/10**；P50 **165s**；S5 `client_timeout` |
| 出口 | L1.5 评审表勾选 + 新一轮 minimal/extended 报告 + 单测绿 |

---

## 1. Context（现状与问题）

### 1.1 M1 累计已落地

| 周 | 能力 | 状态 |
|---|---|---|
| W1 | re-evidence / force_delegation / 变更 gap / RE1+N6 | ✅ |
| W2 | recent_turns stamp / timeout ckpt / evidence match / ci-smoke | ✅ |
| W3 | replan / 动态 max_steps / 并行 tool / cases **20** / eval suite | ✅ 代码 |
| W3 eval | 10 题 9/10；P50 165s；re/replan 事件率 0 | ⚠️ 时延与观测 |

### 1.2 L1.5 门禁对照（路线图 §2.4）

| 门禁 | Go | Conditional | No-Go | **当前** |
|---|---|---|---|---|
| 严格 8/8 质量 | 通过 | 通过 | 失败 | 近似 9/10；**S5 超时**未过 |
| re-evidence | 有事件 + 单测 | 仅可关观测 | 无实现 | 单测 ✅；真跑 **事件率 0**（实现在，观测弱） |
| P50 | ≤100s | ≤120s + backlog | >120s 无解释 | **165s** → 需解释+攻坚 |
| Checkpoint | timeout 落盘 + rehydrate | 仅其一 | 回退 | 落盘 ✅；深层 rehydrate ⬜ |
| CI | pytest+frontend 自动 | 仅 Makefile | 无 | ci-smoke ✅（GH + 本地 pytest）；frontend 未进 smoke |

### 1.3 时延问题拆解（基于 20260713_205114）

| 观察 | 含义 | W4 动作 |
|---|---|---|
| S2/S3 ~210s 且路由 **knowledge** | 动态 max_steps=3 仍极慢 → 瓶颈在 **LLM 调用/工具（RAG/Milvus）** 而非步数 | 模型分层本机生效；knowledge 早停；RAG 超时硬顶 |
| S5 180s `client_timeout` | 客户端 120+60 不够；服务端可能仍在跑 | 评测预算与 harness timeout 对齐；单题复测 |
| RE1 20s 通过但 re=0 | 模型首轮直接调工具，不触发 re_evidence | 保留单测；加 **fixture/脚本级** 可观测 case 或 eval 记 `tools_first_turn` |
| N3 21s / N1 58s | 负例路径可快 | 作为快路径对照 |
| P50 165 > L1 138 | W3 未改善时延 | **W4 主攻** |

### 1.4 非目标（W4 不做）

1. `delegate_parallel` / aux 并行执行（M2）  
2. 专家共享 harness 内核（M2）  
3. 变更源生产接入  
4. 自动处置 / HITL 确认执行  
5. 把 P50 冲到 M3 的 75s（那是 M2/M3 目标）  
6. 前端大改  

---

## 2. 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| D-W4-1 L1.5 时延目标 | **冲 Go：P50≤100s**；保底 **Conditional：P50≤120s** 且书面 backlog | 对齐路线图；本机 165s 需多杠杆 |
| D-W4-2 模型分层 | 本机 `.env` 配置 `LLM_PLANNER_MODEL` 轻量模型；**不**把具体商用模型名写死进 git 默认 | 代码路径已有 `_planner_model()` |
| D-W4-3 knowledge 快路径 | knowledge 路由：动态 steps=3 **且** 首轮无 tool 时直接 reasoner 收口可选；工具仅 knowledge/retrieve | 砍空转 |
| D-W4-4 工具/总超时 | 收紧默认或按路由：`HARNESS_TIMEOUT_SECONDS` 与 eval 对齐；tool timeout 保持可读失败快 | 避免 210s 挂死 |
| D-W4-5 S5 | 先 **加大 eval timeout 复测** 区分「真慢」vs「预算误伤」；再决定是否改 case `max_latency_seconds` | 当前 score=9 被 timeout 杀掉 |
| D-W4-6 re-evidence 观测 | 单测已够 Go 的「有实现」；真跑补 **1 个强制无工具首轮** 的 mock/集成测或 eval 标记 | 不强求线上每题触发 |
| D-W4-7 rehydrate | W4 做 **最小可验收**：resume 时 store miss → 尝试 DB snapshot；失败 emit `context_rehydrate_failed` | 补 Checkpoint 门禁 |
| D-W4-8 出口文档 | 新建 `docs/pilot/l15-exit-review-2026-07-13.md`（或当日日期）Go/Conditional/No-Go 表 + 签字栏 | 可审计 |
| D-W4-9 配置债 | 扫 `config` 注释/默认/`.env.example` 与 W1–W3 开关一致；health 不误报 | WP-H1 收口 |
| D-W4-10 默认开关 | 时延相关新开关 **可关**；默认偏「更快」但保留回滚 | 与 M1 哲学一致 |

### 2.1 需用户拍板（建议默认）

| # | 问题 | 建议 |
|---|---|---|
| Q1 | L1.5 接受 Conditional（P50≤120）还是必须 Go（≤100）？ | **先冲 ≤100，达不到则 Conditional + backlog 进 M2** |
| Q2 | 本机是否配置轻量 `LLM_PLANNER_MODEL`？ | **是**（只写本机 `.env`，示例进 `.env.example`） |
| Q3 | S5 的 `max_latency_seconds` 是否从 120 提到 180？ | **评测脚本 extra 先加；case 上限可调到 180** |
| Q4 | frontend 是否并入 ci-smoke？ | **W4 可选**；不阻塞 L1.5 Conditional |

---

## 3. 工作包明细

### WP-L1b · 时延深挖（P0）

| 杠杆 | 动作 | 文件 |
|---|---|---|
| L1b-1 模型分层落地 | 文档 + 本机 `.env` 指引；审计所有 LLM call site 使用 planner/reasoner；空 planner 时 startup/log **一次** warning | `loop.py`、`config`、`.env.example`、可选 `health` |
| L1b-2 knowledge 早停 | `effective_max_steps` 已=3；增强：knowledge 且已有成功 retrieve → 下一步强制 no-tool close | `loop.py` |
| L1b-3 路由级 timeout | 可选 `HARNESS_ROUTE_TIMEOUT_PROFILE=true`：knowledge 总超时更短（如 90s）、diagnosis 保持 | `config` + `loop` limits |
| L1b-4 工具失败快 | 确认 tool timeout/retry 不在不可达 Milvus 上叠成分钟级；knowledge 工具失败快速 gap | tools / executor |
| L1b-5 观测 | eval 行级已有 latency/steps/tools；补 `model_planner`/`model_reasoner` 若可从日志解析则写入 progress | eval 脚本可选 |
| L1b-6 对照跑 | 优化后重跑 **minimal 10**；与 `20260713_205114` 对照表写入 progress | `evals/results/` |

**验收**：新 P50 ≤100s（Go）或 ≤120s（Conditional）且 progress 写明未达标原因与 M2 backlog。

### WP-F1b · 评测回归（P0）

| 项 | 动作 |
|---|---|
| F1b-1 | S5 单题：`--timeout-extra 120` 或 case max_latency=180 复测 |
| F1b-2 | minimal 10 全绿或仅解释性失败 |
| F1b-3 | extended 20 题至少跑一轮（可隔夜）；记录通过率 |
| F1b-4 |（可选）补 K3/N2 逼近 23；不阻塞出口 |
| F1b-5 | RE 可观测：集成测或 eval 统计「首轮无工具→re_evidence」路径（单测已有则文档引用） |

### WP-I1-d · Context rehydrate 收口（P1）

| 项 | 动作 |
|---|---|
| resume 时 | 若 checkpoint 含 `context_snapshot_ref` 且 store miss → DB snapshot 拉取 |
| 失败 | timeline `context_rehydrate_failed`，不静默 |
| 单测 | mock miss + 成功/失败两条 |
| 非目标 | 不新造存储引擎 |

### WP-H1 · 配置与注释去漂移（P1）

| 项 | 动作 |
|---|---|
| `.env.example` | W1–W4 全部开关有注释默认值 |
| `config.py` | 过时「Disabled by default」类注释清零 |
| 差距清单 | `docs/pilot/2026-07-10-module-gap-inventory.md` 标已解决/有意保留 |
| Windows | 文档注明无 `make` 时用 pytest 等价（已有则强化） |

### WP-EXIT · L1.5 出口评审（P0）

产出文档：`docs/pilot/l15-exit-review-YYYYMMDD.md`

内容最低结构：

1. 门禁表（§2.4）逐项证据链接（结果 JSON / 单测 / 开关）  
2. 结论：Go / Conditional / No-Go  
3. 残留风险与 M2 输入  
4. 签字栏（工程 / 产品；H3 豁免状态重申）  
5. 回滚与降级速查  

同步更新：`handoff`、`AGENTS.md`、`CLAUDE.md` 焦点 → M2 或 Conditional backlog。

---

## 4. 实施步骤（可验收）

### Step 0 — 基线冻结

1. 确认 W3 资产：`20260713_205114` 为 W4 前基线  
2. ci-smoke 44 passed 再确认  
3. 用户确认 §2.1 Q1–Q4  

### Step 1 — WP-L1b 时延（先测量后改）

1. 审计 LLM/tool 耗时点（日志或一次手工 SSE）  
2. 落地 L1b-1～L1b-4 中 **低风险**项（模型分层配置、knowledge 早停、失败快）  
3. 单测：knowledge 早停 / 路由 timeout profile（若做）  
4. 重跑 minimal 10 → 对照表  

### Step 2 — WP-F1b 评测

1. S5 复测  
2. minimal 全量  
3. extended 尽量跑  
4. 更新 `evals/oncall/README` 若 case 有变  

### Step 3 — WP-I1-d + WP-H1

1. rehydrate 最小实现 + 单测  
2. 配置债清扫  

### Step 4 — WP-EXIT

1. 写 L1.5 出口评审文档  
2. 更新 handoff / 索引  
3. 若 Conditional：列出 M2 第一周必须项（并行委派 vs 继续时延）  

---

## 5. 文件清单

| 文件 | 变更 |
|---|---|
| `app/config.py` / `.env.example` | 路由 timeout profile 等新开关（若做） |
| `app/agent/harness/loop.py` | knowledge 早停、超时 profile、rehydrate 钩子 |
| `app/agent/context/integration.py` 等 | rehydrate |
| `scripts/evaluate_oncall_local.py` | timeout 默认/文档 |
| `evals/oncall/cases.jsonl` | S5 预算等微调 |
| `tests/test_m1_w4_latency_exit.py` | **新建** |
| `docs/pilot/l15-exit-review-*.md` | 出口评审 |
| `plan/2026-07-13-m1-w4-progress.md` | 实现后进度 |
| `AGENTS.md` / `CLAUDE.md` / handoff | 索引与状态 |

---

## 6. 开关一览

```text
# --- 已有（保持）---
HARNESS_RE_EVIDENCE_ENABLED=true
HARNESS_REPLAN_ENABLED=true
HARNESS_DYNAMIC_MAX_STEPS=true
HARNESS_PARALLEL_TOOL_CALLS=true
HARNESS_EVIDENCE_MATCH_ENABLED=true

# --- W4 候选新增 ---
# HARNESS_ROUTE_TIMEOUT_PROFILE=true
# HARNESS_KNOWLEDGE_EARLY_CLOSE=true   # knowledge 有证据后强制收口
# LLM_PLANNER_MODEL=<本机轻量模型>
# LLM_REASONER_MODEL=<深度模型>
```

降级：关 early_close / route timeout profile；planner 置空回退默认模型。

---

## 7. 验证方式

### 7.1 工程门禁

```bash
python -m pytest \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_harness_verifier.py \
  tests/test_harness_checkpoint.py \
  tests/test_context_integration.py \
  -q --tb=line --no-cov
```

### 7.2 场景评测

```bash
# S5 单题
python scripts/evaluate_oncall_local.py --case S5-slow-response --timeout-extra 120

# 最小 10
python scripts/evaluate_oncall_local.py --suite minimal --timeout-extra 90

# 扩展 20
python scripts/evaluate_oncall_local.py --suite extended --timeout-extra 90
```

### 7.3 L1.5 出口标准（汇总）

| 项 | 通过线 |
|---|---|
| 单测 / ci-smoke | 全绿 |
| minimal 质量 | ≥9/10；Core ≥4/5；N1/N3/M1/N6 过 |
| re-evidence | 单测证明路径 + 代码开关可关 |
| P50 | ≤100 Go / ≤120 Conditional |
| Checkpoint | timeout 落盘 + rehydrate 有测 |
| 文档 | 出口评审落盘并挂索引 |

---

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 过度砍 steps 损害 diagnosis 质量 | 早停仅 knowledge；diagnosis 保持 |
| 轻量 planner 降智 | 仅决策步；final 仍 reasoner；可关 |
| 评测超时误杀 | 统一 timeout-extra；与服务端 timeout 文档对齐 |
| L1.5 强行 Go 时延不实 | 诚实 Conditional，不改阈值粉饰 |
| rehydrate 引入 resume 回归 | 开关 + 单测；失败可观测 |

---

## 9. 与 M2 边界

| W4 交付 | M2 再做 |
|---|---|
| 串行 harness 时延极限 | 并行委派换时延 |
| cases ~20–23 | 全量 23 周基线 |
| L1.5 出口 | L2 协作诊断出口 |
| 变更仍声明不可用 | 接入只读变更源或永久降权 ADR |

---

## 10. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-13 | 据 W3 eval 起草 W4 |
| 2026-07-13 | 授权后合入 knowledge early close / route profile / rehydrate 事件 / S5 预算 / 出口草稿 |

---

## 11. 授权检查清单（实现前）

- [ ] 用户已读本计划  
- [ ] §2.1 Q1–Q4 默认被接受或已改写  
- [ ] 明确口令：**按 W4 计划实现** / **开始做**  
- [ ] 实现中逐步打勾；偏离写入 progress  
