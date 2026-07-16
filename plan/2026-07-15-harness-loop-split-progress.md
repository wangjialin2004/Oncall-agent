# Harness loop 拆分进度

> 日期：2026-07-15 / 合入确认 2026-07-16  
> 计划：[2026-07-15-harness-loop-split-simplify.md](./2026-07-15-harness-loop-split-simplify.md)  
> 状态：**主干已合入**

## 结果

- 原 `app/agent/harness/loop.py` **3672 行** → 门面 **207 行**
- 职责拆到 mixin 模块，**全部 ≤1000 行**

| 文件 | 行数 | 职责 |
|---:|---:|---|
| `loop.py` | 207 | 门面：`HarnessService` 组合 + `__init__` / `stream` + 测试可 patch 的 re-export |
| `stream_inner.py` | 804 | `_stream_inner` 主路径（路由→上下文→种子→步进） |
| `close_path.py` | ~830 | soft-close / HITL / distill / `_finish_after_steps`（verify·re-evidence·complete） |
| `tools_runtime.py` | 667 | execute / force-seed / aux probes / delegate 事件 |
| `policy.py` | 522 | replan / early-close / tool-cap / evidence 判定 |
| `events_emit.py` | 279 | progress/plan/verify/clarify 事件 |
| `checkpoint_ops.py` | 253 | resume / save / rebuild context |
| `fallback.py` | 249 | knowledge / raw-vector 降级 |
| `llm_turns.py` | 144 | truncate / stream chat·final / model tier |

## 兼容策略

- 公共 API 不变：`from app.agent.harness import HarnessService, harness_service`
- 测试仍可 `monkeypatch` `app.agent.harness.loop.stateful_context_enabled` / `get_expert` / `loop.config.*`（门面 re-export + mixin 内惰性查找）
- 行为与开关默认值未改

## 验证

```text
113+ passed  （核心 harness / M1–M3 / checkpoint / observability / stateful context 等）
tests/ 排除 test_harness_service.py  →  291 passed
tests/test_harness_service.py        →  67 passed（2026-07-16 测试硬化后）
app/agent/harness/*.py 全部 ≤1000
```

命令（节选）：

```bash
python -m pytest \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_harness_checkpoint.py \
  tests/test_m2_latency_eval_hardening.py \
  tests/test_m2_w6_parallel_delegation.py \
  tests/test_m3_w10_distill.py \
  tests/test_m3_w10_anti_pattern.py \
  tests/test_harness_verifier.py \
  tests/test_harness_observability.py \
  tests/test_harness_stateful_context.py \
  tests/test_context_integration.py \
  tests/test_m3_w9_residuals.py \
  tests/test_m3_w9_hitl_metrics.py \
  -q --tb=line --no-cov

python -m pytest tests/test_harness_service.py -q --tb=line --no-cov
# → 67 passed
```

## 门禁与约定

- `Makefile` 新增 `check-harness-size`
- `CLAUDE.md` §4.1 写入 harness 单文件 ≤1000 红线

## 2026-07-16 测试硬化（同棒收尾）

`test_harness_service` 曾因新默认 early-close/re-evidence、升级页脚、checkpoint 脏 resume 失败。  
测试侧：`FakeLLM._next_response`、`_disable_extra_harness_loops`、`checkpoint_store=None`；**产品默认开关未改**。  
详见交接：[docs/pilot/handoff-2026-07-16-harness-loop-split.md](../docs/pilot/handoff-2026-07-16-harness-loop-split.md)

## 后续可选

- 在 `stream_inner` / `close_path` 内继续去重「无工具收口 / planner 超时步」块（进一步减行）
- 将测试中的 `loop.config.*` patch 逐步迁到 `app.config.config.*`（更干净，非必须）
