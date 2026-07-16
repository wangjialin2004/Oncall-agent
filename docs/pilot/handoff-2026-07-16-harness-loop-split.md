# OnCall Agent 交接文档 — Harness loop 拆分 · 测试硬化

> **交接日期**：2026-07-16  
> **工作树**：`super_biz_agent_py-master-commit`  
> **本棒主题**：工程债 — 拆解 `loop.py`（≤1000 行门禁）+ `test_harness_service` 适配新默认开关  
> **产品决策**：**不变** — 仍以 [L3 Conditional 交接](./handoff-2026-07-15-m3-l3-conditional.md) 为准  
> **H3 真人 OnCall**：**仍豁免**；**非无人值守主路径**  
> **目标读者**：继续改 harness / 跑单测 / 接手本机试点的同学  
> **前序主交接**（产品能力）：[M3 L3 Conditional](./handoff-2026-07-15-m3-l3-conditional.md)  
> **本棒计划 / 进度**：  
> - [计划](../../plan/2026-07-15-harness-loop-split-simplify.md)  
> - [进度](../../plan/2026-07-15-harness-loop-split-progress.md)

---

## 0. 30 秒结论

| 项 | 状态 |
|---|---|
| 产品 L3 Conditional / H3 豁免 | **不变**（见前序交接） |
| `loop.py` 上帝文件 | ✅ **3672 → 207 行门面** |
| harness 单文件 ≤1000 | ✅ **全绿**（`make check-harness-size`） |
| 公共 API | ✅ `from app.agent.harness import HarnessService, harness_service` |
| 开关默认值 / SSE 契约 | ✅ ** intentionally 未改** |
| Agent 核心单测（M1–M3 等） | ✅ **约 113–145 passed**（拆分后） |
| `tests/` 排除巨型 harness 套件 | ✅ **291 passed** |
| `tests/test_harness_service.py` | ✅ **67 passed**（2026-07-16 测试硬化后） |
| full live 评测 | ⬜ **本棒未重跑**（非目标） |
| 下一棒（产品） | 仍见 [L3 交接](./handoff-2026-07-15-m3-l3-conditional.md) / [Q-Next W1](../../plan/2026-07-15-q-next-w1-exit-hardening.md) / [backlog](../../plan/2026-07-15-next-quarter-backlog.md) |

**接手人一句话**：本棒是 **可维护性重构 + 测试适配**，不是能力出口变更。读 harness 请从门面 `loop.py` 进 mixin；改行为仍须 **先计划、有开关、不破只读红线**。

---

## 1. 必读文档（本棒）

| 优先级 | 路径 | 用途 |
|---|---|---|
| **P0** | **本文** | 工程债交接 |
| **P0** | [L3 Conditional 主交接](./handoff-2026-07-15-m3-l3-conditional.md) | 产品状态 / 评测 run_id / 红线 |
| **P0** | [拆分计划](../../plan/2026-07-15-harness-loop-split-simplify.md) | 决策、模块边界、非目标 |
| **P0** | [拆分进度](../../plan/2026-07-15-harness-loop-split-progress.md) | 行数表、验证命令 |
| **P0** | [CLAUDE.md](../../CLAUDE.md) | 先计划后编码；**harness ≤1000 红线** |
| P1 | [AGENTS.md](../../AGENTS.md) | 计划索引 |
| P1 | [预生产 Runbook](./preprod-runbook.md) | 白名单 / 回滚（产品） |

---

## 2. 本棒做了什么

### 2.1 Harness `loop.py` 拆分（行为等价）

原 [`app/agent/harness/loop.py`](../../app/agent/harness/loop.py) **~3672 行 / 64 方法** 拆为门面 + Mixin：

```text
app/agent/harness/
  loop.py            # 门面 ~207：组合 Mixin + __init__ + stream + 测试 re-export
  stream_inner.py    # ~843：主循环（route → context → seed → step）
  close_path.py      # ~894：verify / re-evidence / complete / HITL / distill
  tools_runtime.py   # ~667：execute / force-seed / aux / delegate 事件
  policy.py          # ~522：replan / early-close / tool-cap / evidence
  events_emit.py     # ~282：progress / plan / verify / clarify 事件
  checkpoint_ops.py  # ~253：resume / save / rebuild
  fallback.py        # ~249：knowledge / raw-vector 降级
  llm_turns.py       # ~175：truncate / chat·final stream / model tier
  (+ 既有 planner/verifier/subagent/… 未改职责边界)
```

设计原则（见计划）：

- **Mixin 挂回同一 `HarnessService`**，不重写状态机  
- **删重复优先于加抽象**  
- 测试兼容：`loop.stateful_context_enabled` / `get_expert` / `loop.config.*` 仍可 monkeypatch（门面 re-export + 惰性查找）

### 2.2 行数门禁

- `make check-harness-size`：`app/agent/harness/*.py` 任一文件 **>1000 失败**  
- `CLAUDE.md` §4.1 已写红线：禁止继续把新开关堆进门面 `loop.py`

### 2.3 `test_harness_service.py` 硬化（2026-07-16）

拆分后主回归绿，但巨型 [`tests/test_harness_service.py`](../../tests/test_harness_service.py) 曾 **6 failed**（后扩到相关 latency 用例）。

**根因（非产品逻辑写坏）**：

1. 新默认：`investigation_evidence_early_close` / `re_evidence` 等使 **LLM 调用次数 > FakeLLM 脚本条数** → `pop` 空 / 路径偏离  
2. 升级页脚 / corrective verify 改写 exact `answer`  
3. 固定 `session_id` + 本机 Redis checkpoint **脏 resume**

**测试侧修复**（**未改产品默认开关**）：

| 改动 | 说明 |
|---|---|
| `FakeLLM._next_response()` | 队列耗尽时复用最后一条，禁止空 `pop` / 自递归 |
| `_disable_extra_harness_loops(monkeypatch)` | 经典 stream 用例关闭 early-close / re-evidence / replan / corrective / checkpoint / distill / anti-pattern / HITL；升级页脚 stub 为空 text |
| `checkpoint_store=None` + 唯一 session_id | 隔离本机 checkpoint |
| content 断言 | `>=2` 段 content → **有 content 且拼接 == final_answer** |

验收：

```text
tests/test_harness_service.py  →  67 passed
```

---

## 3. 架构心智模型（读代码入口）

```text
POST /api/assistant
  → app.api.assistant → harness_service.stream   # loop.py 门面
       → 外层 timeout / soft-close / fallback     # loop + close_path + fallback
       → _stream_inner                             # stream_inner.py
            route / context / plan / seed / step loop
            → _execute_tools / aux / force-seed    # tools_runtime.py
            → policy 判定（replan / early-close）  # policy.py
       → _finish_after_steps                       # close_path.py
            verify / re-evidence / report / complete
            distill / anti-pattern / escalation
```

**改哪里：**

| 想改… | 优先文件 |
|---|---|
| 主循环步进 / seed 顺序 | `stream_inner.py` |
| 工具执行 / 并行委派 aux | `tools_runtime.py` |
| replan / early-close / tool-cap | `policy.py` |
| verify / re-evidence / complete payload | `close_path.py` |
| 降级 knowledge/vector | `fallback.py` |
| checkpoint resume | `checkpoint_ops.py` |
| 模型 stream / truncate | `llm_turns.py` |
| 仅装配依赖 / 外层 timeout | `loop.py`（保持瘦） |

---

## 4. 验证命令（接手自检）

```bash
# 1) 行数门禁
make check-harness-size

# 2) CI smoke（与 .github/workflows/ci-smoke.yml 对齐）
make ci-smoke

# 3) Agent 核心回归（拆分验收集）
python -m pytest \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_harness_checkpoint.py \
  tests/test_m2_latency_eval_hardening.py \
  tests/test_m2_w6_parallel_delegation.py \
  tests/test_m3_w9_residuals.py \
  tests/test_m3_w10_distill.py \
  tests/test_m3_w10_anti_pattern.py \
  tests/test_harness_verifier.py \
  tests/test_harness_observability.py \
  tests/test_harness_stateful_context.py \
  tests/test_context_integration.py \
  -q --tb=line --no-cov

# 4) 巨型 harness 套件（2026-07-16 硬化后）
python -m pytest tests/test_harness_service.py -q --tb=line --no-cov
# 期望：67 passed
```

本机健康（依赖齐全时）：

```bash
# 需 Milvus / MCP 等；仅代码 import 可用：
python -c "from app.main import app; from app.agent.harness import harness_service; print('ok', len(app.routes))"
```

> 注意：Milvus 断开时 `GET /health` 可能 **503 unhealthy**，与本棒拆分无关。

---

## 5. 明确非目标 / 未做

| 项 | 状态 |
|---|---|
| 改 `HARNESS_*` 默认开关 | ❌ 未做 |
| full live `cases.jsonl` 重跑 | ❌ 未做（仍以 L3 交接 run_id 为准） |
| 压 P50/P95 | ❌ 非本棒 |
| 接变更数据源 | ❌ 仍 option B unavailable |
| 拆分 `test_harness_service.py` 文件本身 | ❌ 仅修脆弱点 |
| 自动处置 / 无人值守 | ❌ 产品红线禁止 |

---

## 6. 风险与注意事项

1. **Monkeypatch 路径**：旧测试常用 `app.agent.harness.loop.config.*` / `loop.stateful_context_enabled`。门面保留兼容；新测试优先 `app.config.config.*` 与实际使用模块。  
2. **本机 Redis checkpoint**：固定 `session_id` 的集成测务必 `checkpoint_store=None` 或唯一 id，否则会 resume 脏数据。  
3. **FakeLLM 条数**：默认 early-close / re-evidence 打开时，脚本响应可能不够；新测请预估调用次数或使用 `_next_response` 语义。  
4. **行数红线**：新增 harness 逻辑禁止堆回 `loop.py`；超 1000 先拆再合。  
5. **并行演进**：工作树上另有过程栏 UI / 工具协议泄漏等计划条目（见 `AGENTS.md`），与 harness 拆分独立，合并时注意 `stream_inner` / `close_path` / `llm_turns` 冲突。

---

## 7. 建议下一棒（工程）

| 优先级 | 项 |
|---|---|
| P1 | 产品侧：S1/N4 · P50/P95 · H3 真联系人（仍以 L3 交接 / Q-Next 为准） |
| P2 | `stream_inner` / `close_path` 内继续去重 closing / planner-timeout 块 |
| P2 | 测试 patch 逐步迁到 `app.config.config.*` |
| P3 | 可选：将 `test_harness_service.py` 按主题拆文件（另立计划） |

---

## 8. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-15 | 拆分计划落地；机械拆分 + `_finish_after_steps` 抽离 |
| 2026-07-16 | 门禁 / 进度文档；`test_harness_service` 硬化至 **67 passed**；**本文交接** |

---

## 9. 交接检查清单

- [ ] 已读本文 + L3 Conditional 主交接  
- [ ] `make check-harness-size` 通过  
- [ ] `make ci-smoke` 通过  
- [ ] `pytest tests/test_harness_service.py` → 67 passed  
- [ ] 知晓：**不改默认开关当测试绿灯手段**；产品行为变更必须先计划  
- [ ] 知晓：full live 基线仍在 L3 交接（`213510` 等），本棒未刷新  
