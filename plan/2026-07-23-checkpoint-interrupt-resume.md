# Checkpoint 中断恢复：去掉工具门禁阻断

## 问题

Checkpoint 目标是**中断后从下一步继续**，而不是重跑历史工具。

当前默认路径 `_should_replay_resume()` 会扫描已落盘步骤：只要出现过非白名单工具，就把 `resume_close_only=True`，直接跳过后续 tool loop，只做收口 LLM。

这与“中断恢复”冲突：

1. 主循环本来就不会重放 `steps[1..N]` 的 tool_calls，只是从 `next_step` 接着跑。
2. 白名单门禁实际阻断的是**继续调查**，不是“防重放副作用”。
3. Stateful 路径 `persist_messages=False`，历史工具结果在 context whiteboard；close-only 还会丢掉继续取证的机会。

## 决策与默认

| 项 | 决策 |
|---|---|
| 默认 resume | **始终从 `next_step` 继续**（真正的中断恢复） |
| 历史步骤 | 不重放；已完成 step 仍只作状态/上下文来源 |
| 非白名单工具 | **不再**触发默认 close-only |
| 显式保守收口 | 仅当请求 `checkpoint_replay=False` 时强制 close-only |
| 配置 `HARNESS_CHECKPOINT_REPLAY` | 保留字段兼容；默认 false 时语义改为“不强制 close-only”（与“默认继续”一致）。`true` 仍表示允许继续（与默认相同）。请求 override `False` 才强制保守收口 |
| 白名单 `_DEFAULT_TOOLS` | 保留给 `is_step_idempotent` / 观测；补 `delegate_parallel`；不再作为 resume 阻断条件 |

## 范围

- 改：`app/agent/harness/checkpoint_ops.py`、`stream_inner.py`、`app/services/harness_checkpoint.py` 文档/注释
- 改：相关单测期望
- 不改：SSE `type` 语义、checkpoint Redis key schema、激进重放历史 tool_calls（本系统本就没有逐步重放实现）

## 非目标

- 不实现“把历史 tool_calls 再执行一遍”
- 不放开写操作类工具的真实副作用语义（因为 resume 不重跑历史调用）

## 验证

```bash
.venv/bin/python -m pytest \
  tests/test_harness_checkpoint.py \
  tests/test_harness_service.py::test_harness_checkpoint_resume_replays_from_next_step_without_skipping \
  tests/test_harness_service.py::test_harness_checkpoint_resume_non_idempotent_closes_without_tool_replay \
  tests/test_harness_service.py::test_harness_checkpoint_resume_replay_override_replays_non_idempotent_tool \
  tests/test_harness_stateful_checkpoint_resume.py \
  tests/test_m1_w2_context_checkpoint.py \
  -q
```

退出标准：

1. 含非白名单历史工具的 checkpoint，默认 resume 进入 `model_decision` 而非 `checkpoint_conservative_close`
2. `checkpoint_replay=False` 仍可强制保守收口
3. next_step off-by-one 回归仍绿

## 回滚

恢复 `_should_replay_resume` 白名单短路逻辑；或请求层传 `checkpoint_replay=False` 临时强制 close-only。

## 验证证据

- 2026-07-23: focused pytest 全部通过（checkpoint 单测 + resume 主路径 + stateful resume + m1 w2）。
- 语义变更：默认中断恢复从 next_step 继续；仅 `checkpoint_replay=false` 强制 close-only。
