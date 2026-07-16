# Harness `loop.py` 拆分与简化实施计划

> 角色：工程可维护性重构（行为不变）  
> 日期：2026-07-15  
> 上级：L3 Conditional 后的 Q-Next 工程债  
> 状态：**已合入**（见 [进度](./2026-07-15-harness-loop-split-progress.md)）

---

## Context（现状与问题）

[`app/agent/harness/loop.py`](../app/agent/harness/loop.py) 当前约 **3672 行**，`HarnessService` 单类 **64 个方法**：

| 区段 | 行量级 | 职责 |
|---|---:|---|
| `stream` + `_stream_inner` | ~1300 | 整条主路径（路由→上下文→种子→步进→核验→收口） |
| 策略/判定 helpers | ~350 | replan / early-close / force-seed / tool-cap |
| 工具执行与委派 | ~550 | execute_tools / seed_delegate / seed_parallel / aux_probes |
| 收口 / HITL / 蒸馏 | ~300 | soft-close / suggested_actions / distill / anti-pattern |
| fallback / checkpoint / 事件 | ~700 | 降级、恢复、事件拼装 |

**问题**：

1. **上帝对象**：改 early-close 也要在 3k 行文件里定位，评审与回归成本高。
2. **重复代码**：`_stream_final_answer` 出现 **8 次**，`_stream_chat_turn` **3 次**，`model_closing` 模式 **5 次**，re-evidence / post-replan 与主步循环结构高度相似。
3. **测试强耦合**：大量用例 `from app.agent.harness.loop import HarnessService`，并 monkeypatch  
   `app.agent.harness.loop.config.*` / `stateful_context_enabled` / `build_stateful_context` / `get_expert`。拆分时必须保持公共 API 与可 patch 路径可预期。
4. **后续会继续膨胀**：M 系列开关若继续堆进同一文件，将不可维护。

用户要求：

- 拆解当前 3000+ 行 `loop.py`
- **之后单文件代码行数 ≤ 1000**
- **能用更简单实现完成同等功能时，优先简单实现**（去重、纯函数、少抽象层）

---

## 设计决策

### D1. 拆分策略：Mixin + 纯函数（非重写主循环）

**采用**「**行为等价的机械拆分 + 局部去重**」，**不**在本轮重写 Harness 语义。

| 方案 | 结论 |
|---|---|
| A. 大重写为 pipeline 框架 | ❌ 风险高，评测/开关组合易回归 |
| B. 仅按行剪切成 10+ 小文件 | ⚠️ 过碎，调用链更难读 |
| **C. 4–6 个职责模块 + Mixin 挂回 `HarnessService` + 去重 helper** | ✅ **采用** |

保留：

- 公共入口：`from app.agent.harness import harness_service` / `HarnessService`
- 方法名：`service._should_*` / `_maybe_*` 等测试已引用的私有方法仍可调用（Mixin 方法挂在同一类上）
- SSE 事件 `type` / 关键 `stage` 语义不变

### D2. 目标文件布局（均 ≤1000 行）

```text
app/agent/harness/
  loop.py                 # 公共门面：HarnessService 组合 + stream/_stream_inner 编排骨架  ≤900
  policy.py               # 纯/近纯判定：replan/early-close/seed/tool-cap/evidence  ≤400
  tools_runtime.py        # 工具执行、force seed、aux probes、log postprocess     ≤900
  close_path.py           # verify / re-evidence / post-replan / complete payload  ≤900
  fallback.py             # fallback_stream / knowledge / final fallback           ≤350
  checkpoint_ops.py       # resume 判定、restore、schedule save/complete           ≤250
  llm_turns.py            # stream_chat_turn / stream_final_answer / truncate      ≤250
  events_emit.py          # progress/plan/verify/clarify/complete 事件拼装         ≤300
  # 既有文件不动职责边界：planner/verifier/subagent/sub_harness/context/clarifier
```

`loop.py` 只保留：

1. `__init__` / 依赖装配  
2. `stream`（外层 timeout + soft-close）  
3. `_stream_inner` **骨架**（调用 phase helpers，而不是内联 1200 行）  
4. `harness_service` 单例  

### D3. 简化优先（本轮必须做的去重，不做花活）

| 简化项 | 做法 | 预期减行 |
|---|---|---:|
| S1. 统一「无工具收口」 | 抽 `_close_without_tools(...)`：progress(`model_closing`) + stream final + 返回 answer | −150~200 |
| S2. 统一「单步模型决策」 | 抽 `_planner_step_with_timeout(...)`：timeout + stream_chat_turn + step_timeout 事件 | −80~120 |
| S3. 证据/工具统计 | 已有逻辑下沉 `policy.py` 为 **module-level 函数**（类方法薄包装，兼容测试） | 可读性↑ |
| S4. complete 收尾指标 | 抽 `_emit_success_observability(...)`：trace/otel/metrics/checkpoint_completed | −40 |
| S5. **不做** | 新状态机 DSL、新中间件框架、泛化 pipeline 库 | — |

原则：**删重复 > 加抽象**。若抽象只服务 1 处调用，不抽。

### D4. 兼容性与 monkeypatch

| 策略 | 说明 |
|---|---|
| 公共导入路径 | `__init__.py` 仍从 `loop` 导出 `HarnessService, harness_service` |
| `config` patch | 各新模块统一 `from app.config import config`；**同步修正**测试中错误地 patch `app.agent.harness.loop.config.*` 的路径为 **`app.config.config.*`**（更正确，且跨模块生效） |
| 函数 patch | `stateful_context_enabled` / `build_stateful_context` / `get_expert`：在**实际使用模块** patch；`loop.py` 可 re-export 仅当 `_stream_inner` 仍从本模块名绑定（优先：使用处 `import module as m; m.fn`，测试 patch `m.fn`） |
| 私有方法 | Mixin 方法名保持 `_should_knowledge_early_close` 等，现有 `service._xxx` 单测零改或极少改 |

### D5. 行数门禁（硬约束）

1. **`app/agent/harness/**/*.py` 单文件 ≤ 1000 行**（本轮拆分后验收）  
2. 后续新增 harness 代码：优先扩已有职责文件；将超 1000 的改动必须先拆再合  
3. 在 [`CLAUDE.md`](../CLAUDE.md) §4 增加一条约定（实现阶段写入）：  
   `app/agent/harness` 单文件硬上限 1000 行；禁止把新开关逻辑继续堆进门面 `loop.py`  
4. 可选：在 `scripts/` 或 `make ci-smoke` 增加 `wc -l` 检查（实现阶段决定是否进 CI；默认 **make 目标 `check-harness-size`**）

### D6. 非目标

- 不改 SSE 对外契约 / 评测 case 语义  
- 不调整 harness 默认开关值  
- 不接变更数据源、不改专家 prompt  
- 不拆 `tests/test_harness_service.py`（可另立计划；本轮只修因 patch 路径失效的测试）  
- 不追求本轮压 P50（行为等价；时延另案）

---

## 范围与非目标

### 范围

- 拆分 `loop.py` → 上表模块  
- 去重 S1–S4  
- 修正受影响测试的 import/patch 路径  
- 文档：本计划 + `AGENTS.md` 索引 + `CLAUDE.md` 行数约定  
- 验收：相关单测 + ci-smoke

### 非目标

- full live 评测重跑（可选，非门禁）  
- 前端 / API 变更  
- 删除 legacy context 双路径  

---

## 实施步骤（可验收）

### Phase 0 — 基线锁定（不改行为）

1. 记录当前 `wc -l app/agent/harness/loop.py`  
2. 跑门禁：  
   `python -m pytest tests/test_m1_close_the_loop.py tests/test_m1_w2_context_checkpoint.py tests/test_m1_w3_replan_latency.py tests/test_m1_w4_latency_exit.py tests/test_harness_verifier.py tests/test_harness_checkpoint.py tests/test_context_integration.py tests/test_m2_latency_eval_hardening.py tests/test_m2_w6_parallel_delegation.py tests/test_m3_w9_residuals.py tests/test_m3_w10_distill.py tests/test_m3_w10_anti_pattern.py -q --tb=line --no-cov`  
3. 结果记入进度文档（通过数）

### Phase 1 — 抽出纯策略 `policy.py`（低风险）

1. 迁移：`_should_replan` / `_apply_replan`（若 `_apply_replan` 有副作用事件，可留 Mixin 包装）  
2. 迁移：early-close、force-seed 判定、tool success/fail 统计、tool cap、investigation helpers、simulate 故障注入的**判定部分**  
3. `HarnessService` 保留同名方法，一行委托到 module function（或 Mixin）  
4. 跑 Phase 0 同套测试  

**出口**：`policy.py` ≤400 行；loop 减少对应行数；测试绿。

### Phase 2 — `llm_turns.py` + `events_emit.py` + 去重 S1/S2

1. 迁移 stream_chat_turn / stream_final_answer / truncate / model name helpers  
2. 实现 `_close_without_tools`、`_planner_step_with_timeout`  
3. 替换 `_stream_inner` 内 8 处 final-answer / 3 处 chat-turn 重复块  
4. 迁移 progress/plan/verify/clarify/complete 事件构建  

**出口**：主循环内不再出现复制粘贴的 closing 块；测试绿。

### Phase 3 — `tools_runtime.py`

1. 迁移 `_execute_tools`、`_seed_force_*`、`_maybe_run_aux_probes`、delegate 事件 helpers、`_log_postprocess`  
2. 注意 `get_expert` 导入位置 → 测试 patch 路径改为 `app.agent.harness.tools_runtime.get_expert`（或 tools_runtime 内 `from app.agent.experts.registry import get_expert` 且测试改 patch 该处）  

**出口**：tools 相关单测（W6 parallel 等）绿。

### Phase 4 — `close_path.py` + `fallback.py` + `checkpoint_ops.py`

1. 将 verify / re-evidence while / post-replan / corrective / complete payload / distill / anti-pattern / escalation 收成 close 流程函数或 Mixin  
2. fallback 与 checkpoint 独立模块  
3. `_stream_inner` 变为顺序调用：  
   `setup → route → context → plan → seed → step_loop → close → observe`  

**出口**：`loop.py` ≤900 行；各新文件 ≤1000；全套 Phase 0 测试绿。

### Phase 5 — 门禁与约定固化

1. 增加 `make check-harness-size`（失败若任何 `app/agent/harness/*.py` >1000）  
2. 写入 `CLAUDE.md` §4.1 行数红线  
3. 更新 `AGENTS.md` 状态一句  
4. 写 `plan/2026-07-15-harness-loop-split-progress.md`

---

## 文件清单

| 路径 | 动作 |
|---|---|
| `app/agent/harness/loop.py` | 瘦身为门面 + 编排骨架 |
| `app/agent/harness/policy.py` | 新建 |
| `app/agent/harness/llm_turns.py` | 新建 |
| `app/agent/harness/events_emit.py` | 新建 |
| `app/agent/harness/tools_runtime.py` | 新建 |
| `app/agent/harness/close_path.py` | 新建 |
| `app/agent/harness/fallback.py` | 新建 |
| `app/agent/harness/checkpoint_ops.py` | 新建 |
| `app/agent/harness/__init__.py` | 保持导出不变（必要时注释） |
| `tests/test_*.py`（受 patch 路径影响者） | 修正 patch 目标模块 |
| `Makefile` | 可选 `check-harness-size` |
| `CLAUDE.md` | 行数硬上限约定 |
| `AGENTS.md` | Current Plan Index 挂链 |
| `plan/2026-07-15-harness-loop-split-progress.md` | 实现后进度 |

---

## 开关一览

**本轮无新业务开关。**  
不改变任何 `HARNESS_*` 默认值。

---

## 验证方式（命令 + 出口标准）

```bash
# 1) 行数门禁
python - <<'PY'
from pathlib import Path
bad=[]
for p in Path('app/agent/harness').glob('*.py'):
    n=sum(1 for _ in p.open(encoding='utf-8', errors='ignore'))
    print(f'{n:5d} {p}')
    if n>1000: bad.append((p,n))
raise SystemExit(1 if bad else 0)
PY

# 2) 核心回归（实现阶段以实际清单为准，至少包含）
python -m pytest \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_m2_latency_eval_hardening.py \
  tests/test_m2_w6_parallel_delegation.py \
  tests/test_m3_w9_residuals.py \
  tests/test_m3_w9_hitl_metrics.py \
  tests/test_m3_w10_distill.py \
  tests/test_m3_w10_anti_pattern.py \
  tests/test_m3_w10_clarify.py \
  tests/test_harness_checkpoint.py \
  tests/test_harness_verifier.py \
  tests/test_harness_observability.py \
  tests/test_harness_stateful_context.py \
  tests/test_context_integration.py \
  -q --tb=line --no-cov

# 3) 既有 smoke
make ci-smoke
```

**出口标准**：

- [ ] 每个 `app/agent/harness/*.py` ≤ **1000** 行  
- [ ] `loop.py` ≤ **900** 行（门面更瘦）  
- [ ] 上列 pytest 全绿  
- [ ] `make ci-smoke` 绿  
- [ ] 无故意行为变更（开关默认、事件 stage 名、complete payload 关键字段保持）  
- [ ] 进度文档写明各文件最终行数与测试结果  

---

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| monkeypatch 路径失效导致假绿/假红 | Phase 内每步跑测试；优先改测到 `app.config.config` |
| 循环 import（loop ↔ tools ↔ close） | 纯函数模块不导入 `HarnessService`；Mixin 由 loop 单向组合 |
| 拆分时误改分支条件 | 机械搬移优先；去重仅替换确认等价的 closing/chat 块 |
| 单次 PR 过大难审 | 按 Phase 1→5 提交/验收；可分 2–3 个提交但同一计划 |

**回滚**：git 还原 `app/agent/harness/` 与相关 tests；`__init__` 导出保持兼容则 API 无感。

---

## 建议实施顺序（给批准人）

1. **批准本计划**  
2. Phase 1–2（低风险 + 去重可见减行）  
3. Phase 3–4（主循环骨架化）  
4. Phase 5 门禁固化  

预估工作量：1–2 个专注工作日（含测试修复），**不包含** full live 评测。

---

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-15 | 初稿：待批准；基于 loop 64 方法测绘与测试耦合分析 |
