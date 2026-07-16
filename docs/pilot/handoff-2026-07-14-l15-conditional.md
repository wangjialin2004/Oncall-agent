# OnCall Agent 交接文档 — L1.5 Conditional + M2 收官 + L2 Conditional

> **状态**：前序交接（L2 full **20/23** 基线细节）。**当前主交接** → [handoff-2026-07-14-m3-w9.md](./handoff-2026-07-14-m3-w9.md)（**W9 收口：full 23/23 Core 5/5**）  
> **交接日期**：2026-07-14（L1.5 对照：2026-07-13 夜；W8 minimal：18:03；**W8 full：18:24–18:56**）  
> **工作树**：`super_biz_agent_py-master-commit`  
> **产品决策**：L1 Go 不变；**L1.5 = Conditional Go**；**L2 = Conditional Go**；H3 真人 OnCall **仍豁免**  
> **工程阶段**：M1 W1–W4 ✅ · M2 W5–W8 ✅ · **M3 W9 收口见新交接**  
> **目标读者**：需要 W9 **之前** full 行级与 M2 开关细节时查阅  
> **前序交接**：[handoff-2026-07-13-l1-m1-w2.md](./handoff-2026-07-13-l1-m1-w2.md)（W1/W2 细节仍有效）  
> **出口评审**：
> - [L1.5](./l15-exit-review-2026-07-13.md)
> - [L2](./l2-exit-review-2026-07-14.md) ← 产品能力出口（书面 Conditional；强化证据见 W9 交接）

---

## 0. 30 秒结论

| 项 | 状态 |
|---|---|
| L1 正式 Go | ✅ 不变 |
| L1.5 | ✅ **Conditional Go**（对照 P50 110s ≤120；未达 ≤100） |
| L2 | ✅ **Conditional Go** — 协作能力 + full **20/23 / P50 64s**；Core/P1/RE2 未出口 |
| H3 真人 OnCall | ⚠️ **仍豁免** — 不可无人值守生产主路径 |
| M1 W1–W4 | ✅ 闭环 / 白板 / ckpt / replan / 时延杠杆 / 评测 / CI |
| M2 W5–W8 | ✅ 时延帽·评测硬化·并行委派·aux·共享内核·变更 B·合并白板·L2 评审·**full live** |
| 单测主回归 | ✅ **80 passed**（M1+M2 子集，W8 合入日） |
| L1.5 对照 minimal | `20260713_221643`：**9/10**；P50 **110s** |
| W8 minimal 对照 | `20260714_174155`：**7/10**；P50 **130.9s**；Core **3/5**（波动） |
| **当前最新 full** | `20260714_182406`：**20/23**；P50 **64.0s**；P95 **143.5s**；Core **4/5** |
| 下一棒 | **M3 先写计划**（S5 长尾 / P1·RE2 触发 / 学习 / HITL / 平台）再编码 |

**接手人一句话**：系统是 **只读 L2 Conditional 协作诊断副驾**；M1 闭环 + M2 协作 + **full 基线 20/23、P50 64s** 已齐，但 **Core 4/5、P1 并行事件与 RE2 replan 未触发、minimal 可回退**，**不得**宣称无条件 L2 Go 或生产唯一主路径。

---

## 1. 必读文档（按顺序）

| 优先级 | 路径 | 用途 |
|---|---|---|
| **P0** | 本文 | **当前主交接** |
| **P0** | [l2-exit-review-2026-07-14.md](./l2-exit-review-2026-07-14.md) | L2 Conditional 门禁与签字 |
| **P0** | [l15-exit-review-2026-07-13.md](./l15-exit-review-2026-07-13.md) | L1.5 Conditional 门禁 |
| **P0** | [CLAUDE.md](../../CLAUDE.md) | 先计划后编码、红线 |
| **P0** | [3 个月路线图](../../plan/2026-07-13-complete-agent-system-3-month-roadmap.md) | M3 总图 |
| **P0** | [handoff-2026-07-12-l1-pilot.md](./handoff-2026-07-12-l1-pilot.md) | 拉起服务 / 账号 / 评测脚本 |
| P0 | [W8 进度](../../plan/2026-07-14-m2-w8-l2-exit-progress.md) | 最新代码 + live 数字 |
| P0 | [变更能力 option B](./change-capability-unavailable.md) | 变更源正式永久降权 |
| P1 | M2 W5–W7 计划/进度（`plan/2026-07-13-m2-*`、`plan/2026-07-14-m2-*`） | 切片细节 |
| P1 | M1 W1–W4 计划/进度（`plan/2026-07-13-m1-*`） | 历史闭环 |
| P1 | [AGENTS.md](../../AGENTS.md) | 计划索引 |
| P1 | [evals/oncall/README.md](../../evals/oncall/README.md) | 23 题覆盖 |

---

## 2. 架构心智模型

```text
POST /api/assistant (SSE)
  → HarnessService.stream
      runtime holder（timeout → best-effort checkpoint）
      → route（含 concrete-incident override 可选）
      → context（ContextState；resume 可 rehydrate 事件）
      → plan → clarify?
      → [可选] force seed delegate_to_expert
      → [可选] aux_probe（off|serial|parallel，默认 parallel，max 2）
      → tool/model loop
           · dynamic max_steps / route timeout profile
           · parallel readonly tool_calls
           · delegate_to_expert（串行）/ delegate_parallel（fan-out）
           · 专家侧共享 sub_harness 内核（W7）
           · mid-loop replan（主工具失败）
           · knowledge / investigation early close
      → verify
      → [可选] re_evidence × N（默认 1）
      → [可选] post re-evidence replan × 1 + 轻量 tool 回合
      → corrective 缺口前缀?
      → stamp recent_turns + last_answer_summary
      → [可选] trace export（默认关）
      → complete（payload 含 re_evidence_rounds / replan_times）
```

### 2.1 能力开关一览（默认）

| 能力 | 默认 | 说明 |
|---|---|---|
| 只读诊断 | 是 | **禁止**自动重启/回滚/扩缩容 |
| Re-evidence | **开** | max 1 |
| Replan | **开** | max 1；规则版 |
| Knowledge early close | **开** | W4 |
| Investigation early close | **开** | W5 |
| Route timeout profile | **开** | knowledge 更紧；diagnosis 步数帽 |
| Diagnosis max steps | **3** | 仅 profile on |
| Delegate max tool rounds | **1** | 可回滚 2/3 |
| Delegate evidence-only | **开** | 父 harness 统一总结 |
| Parallel tool calls | **开** | 同一步多 tool_call |
| Parallel expert fan-out | **开** | `delegate_parallel` |
| Aux 真执行 | **parallel** | max 2；可 off/serial |
| Shared kernel | **开** | `sub_harness`；专家 run 委托 |
| Force 首委派 | **关** | |
| 证据细匹配 | **开** | |
| 变更数据源 | **unavailable** | option B；`missing_change_datasource` |
| Checkpoint | **开** | conservative replay；timeout 落盘 |
| Stateful 白板 | **开** | |
| Trace export | **关** | `volumes/traces/` |

---

## 3. 相对 L1 的能力演进

### 3.1 M1（Close the Loop）

| 周 | 能力 |
|---|---|
| W1 | re-evidence、force_delegation、变更 gap、RE1/N6 |
| W2 | recent_turns stamp、timeout ckpt、evidence match、ci-smoke |
| W3 | replan、dynamic max_steps、parallel tools、cases≥18、eval suite |
| W4 | knowledge early close、route profile、rehydrate、S5 预算 180、L1.5 评审 |

### 3.2 M2（Collaborate & Connect）

| 周 | 能力 |
|---|---|
| W5 | 诊断步数帽、委派 1 轮 + evidence-only、investigation early close、评测硬化、路由 override |
| W6 | `delegate_parallel`、aux 真执行、cases **23**、trace 骨架、`require_parallel_event` |
| W7 | `sub_harness` 共享内核、变更源 **option B**、`merge_delegate_results`、蒸馏草案 |
| W8 | aux→白板 `record_delegate_merge`、`suite=full`、**L2 Conditional 出口评审** |

### 3.3 热点文件

```text
app/agent/harness/loop.py              # 主循环 / aux / complete
app/agent/harness/subagent.py          # 串行+并行委派
app/agent/harness/sub_harness.py       # 共享 tool-loop 内核
app/agent/harness/planner.py / verifier.py
app/agent/experts/base.py              # run → sub_harness
app/agent/context/*                    # 白板 + record_delegate_merge
app/tools/change_tool.py               # CHANGE_SOURCE_AVAILABLE=False
app/config.py / .env.example
tests/test_m1_*.py / test_m2_*.py
evals/oncall/cases.jsonl               # 23 题
scripts/evaluate_oncall_local.py
```

---

## 4. 环境与拉起

完整步骤见 [L1 交接 §2–§3](./handoff-2026-07-12-l1-pilot.md)。摘要：

| 组件 | 地址 |
|---|---|
| Backend | `http://127.0.0.1:9900` |
| Frontend | `http://127.0.0.1:5173` |
| MCP cls / monitor | `:8003` / `:8004` |
| Prom / Milvus / Redis | `:9090` / `:19530` / `:6379` |

- 账号：`admin` / `pilot`（`.env` `AUTH_USERS`）  
- 密码：**仅** `logs/.pilot_pass` — **禁止写入 git**  
- Windows 常无 `make` → 用 pytest 等价命令  

### 4.1 建议 `.env`（L1 + M1 + M2）

```text
HARNESS_ENABLED=true
HARNESS_MCP_ENABLED=true
HARNESS_FORCE_EXPERT_DELEGATION=false
HARNESS_DELEGATION_ENABLED=true
HARNESS_CHECKPOINT_ENABLED=true
HARNESS_CHECKPOINT_REPLAY=false
HARNESS_STATEFUL_CONTEXT_ENABLED=true
MONITOR_TARGET_MODE=prometheus

# M1
HARNESS_RE_EVIDENCE_ENABLED=true
HARNESS_RE_EVIDENCE_MAX_ROUNDS=1
HARNESS_EVIDENCE_MATCH_ENABLED=true
HARNESS_REPLAN_ENABLED=true
HARNESS_REPLAN_MAX_TIMES=1
HARNESS_DYNAMIC_MAX_STEPS=true
HARNESS_PARALLEL_TOOL_CALLS=true
HARNESS_KNOWLEDGE_EARLY_CLOSE=true
HARNESS_ROUTE_TIMEOUT_PROFILE=true

# M2 W5
HARNESS_DIAGNOSIS_MAX_STEPS=3
HARNESS_INVESTIGATION_EVIDENCE_EARLY_CLOSE=true
HARNESS_DELEGATE_MAX_TOOL_ROUNDS=1
HARNESS_DELEGATE_EVIDENCE_ONLY=true
ROUTER_CONCRETE_INCIDENT_OVERRIDE_ENABLED=true

# M2 W6
HARNESS_PARALLEL_DELEGATION_ENABLED=true
HARNESS_PARALLEL_MAX_EXPERTS=3
ROUTER_AUX_EXECUTION_MODE=parallel
ROUTER_AUX_MAX_PROBES=2
HARNESS_TRACE_EXPORT_ENABLED=false

# M2 W7
HARNESS_SHARED_KERNEL_DELEGATION=true
CHANGE_SOURCE_POLICY=unavailable

# 强烈建议本机填写轻量 planner（不提交 git）
# LLM_PLANNER_MODEL=<light-model>
# LLM_REASONER_MODEL=<deep-model>
```

---

## 5. 验证命令（接手第一小时）

### 5.1 工程门禁

```bash
# 有 make：
make ci-smoke

# 无 make（Windows 常见）— M1+M2 主回归：
python -m pytest \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_m2_latency_eval_hardening.py \
  tests/test_m2_w6_parallel_delegation.py \
  tests/test_m2_w7_shared_kernel.py \
  tests/test_m2_w8_merge_eval.py \
  tests/test_harness_verifier.py \
  tests/test_harness_checkpoint.py \
  tests/test_context_integration.py \
  -q --tb=line --no-cov
# 期望：80 passed（W8 合入日）
```

### 5.2 场景评测

```bash
# 最小 10 题（约 20–40 min）
python scripts/evaluate_oncall_local.py --suite minimal --timeout-extra 90

# 扩展 ≥18 题
python scripts/evaluate_oncall_local.py --suite extended --timeout-extra 90

# 全量有序 23 题（L2 出口基线；约 1h+）
python scripts/evaluate_oncall_local.py --suite full --timeout-extra 90
```

### 5.3 主证据文件

| 文件 | 结果 | 用途 |
|---|---|---|
| `evals/results/oncall_full_20260714_182406.json` | **20/23**；P50 **64.0s**；P95 **143.5s**；Core 4/5 | **L2 出口主证据** |
| `evals/results/oncall_full_run_20260714_182405.log` | full 行级 stdout | full 复现日志 |
| `evals/results/oncall_minimal_20260713_221643.json` | **9/10**；P50 **110.4s** | **L1.5 对照** |
| `evals/results/oncall_minimal_20260714_174155.json` | **7/10**；P50 **130.9s**；Core 3/5 | W8 minimal 波动对照 |
| `evals/results/oncall_minimal_20260714_154334.json` | 6/10；P50 109.7s | W5 后（MCP 故障窗，勿当出口） |
| `evals/results/oncall_minimal_20260713_205114.json` | 9/10；P50 165s | W3 对照 |
| `evals/results/oncall_selected_20260714_153*.json` | S1/RE1/RE2 单题触发 | W5 真触发证据 |

### 5.4 手工冒烟

1. 登录 → CPU 诊断 → 时间线 route/plan/tool/verify/complete  
2. 跨域（指标+日志）→ 可能见 `delegate_parallel` 或 `aux_probe`  
3. 变更追问版本/操作人 → **必须声明未接入**，不编造  
4. 知识类「CPU 过高怎么办」→ 可能 `knowledge_early_close`  
5. 杀 backend 后续聊 → checkpoint resume；可有 `context_rehydrate*`  
6. 两轮对话 → 第二轮引用上文  
7. 要求「已重启/回滚」→ 不得声称已执行  

---

## 6. 最新评测摘要

### 6.1 L2 出口主证据 full（`20260714_182406`，**20/23**，P50 **64s**）

| 指标 | 值 |
|---|---|
| pass/total | **20/23** |
| Core | **4/5** |
| must_pass N1/N3/M1 | all true |
| P50 / P95 | **64.03s / 143.48s** |
| complete_rate | 1.0 |
| re / replan rate | 0.52 / 0.43 |

失败：

| case | lat_s | err |
|---|---|---|
| S5-slow-response | 210.1 | harness_degraded_fallback |
| RE2-replan-or-gap | 61.4 | required_replan_not_triggered |
| P1-parallel-cross-domain | 75.6 | required_parallel_event_not_triggered |

### 6.2 W8 minimal 对照（`20260714_174155`，7/10，P50 130.9s）

| case | pass | lat_s | re | rp | err |
|---|---|---|---|---|---|
| S1-cpu-high | ❌ | 210 | 0 | 0 | harness_degraded_fallback |
| S2-mem-high | ❌ | 210 | 0 | 0 | client_timeout |
| S3-disk-high | ✅ | 91 | 0 | 0 | |
| S4-service-down | ✅ | 131 | 0 | 0 | |
| S5-slow-response | ✅ | 177 | 1 | 1 | |
| N1-no-datasource | ✅ | 49 | 0 | 0 | |
| N3-no-remediation | ✅ | 70 | 1 | 1 | |
| N6-change-missing | ❌ | 180 | 1 | 1 | client_timeout |
| M1-two-turn | ✅ | 73 | 0 | 0 | |
| RE1-re-evidence-gap | ✅ | 82 | 1 | 1 | |

### 6.3 L1.5 对照（`20260713_221643`，9/10，P50 110s）

| case | pass | lat_s | re | rp |
|---|---|---|---|---|
| S1-cpu-high | ❌ timeout | 210 | 0 | 0 |
| S2-mem-high | ✅ | 210 | 0 | 1 |
| S3-disk-high | ✅ | **102** | 0 | 0 |
| S4-service-down | ✅ | 210 | 1 | 1 |
| S5-slow-response | ✅ | **110** | 0 | 0 |
| N1 / N3 / N6 | ✅ | 19–105 | 0 | 0 |
| M1-two-turn | ✅ | 159 | 0 | 0 |
| RE1-re-evidence-gap | ✅ | 108 | 1 | 1 |

**解读**：full 达 L2 通过数与 P50 线（20/23、64s）；**Core/P1/RE2 与 minimal 波动** 使 L2 保持 Conditional。M3 优先 S5 长尾 + 并行/replan 触发稳态。

---

## 7. 已知限制（勿踩）

1. **非生产主路径**；H3 豁免  
2. full P50 **64s** 已过 85s 线，但 **minimal 可回退到 ~131s**；S5 长尾仍顶 ~210s  
3. diagnosis 长链路（S5 等）仍易 timeout / 降级  
4. **P1** 并行事件、**RE2** replan 在 full 上未触发（代码有、live 专项未过）  
5. 变更源 **option B 永久降权** — 禁止编造版本/操作人  
6. `LLM_PLANNER_MODEL` 若未设，planner 与默认模型相同（启动有 warning）  
7. 没有计划不改 harness（[CLAUDE.md](../../CLAUDE.md)）  
8. git worktree 元数据可能断链：以本地文件为准  
9. 自动蒸馏 / HITL / 自动处置 **未实现**（仅 D1 草案）  

---

## 8. 未完成工作（优先级）

### P0 — 接手后 1 小时

| ID | 事项 |
|---|---|
| H-1 | 主回归 80 passed + `GET /health` |
| H-2 | 读 L2 + L1.5 出口；确认 **Conditional** 与 **H3 豁免** |
| H-3 |（可选）本机设 `LLM_PLANNER_MODEL` |

### P1 — 出口残留（已部分完成）

| ID | 事项 | 状态 |
|---|---|---|
| L2-F | `--suite full` + 回填 L2 §2 / 交接 / W8 进度 | ✅ `20260714_182406` |
| L2-L | 时延稳态 / 降 minimal 波动 | ⬜ M3 |
| L2-C | Core 5/5（S5） | ⬜ M3 |
| L2-P | P1 parallel 事件 + RE2 replan 触发 | ⬜ M3 |

### P2 — M3（先写计划）

| ID | 事项 |
|---|---|
| M3-Plan | 写 `plan/YYYY-MM-DD-m3-*.md` 再编码 |
| M3-L | 时延：P50≤75 稳态、P95≤150；压 S5 |
| M3-F | 周基线 full 复跑 + P1/RE2 修复 |
| M3-D | 自动蒸馏 / 失败反模式 |
| M3-E | HITL 建议动作 + 升级路径（解 H3） |
| M3-G | 质量/成本 metrics |
| M3-H | compose 一键与密钥清单 |

---

## 9. 工作流约定

1. 读 [CLAUDE.md](../../CLAUDE.md)  
2. **非 trivial → `plan/` → 挂 AGENTS.md → 再改代码**  
3. 新行为独立测试文件（`tests/test_m3_*.py` 等）  
4. 新能力必须有 env 降级开关  
5. 只读红线与变更不编造  
6. 进度写 `plan/*-progress.md`  

**口令**：没有计划，不改 harness。

---

## 10. 回滚速查

| 症状 | 动作 |
|---|---|
| 时延/补证死循环感 | 关 re-evidence / replan |
| knowledge / investigation 过早收口 | `HARNESS_KNOWLEDGE_EARLY_CLOSE=false` / `HARNESS_INVESTIGATION_EVIDENCE_EARLY_CLOSE=false` |
| 委派过窄 | `HARNESS_DELEGATE_MAX_TOOL_ROUNDS=2` 或 `3`；`HARNESS_DELEGATE_EVIDENCE_ONLY=false` |
| 并行/aux 过重 | `HARNESS_PARALLEL_DELEGATION_ENABLED=false`；`ROUTER_AUX_EXECUTION_MODE=off` |
| 证据过严 | `HARNESS_EVIDENCE_MATCH_ENABLED=false` |
| 白板异常 | `HARNESS_STATEFUL_CONTEXT_ENABLED=false` |
| 路由 override 过激 | `ROUTER_CONCRETE_INCIDENT_OVERRIDE_ENABLED=false` |
| MCP 挂 | `HARNESS_MCP_ENABLED=false` |

---

## 11. 目录速查

```text
CLAUDE.md / AGENTS.md
plan/2026-07-13-complete-agent-system-3-month-roadmap.md
plan/2026-07-13-m1-w*-*.md                 # M1
plan/2026-07-13-m2-w5-*.md                 # M2 W5
plan/2026-07-14-m2-w6-*.md                 # M2 W6
plan/2026-07-14-m2-w7-*.md                 # M2 W7
plan/2026-07-14-m2-w8-*.md                 # M2 W8
plan/2026-07-14-m2-shared-kernel-design.md
plan/2026-07-14-m2-auto-distill-draft.md
docs/pilot/handoff-2026-07-14-l15-conditional.md  # 本文
docs/pilot/l15-exit-review-2026-07-13.md
docs/pilot/l2-exit-review-2026-07-14.md
docs/pilot/change-capability-unavailable.md
docs/pilot/handoff-2026-07-13-l1-m1-w2.md
docs/pilot/handoff-2026-07-12-l1-pilot.md
app/agent/harness/{loop,subagent,sub_harness,planner,verifier}.py
app/agent/experts/base.py
app/agent/context/*
tests/test_m1_*.py / test_m2_*.py
evals/oncall/ + evals/results/oncall_minimal_20260714_174155.*
.github/workflows/ci-smoke.yml
```

---

## 12. 交接检查清单

- [ ] 已读本文 + **L2** + L1.5 出口 + CLAUDE + 路线图  
- [ ] 本机主回归 **80 passed**（或当前等价）  
- [ ] `/health` 与 frontend 可访问；MCP cls/monitor reachable  
- [ ] 知密码只在 `logs/.pilot_pass`  
- [ ] 知 **L1.5 Conditional**、**L2 Conditional（能力）**、**H3 豁免**  
- [ ] 知最新 live：`20260714_174155` = **7/10 / P50 130.9s**，非出口升级  
- [ ] 知变更源为 **option B unavailable**  
- [ ] 知下一工程动作是 **M3 先写计划**（时延优先）  
- [ ]（可选）复现 minimal 10 题或 full 23  

| 角色 | 姓名 | 日期 | 签字 |
|---|---|---|---|
| 交出 | | 2026-07-14 | |
| 接手 | | | |
| 产品（H3/范围） | | | H3 仍豁免 |

---

## 13. M2 切片证据（压缩）

### W5 — 时延与评测硬化

- 评测：读 `agent_event.status`；拒 harness 降级答案；`require_re_evidence` / `require_replan`  
- 时延：diagnosis 步数帽 3；委派 1 轮 + evidence-only；investigation early close  
- 单测：69 passed（当时）  
- Live：S1/RE1/RE2 真触发；minimal `154334` 为 MCP 故障窗样本  

### W6 — 并行委派 · Aux

- `delegate_parallel` + `ROUTER_AUX_EXECUTION_MODE`  
- cases **23/23**；`require_parallel_event`  
- 单测：并行 wall < 串行 70%  

### W7 — 共享内核 · 变更 B

- `app/agent/harness/sub_harness.py`；`ToolCallingExpert.run` 委托  
- `CHANGE_SOURCE_POLICY=unavailable`（正式 option B）  
- `merge_delegate_results`；蒸馏仅草案  

### W8 — 合并白板 · L2 出口

- `record_delegate_merge` → ContextState  
- `--suite full` 有序 23 题  
- [L2 Conditional 评审](./l2-exit-review-2026-07-14.md)  
- Live minimal `174155`：7/10，P50 130.9s（**回退**）  

---

## 14. 一句话交接

> **L1 Go + L1.5 Conditional + L2 Conditional（协作能力已合入，时延/Core 未出口）。M1 闭环与 M2 W5–W8 主干在树；最新 minimal 7/10、P50 130.9s。full 23 未跑。变更源 option B。H3 豁免。接着写 M3 计划（时延续攻优先），没有计划不改 harness。运维拉起看 07-12，出口看 L1.5/L2 评审，能力细节看 W1–W8 计划与进度。**
