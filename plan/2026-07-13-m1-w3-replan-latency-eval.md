# M1 W3 实施计划：mid-loop Replan · 时延快赢 · 评测扩面

> **角色边界**：本文件描述「怎么做」。**用户明确授权「按计划实现 / 开始做」后**，再改 harness 业务代码。  
> **日期**：2026-07-13  
> **状态**：**主干已实施**（见 [progress](./2026-07-13-m1-w3-progress.md)）  
> **交接入口**：[docs/pilot/handoff-2026-07-13-l1-m1-w2.md](../docs/pilot/handoff-2026-07-13-l1-m1-w2.md)  
> **上级**：
> - [3 个月路线图](./2026-07-13-complete-agent-system-3-month-roadmap.md) §2.2 WP-A3 / WP-L1 / WP-F1、§2.3 W3  
> - [M1 W1 计划](./2026-07-13-m1-w1-close-the-loop-implementation.md)（已落地）  
> - [M1 W2 计划](./2026-07-13-m1-w2-context-checkpoint-evidence-ci.md)（已落地）  
> - [CLAUDE.md](../CLAUDE.md)（先计划后编码；**W3 禁止再先写代码**）

---

## 0. 一页摘要

| 项 | 内容 |
|---|---|
| 主题 | Plan 可中途修订 + 时延可观测/可降 + 评测题库向 18/23 推进 |
| 范围 WP | **WP-A3** · **WP-L1** · **WP-F1** ·（可选 **WP-I1-d** 深层 rehydrate 观测） |
| 明确不做 | 并行委派 / aux 执行（M2）、共享内核、自动蒸馏、L1.5 出口评审（W4）、自动处置 |
| 出口 | 单测绿 + replan 可演示 + 评测 ≥18 cases 落盘 + P50 对照报告（相对 L1 ~138s） |
| 默认开关 | `HARNESS_REPLAN_ENABLED=true`（max 1）；时延开关可观测、默认不激进 |

---

## 1. Context（现状与问题）

### 1.1 接手日事实（2026-07-13）

| 点 | 现状 | 差距 |
|---|---|---|
| L1 | 正式 Go；H3 真人 OnCall **豁免** | 不可当生产无人值守主路径 |
| W1 | re-evidence / force_delegation / 变更 gap / RE1+N6 | ✅ |
| W2 | recent_turns stamp / timeout ckpt / evidence match / ci-smoke | ✅ |
| ci-smoke | 等价 pytest **37 passed**（本机无 `make` 时用 pytest 子集） | 门禁可用 |
| 服务 | backend `:9900` / frontend `:5173` / MCP / Milvus healthy | 本机可冒烟 |
| Plan | 只在 loop **开头**一次 `LightweightPlanner` | **无 mid-loop replan** |
| Re-evidence | verify 后最多 1 轮补工具，**不改 plan.todos / required_evidence** | 缺口类型可能与原计划错位 |
| 时延 | L1 P50 ~138s；`llm_planner_model` 默认空（走默认 `llm_model`） | 未做分层对照；max_steps 固定 6 |
| 评测 | `cases.jsonl` **13** 题；MINIMAL 10 题（含 N6/RE1） | 目标 **≥18/23**；fixtures 仍空 |
| 10 题 eval | 交接未强制重跑；历史主证据 8/8（`20260712_171736`，无 RE1/N6） | 需新基线 |
| Context rehydrate | resume 事件带 ref/version；深层从 ref 重建未做 | W2 进度列为 W3+ 可选 |

### 1.2 问题 → WP

| ID | 问题 | WP |
|---|---|---|
| P1 | plan 只在开头，主工具失败 / re-evidence 后仍 gap 时 steps 仍按旧 todos | **WP-A3** |
| P2 | P50 过高；planner 与 reasoner 未分层生效；简单路由步数浪费 | **WP-L1** |
| P3 | 评测题不足 18；缺 K2/N2/N4/N5/R1–R3/RE2 等 | **WP-F1** |
| P4 |（可选）resume 仅元数据 ref，Redis miss 时 rehydrate 路径不够硬 | **WP-I1-d** |

### 1.3 非目标（W3 不做）

1. 并行委派 `delegate_parallel` / aux 真执行（M2 WP-B*）  
2. 专家共享 harness 内核（M2）  
3. 变更源真实接入（继续「明确不可用」边界）  
4. L1.5 出口评审全套签字（W4）  
5. 自动处置 / HITL 执行器  
6. 物理删除 `RouterService`  
7. 把 replan 做成无限 LLM 重规划（默认 **规则 replan**，LLM 挂既有 `HARNESS_LLM_PLANNING_ENABLED`）

---

## 2. 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| D-W3-1 replan 默认形态 | **规则 replan**（确定性更新 todos/required_evidence）；LLM replan 仅当 `harness_llm_planning_enabled=true` | 可控、可测、不抬时延 |
| D-W3-2 触发条件（满足任一） | ① 主调查工具失败（非澄清类）≥1 且尚有预算 ② **re-evidence 结束后** verify 仍 degraded/failed 且有 gaps ③ 路由 `aux`/`secondary` 暗示跨域但时间线无对应 expert 成功 | 对齐路线图 WP-A3；避免每步 replan |
| D-W3-3 次数与位置 | `HARNESS_REPLAN_MAX_TIMES=1`；emit `stage=replan`；**不**重置整个 max_steps，只刷新 plan 对象 + 注入一条 user/system 提示 | 防死循环 |
| D-W3-4 与 re-evidence 关系 | 优先路径：verify → re-evidence（W1）→ 仍不足 → **replan 一次** → 再允许 **至多 1 次**轻量 tool 回合（计入 max_steps 剩余）→ 再 verify → final | 闭环加深但不叠乘爆炸 |
| D-W3-5 时延分层 | 已有 `_planner_model()` / `_reasoner_model()`；W3 **强制文档化 + 配置校验**：`LLM_PLANNER_MODEL` 空时 log 一次 warning；可选默认建议轻量模型名写 `.env.example` 不写死密钥 | 代码路径已存在，缺运营对照 |
| D-W3-6 动态 max_steps | knowledge / clarify-only 路由 `effective_max_steps=min(3, max_steps)`；metric/log/diagnosis 保持 6；开关 `HARNESS_DYNAMIC_MAX_STEPS=true`（默认 **true**） | 快赢 |
| D-W3-7 同一步多 tool_call 并行 | 若底层 `_stream_chat_turn` / `stream_tool_results` 已支持多 call：确认并行；若串行则 W3 做 **只读工具** `asyncio.gather`（失败隔离） | 注意幂等；写类工具本项目无 |
| D-W3-8 评测扩面优先序 | 先补 **不依赖复杂 fixture** 的：K2、N4、N5、R1、R2、R3、RE2；N2/M2/K3 能补则补，依赖环境则标 `level=P1` 可 skip | 先到 18 可跑 |
| D-W3-9 评测脚本 | `evaluate_oncall_local.py` 增加 `--suite extended`（≥18 ids）与延迟字段汇总 P50；MINIMAL 保持 10 题 | 不破坏 L1 最小集 |
| D-W3-10 默认开关哲学 | 新能力 **可关**；replan 默认开但 max=1；动态 steps 默认开；并行 tool 默认开 | 出问题可降级 |

### 2.1 需用户拍板（实现前确认，可默认采纳）

| # | 问题 | 建议默认 |
|---|---|---|
| Q1 | replan 默认 `true` 还是先 `false` 观测？ | **true**（max=1，与路线图一致） |
| Q2 | 动态 max_steps 是否默认 true？ | **true** |
| Q3 | `LLM_PLANNER_MODEL` 是否在本机 `.env` 写死某个轻量模型？ | **不写死进 git**；`.env.example` 给示例占位；本机可选手动设 |
| Q4 | W3 是否包含 WP-I1-d 深层 rehydrate？ | **可选 P2**；不阻塞 replan/评测 |

> 用户若未特别反对，实现时按上表建议默认推进。

---

## 3. 目标行为

### 3.1 WP-A3 mid-loop replan

```text
主循环内（verify / re-evidence 之后，或 tool 失败路径）：
  if HARNESS_REPLAN_ENABLED
     and replan_times_used < HARNESS_REPLAN_MAX_TIMES
     and trigger(主工具失败 | re-evidence后仍gap | aux跨域未委派)
     and not over_budget:
        new_plan = rule_replan(old_plan, route, gaps, tool_failures, aux_hints)
        # optional: if harness_llm_planning_enabled → refine via LLM once
        state.plan = new_plan
        emit stage=replan (todos, required_evidence, reason, trigger)
        inject message: 计划已修订，请按新 todos 取证；禁止编造
        replan_times_used += 1
        continue tool/model loop with remaining steps
```

**规则 replan 映射（初版）**：

| 触发 | todos 调整 | required_evidence 调整 |
|---|---|---|
| metric 工具失败 | 追加「换时间窗/换 instance 再查指标」；必要时加 knowledge 兜底 | 保留指标类 + 加「证据缺口说明」 |
| log 工具失败 | 收窄关键词 / 换 scope | 保留日志类 |
| re-evidence 后仍缺某 evidence 类 | 把缺失类提到 todos 首位；指向对应工具名 | 仅保留仍 missing 的类 |
| aux 跨域未委派 | 追加 `delegate_to_expert` 或跨类工具 todo | 并集主+aux 证据类 |
| change 路由 | **不得**引入伪造变更；保留 gap 话术 | 变更记录 + 时间对齐（可声明无源） |

### 3.2 WP-L1 时延快赢

| 杠杆 | 动作 | 验收 |
|---|---|---|
| 模型分层 | 决策/单步用 `_planner_model()`；final/verify 用 `_reasoner_model()`（核对现有 call site，缺则补） | 代码路径覆盖 + 单测 mock 断言 model 参数 |
| 动态 max_steps | knowledge→3；其余默认 | 单测 |
| no_progress | 保持 2；文档说明可调 `HARNESS_NO_PROGRESS_LIMIT` | 不回归 |
| 多 tool 并行 | 只读 gather | 单测 2 tools 并行完成 |
| 观测 | eval 结果 JSON 含 `latency_ms, steps, tool_count, re_evidence_rounds, replan_times, route` | 脚本输出 |

**M1 时延门禁（W3 冲刺，W4 出口用）**：严格 core/最小集 P50 **≤100s** 为 Conditional/Go 线；本周至少产出 **对照表**（优化前/后，可同机近似）。

### 3.3 WP-F1 评测扩面

目标：`cases.jsonl` 从 **13 → ≥18**。

| 优先新增 ID | suite | 要点 |
|---|---|---|
| K2-mem-howto | knowledge | 解释告警含义，不伪造当前值 |
| N4-prompt-inject | negative | 不泄露 system；不执行注入 |
| N5-long-noise | negative | 有 complete，不崩溃 |
| R1-metric-firing | routing | 路由/工具偏 metric |
| R2-log-error | routing | 偏 log |
| R3-change-recent | routing | change 可声明无源 |
| RE2-replan-or-gap | re_evidence / replan | 时间线可见 re_evidence 或 replan，最终声明缺口或补证 |

可选（凑满/超过 18）：N2、M2、K3、R5 — 依赖 fixture 或附件则标 skip 条件。

脚本：

```bash
# 最小 10 题（回归 L1+M1）
python scripts/evaluate_oncall_local.py

# 扩展 ≥18
python scripts/evaluate_oncall_local.py --suite extended
```

更新 `evals/oncall/README.md` 覆盖表。

### 3.4（可选）WP-I1-d Context rehydrate

若时间允许：

- resume 时若 store miss 且 checkpoint 带 `context_snapshot_ref`，尝试 DB snapshot 拉取  
- 失败则明确 timeline 事件 `context_rehydrate_failed`，不静默空白板  
- 单测 mock store miss

**不阻塞** A3/L1/F1。

---

## 4. 实施步骤（可验收）

### Step 0 — 基线（实现前）

1. 跑 ci-smoke 子集确认绿（见 §7）  
2. （可选）跑 10 题 eval，落 `evals/results/oncall_minimal_YYYYMMDD_*.json` 作 W3 前基线  
3. 用户确认 §2.1 默认或改写开关  

### Step 1 — WP-A3 规则 replan + 开关

1. `app/config.py` + `.env.example`：  
   - `harness_replan_enabled: bool = True`  
   - `harness_replan_max_times: int = 1`  
2. `planner.py`：新增 `rule_replan(...)` / `LightweightPlanner.replan(...)`  
3. `loop.py`：接入触发点、计数、`stage=replan` 事件、注入修订提示  
4. 独立单测：`tests/test_m1_w3_replan_latency.py`  
   - 触发条件命中 → 恰好 1 次 replan 事件  
   - max_times 耗尽不再 replan  
   - enabled=false 不触发  

### Step 2 — WP-L1 时延

1. 审计 `_planner_model` / `_reasoner_model` 所有 LLM 调用点，补齐  
2. `effective_max_steps(route)` + 开关  
3. 多 tool_call 只读并行（若尚未并行）  
4. eval 脚本汇总 P50  
5. 单测：knowledge max_steps=3；model 参数分流  

### Step 3 — WP-F1 评测

1. 向 `cases.jsonl` 追加 ≥5 题（到 ≥18）  
2. `MINIMAL_IDS` 不变；新增 `EXTENDED_IDS` 或 suite 过滤  
3. README 覆盖表更新  
4. （可选）`fixtures/` 放 1～2 个静态片段，减少 live 依赖  

### Step 4 — 回归与进度

1. ci-smoke 子集 + 新单测全绿  
2. 写 `plan/2026-07-13-m1-w3-progress.md`  
3. 更新交接/AGENTS 状态一句  
4. 若完成 10 题或 18 题真跑：结果入 `evals/results/`  

---

## 5. 文件清单

| 文件 | 变更 |
|---|---|
| `app/config.py` | replan / dynamic_max_steps /（可选）parallel_tools 开关 |
| `.env.example` | 同上 + planner model 示例注释 |
| `app/agent/harness/planner.py` | `rule_replan` / replan API |
| `app/agent/harness/loop.py` | 触发、事件、动态 steps、model 分流核对、tool 并行 |
| `app/agent/agent_loop.py` 或 tool 执行路径 | 多 tool 并行（若需） |
| `scripts/evaluate_oncall_local.py` | extended suite + P50 汇总字段 |
| `evals/oncall/cases.jsonl` | +≥5 cases |
| `evals/oncall/README.md` | 覆盖表 |
| `tests/test_m1_w3_replan_latency.py` | **新建**独立测试 |
| `plan/2026-07-13-m1-w3-progress.md` | 实现后进度 |
| `AGENTS.md` / `CLAUDE.md` | 索引与焦点 |

---

## 6. 开关一览

```text
# --- 新增（W3）---
HARNESS_REPLAN_ENABLED=true
HARNESS_REPLAN_MAX_TIMES=1
HARNESS_DYNAMIC_MAX_STEPS=true
# HARNESS_PARALLEL_TOOL_CALLS=true   # 若实现多 tool 并行

# --- 已有相关 ---
HARNESS_RE_EVIDENCE_ENABLED=true
HARNESS_RE_EVIDENCE_MAX_ROUNDS=1
HARNESS_EVIDENCE_MATCH_ENABLED=true
HARNESS_LLM_PLANNING_ENABLED=false   # true 时 replan 可走 LLM 精炼
LLM_PLANNER_MODEL=                   # 建议本机填轻量模型
LLM_REASONER_MODEL=gpt-5.4           # 或现网 reasoner
HARNESS_NO_PROGRESS_LIMIT=2
HARNESS_MAX_STEPS=6
```

降级：

```text
HARNESS_REPLAN_ENABLED=false
HARNESS_DYNAMIC_MAX_STEPS=false
HARNESS_RE_EVIDENCE_ENABLED=false    # 时延仍炸时
```

---

## 7. 验证方式

### 7.1 工程门禁（无真 LLM）

```bash
# 本机无 make 时：
python -m pytest \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_harness_verifier.py \
  tests/test_harness_checkpoint.py \
  tests/test_context_integration.py \
  -q --tb=line --no-cov

# 有 make 时：
make ci-smoke
# （实现后把 w3 测试并入 Makefile ci-smoke 目标）
```

**出口**：全部 passed；新 replan/时延断言覆盖触发与关闭路径。

### 7.2 场景评测（需 LLM + 依赖）

```bash
python scripts/evaluate_oncall_local.py              # 10 题
python scripts/evaluate_oncall_local.py --suite extended  # ≥18
```

**出口标准（W3）**：

| 项 | 标准 |
|---|---|
| cases 数量 | ≥18 落盘 |
| 最小 10 题 | 尽量全跑；Core 不回退；N1/N3/N6 安全仍过 |
| replan/re-evidence | 相关 case 时间线可观测（事件存在即可，不强制每题触发） |
| P50 | 有对照数字；冲刺 ≤100s，否则写入 backlog 原因 |

### 7.3 手工冒烟

1. 构造缺证据问题 → 可见 `re_evidence`，必要时 `replan`  
2. knowledge 类问题 → steps 更早结束（动态 max_steps）  
3. 变更追问 → 仍不编造版本/操作人  
4. `REPLAN_ENABLED=false` → 无 replan 事件  

---

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| replan + re-evidence 叠乘拖慢 | max replan=1；re-evidence max=1；总 timeout 不变 |
| 规则 replan 误改 required_evidence 过严 | evidence_match 可关；replan 可关 |
| 动态 max_steps 截断复杂 diagnosis | 仅 knowledge/clarify 降步；diagnosis 保持 6 |
| 多 tool 并行竞态写 context | 只读工具；结果合并串行 patch |
| 评测扩面依赖 live Prom | 优先 N/R/K 题；fixture 兜底 |
| Windows 无 `make` | 文档写明 pytest 等价命令；可选后续加 `scripts/ci_smoke.ps1` |

回滚：关 `HARNESS_REPLAN_ENABLED` / `HARNESS_DYNAMIC_MAX_STEPS`；代码保留 feature flag 死路径。

---

## 9. 与路线图 / W4 的边界

| W3 交付 | W4 再做 |
|---|---|
| replan 规则版 + 开关 | L1.5 出口评审、债清理 |
| 时延快赢 + 对照报告 | 若 P50 仍 >100s 的更深优化 / 并行委派预热 |
| cases ≥18 | 冲 23 全量、周基线制度化 |
| （可选）rehydrate 加固 | Checkpoint 深恢复验收签字 |

---

## 10. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-13 | 接手后起草 W3 计划 |
| 2026-07-13 | 用户授权「开始」；合入 replan / 动态 steps / 并行 tool / cases 20 / eval suite |

---

## 11. 授权检查清单（实现前）

- [ ] 用户已读本计划  
- [ ] §2.1 Q1–Q4 默认被接受或已改写  
- [ ] 明确口令：**按 W3 计划实现** / **开始做**  
- [ ] 实现中逐步打勾；偏离写入 progress 变更记录  
