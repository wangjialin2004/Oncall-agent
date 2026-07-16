# M1 W1 实施计划：Close the Loop（闭环加深）

> **角色边界**：本文件描述「怎么做」。业务代码改动需用户明确授权后执行。  
> **状态**：2026-07-13 **补录**（Day1 编码已按本计划意图落地；本文件为正式计划文档，约束后续 M1 工作）。  
> **进度**：见 [2026-07-13-m1-day1-progress.md](./2026-07-13-m1-day1-progress.md)  
> **上级路线图**：[2026-07-13-complete-agent-system-3-month-roadmap.md](./2026-07-13-complete-agent-system-3-month-roadmap.md) §2 / §12

---

## 0. 为什么要先写本计划

按项目 `CLAUDE.md` 约定：**任何非 trivial 实现任务，必须先落盘计划再改代码**。  
本计划覆盖 M1 第 1 周（W1）启动包，目标是把 L1「会答」推进到 L1.5 的第一步——**证据不够时会补查，而不是直接结案**。

---

## 1. Context（现状与问题）

### 1.1 基线

- 正式 **L1 Go**（2026-07-13）；只读 OnCall 诊断副驾
- Harness 主路径：`route → plan → tool loop → verify → complete`
- Verify 仅标注 `status/confidence/gaps`，`corrective_verify` 只改展示文案
- `HARNESS_FORCE_EXPERT_DELEGATION` 配置存在，主循环**未 seed 委派**
- 变更工具为骨架；配置注释与默认值有漂移
- 评测最小集 8 题；全量目标 23 题，当时 12 题

### 1.2 要解决的问题（W1 范围）

| ID | 问题 | 对应 WP |
|---|---|---|
| P1 | 低置信/无工具证据仍可直接 final | WP-A1 |
| P2 | force_delegation 配置漂移 | WP-A4 |
| P3 | 变更源缺失时可能编造版本/操作人 | WP-C1 |
| P4 | 配置注释与真实行为不一致 | WP-H1 |
| P5 | 评测未覆盖 re-evidence / N6 进最小集 | WP-F1 |

### 1.3 非目标（W1 不做）

- mid-loop replan（WP-A3 → W3–W4）
- required_evidence 细匹配（WP-A2 → W2–W3）
- checkpoint timeout 落盘 / ContextState rehydrate（WP-I1 → W2）
- 多轮 `recent_turns` happy path（WP-CTX1 → W2）
- 并行委派 / 共享内核（M2）
- 自动处置 / 写操作

---

## 2. 设计决策（已确认）

| 决策 | 选择 | 理由 |
|---|---|---|
| D-M1-1 force_delegation | **实现**，默认 `false` | 保留确定性编排能力；默认不改变现网行为 |
| D-M1-2 re-evidence | **默认开**，`max_rounds=1` | 闭环优先；限制 1 轮控制时延 |
| re-evidence 触发条件 | `status in {degraded,failed}` **且** `gaps` 非空 **且** 有调查工具 **且** 未超预算 **且** 非 close-only resume | 避免空转与无限循环 |
| re-evidence 后 | 再跑 verify；仍不足则 corrective 缺口前缀 final | 与现有 corrective 兼容 |
| 变更源 | **保持不可用**，结构化 gap，禁止编造 | 对齐 D2 产品边界 |
| 评测最小集 | 8 → **10**（+N6 +RE1） | 守住变更负例与闭环回归 |

---

## 3. 目标架构（W1 后主循环）

```text
/api/assistant → HarnessService.stream
  route → context → plan → (optional clarify)
  [if force_expert_delegation]
      seed delegate_dispatch + delegate_to_expert(route, message)
  tool / model loop (max_steps, budget, no_progress, step_timeout)
  if answer:
      verify
      while needs_re_evidence and rounds < max:
          emit stage=re_evidence
          inject gap user message
          one tool-capable model turn (+ tools + optional close)
          verify again
      if still gaps: corrective notice prefix
      report + content
  complete
```

### 3.1 事件契约（前端兼容）

| stage | 含义 | 既有/新增 |
|---|---|---|
| `delegate_dispatch` | 强制首委派开始 | 新增（agent_event） |
| `delegate_start` | 委派执行 | 既有 |
| `re_evidence` | 补取证轮次 | 新增（agent_event） |
| `verify` | 自检结果 | 既有（可多次） |
| `complete.payload.re_evidence_rounds_used` | 使用轮数 | 新增字段（additive） |

未知 `type` 前端会丢弃；`agent_event` 可渲染，新增 stage 不破契约。

---

## 4. 实施步骤（W1）

### Step 0 — 写计划并挂索引（本文件）

- 写入 `plan/2026-07-13-m1-w1-close-the-loop-implementation.md`
- 更新 `CLAUDE.md` / `AGENTS.md` 索引
- **验收**：计划可独立阅读，含决策、文件、开关、测试

### Step 1 — WP-H1 配置去漂移

**文件**：`app/config.py`、`.env.example`

| 项 | 改动 |
|---|---|
| `harness_enabled` 注释 | 标明 HTTP 已固定 harness；开关主要门控 checkpoint 等 |
| `harness_stateful_context_enabled` 注释 | 默认 True；False 回退 ContextBuilder |
| 新增 | `harness_re_evidence_enabled`、`harness_re_evidence_max_rounds` |
| `.env.example` | 同步注释与默认建议 |

**验收**：注释与代码默认一致；无「默认关闭」与 `True` 冲突。

### Step 2 — WP-C1 变更边界硬化

**文件**：`app/tools/change_tool.py`、`app/agent/experts/change.py`

| 项 | 改动 |
|---|---|
| 无源返回 | `source_available=false`、`gap=missing_change_datasource`、明确「不要编造」 |
| expert prompt | 强制声明缺口；禁止版本/操作人幻觉 |

**验收**：单元断言 gap 字段；N6 语义可匹配关键词。

### Step 3 — WP-A4 force seed-delegation

**文件**：`app/agent/harness/loop.py`

| 项 | 改动 |
|---|---|
| `_should_force_seed_delegate` | 开关 + delegation + route 合法 + 有 `delegate_to_expert` |
| `_seed_force_delegate` | emit `delegate_dispatch` → 构造 ToolCall → `_execute_tools` → checkpoint save |
| 时机 | plan/clarify 之后、主 for-loop 之前；**不**在 resume_close_only / 有 resume_messages 时 seed |

**验收**：

- `force=true`：时间线有 `delegate_dispatch` + `delegate_to_expert` tool_event
- `force=false`：与现网一致（既有 soft delegation 测试不破）

### Step 4 — WP-A1 re-evidence 闭环

**文件**：`app/agent/harness/loop.py`、`app/config.py`

| 项 | 改动 |
|---|---|
| verify 后循环 | 最多 `max_rounds` 次补取证 |
| 注入消息 | user 角色说明 gaps，要求只读补查或声明缺口 |
| 工具轮 | 与主循环相同 `_stream_chat_turn` + `_execute_tools`；有 tool 后再 no-tool close |
| 超时 | 复用 `step_timeout_seconds`；超时则带 gap 收尾 |
| complete payload | `re_evidence_rounds_used` |

**验收**：

- 无证据首答 + 有调查工具 → 出现 `re_evidence`，第二次 verify `evidence_count>=1`（在 fake tool 场景）
- `re_evidence_enabled=false` → 不出现 `re_evidence`，LLM 只调 1 次
- 零工具场景仍 degraded（不因 re-evidence 死循环）

### Step 5 — WP-F1 评测增量

**文件**：`evals/oncall/cases.jsonl`、`evals/oncall/README.md`、`scripts/evaluate_oncall_local.py`

| 项 | 改动 |
|---|---|
| 新增 | `RE1-re-evidence-gap` |
| MINIMAL_IDS | + `N6-change-missing` + `RE1-re-evidence-gap`（10 题） |
| README | 覆盖表更新 |

**验收**：cases 去重后 ≥13；脚本最小集含 N6/RE1。

### Step 6 — 测试

**文件**：`tests/test_m1_close_the_loop.py`（新建，避免撑大 5k 行 harness 测试）

| 用例 | 断言 |
|---|---|
| force on | seed 事件 + 首 call 消息含 tool |
| force off | 无 seed |
| re-evidence on | stage + tool + 二次 verify + rounds_used=1 |
| re-evidence off | 无 stage，1 次 LLM call |
| change tool | gap 字段与话术 |

**回归**：既有 verify / soft delegation / missing subject 测试。

---

## 5. 开关一览

| 开关 | 默认 | 说明 |
|---|---|---|
| `HARNESS_RE_EVIDENCE_ENABLED` | `true` | 是否补取证 |
| `HARNESS_RE_EVIDENCE_MAX_ROUNDS` | `1` | 补取证轮数上限 |
| `HARNESS_FORCE_EXPERT_DELEGATION` | `false` | 是否确定性首委派 |
| `HARNESS_CORRECTIVE_VERIFY_ENABLED` | `true` | 最终缺口前缀（既有） |
| `HARNESS_DELEGATION_ENABLED` | `true` | 总委派门（既有） |

**降级**：任一新能力可 env 关闭；`force=false` + `re_evidence=false` 行为接近 L1 基线（verify 仍会标 gaps）。

---

## 6. 文件清单

| 动作 | 路径 |
|---|---|
| 改 | `app/agent/harness/loop.py` |
| 改 | `app/config.py` |
| 改 | `.env.example` |
| 改 | `app/tools/change_tool.py` |
| 改 | `app/agent/experts/change.py` |
| 增 | `tests/test_m1_close_the_loop.py` |
| 改 | `evals/oncall/cases.jsonl` |
| 改 | `evals/oncall/README.md` |
| 改 | `scripts/evaluate_oncall_local.py` |
| 增 | 本计划 + Day1 进度 |

---

## 7. 验证方式

```bash
# W1 核心
python -m pytest tests/test_m1_close_the_loop.py -q --no-cov

# 相关回归
python -m pytest \
  tests/test_harness_service.py::test_harness_verify_marks_answer_without_tool_evidence_as_degraded \
  tests/test_harness_service.py::test_harness_corrective_verify_prepends_gap_notice \
  tests/test_harness_service.py::test_harness_stream_does_not_seed_delegate_before_model_decision \
  tests/test_harness_service.py::test_harness_stream_soft_delegation_lets_model_decide \
  tests/test_harness_service.py::test_harness_asks_for_missing_metric_subject_after_plan \
  -q --no-cov

# 可选：本机有 LLM + 依赖时
python scripts/evaluate_oncall_local.py   # MINIMAL 10 题
```

**出口标准（W1）**

- [x] 计划文档落盘并索引
- [x] re-evidence 可演示（单测）
- [x] force_delegation 可开关（单测）
- [x] 变更 gap 结构化（单测）
- [x] 配置注释对齐
- [x] 评测 RE1 + N6 进最小集
- [ ] 本机 10 题 eval 对照基线 P50（需运行环境，未在 Day1 强制）

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| re-evidence 增加时延 | max_rounds=1；预算不足跳过；可关开关 |
| force 与 soft 测试冲突 | 默认 false；seed 测试独立文件 |
| 双重 verify 事件 | 前端可接受多次 agent_event；payload 带 round |
| 零工具死循环 | 无调查工具不触发 re-evidence |
| resume 重复 seed | 有 resume_messages 或 close-only 不 seed |

---

## 9. 实施顺序与状态（回填）

| 顺序 | 步骤 | 状态（2026-07-13） |
|---|---|---|
| 0 | 写计划 | ✅ 补录完成 |
| 1 | WP-H1 | ✅ 已合入 |
| 2 | WP-C1 | ✅ 已合入 |
| 3 | WP-A4 | ✅ 已合入 |
| 4 | WP-A1 | ✅ 已合入 |
| 5 | WP-F1 | ✅ 已合入 |
| 6 | 单测/回归 | ✅ 通过 |
| 7 | 本机 10 题 eval | ⬜ 待环境 |

---

## 10. W2 衔接（下一计划应单独成文）

开工 W2 前**另写** `plan/YYYY-MM-DD-m1-w2-*.md`，建议范围：

1. WP-CTX1 多轮 recent_turns  
2. WP-I1 checkpoint timeout 落盘 + rehydrate  
3. WP-A2 required_evidence 细匹配  
4. WP-L1 时延快赢（planner 轻量模型）  
5. WP-F2 CI 骨架  

---

## 11. 一句话

> **W1：默认开启一轮 re-evidence，可选强制首委派，硬化变更缺口，补齐评测与配置真相；先计划后编码，可开关可回滚。**
