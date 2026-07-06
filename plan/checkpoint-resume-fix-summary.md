# Checkpoint Resume 修复总结

## 背景

本次修复针对 Harness Loop checkpoint 恢复路径中的两个阻塞问题：

1. `resume.next_step` 是 1-based 业务步数，但主循环 `range()` 使用 0-based index，导致恢复后多跳过一步。
2. 保守模式下遇到非白名单工具时，代码没有真正停止工具重放，而是可能从头进入正常工具循环。

## 修复内容

### 1. 修复恢复步数 off-by-one

恢复时现在使用：

```python
resume_from_step = max(0, int(resume.next_step) - 1)
```

这样当 checkpoint 表示第 2 步已完成、下一步是第 3 步时，主循环会从 index `2` 开始，随后 `state.step = step_index + 1` 得到正确的第 3 步。

### 2. 保守模式恢复时禁止非白名单工具重放

恢复时无论是否允许 replay，都会先恢复 checkpoint 中的 `messages` 和 `HarnessState`。如果 `_should_replay_resume()` 返回 `False`，会进入 `resume_close_only` 路径：

- 跳过 seed delegation。
- 跳过 `model_decision` 工具循环。
- 基于已保存的 `messages` 调用最终收口 LLM。
- 发出 `checkpoint_conservative_close` 进度事件，明确说明本次不会自动重放非白名单工具。

这保证默认保守模式不会重复执行可能有副作用或成本的工具调用。

## 回归测试

新增两条主循环级回归测试：

1. `test_harness_checkpoint_resume_replays_from_next_step_without_skipping`
   - 预置 step 2 checkpoint。
   - 验证恢复后第一轮 `model_decision` 的 step 是 3。

2. `test_harness_checkpoint_resume_non_idempotent_closes_without_tool_replay`
   - 预置包含 `query_prometheus_alerts` 的非白名单 checkpoint。
   - 验证恢复后进入 `checkpoint_conservative_close`。
   - 验证不会进入 `model_decision`。
   - 验证最终 LLM 调用不携带 `tools` 参数。

## 验证结果

已运行：

```bash
python -m pytest tests/test_harness_service.py::test_harness_checkpoint_resume_replays_from_next_step_without_skipping tests/test_harness_service.py::test_harness_checkpoint_resume_non_idempotent_closes_without_tool_replay
```

结果：`2 passed`。

```bash
python -m pytest tests/test_harness_checkpoint.py tests/test_harness_service.py
```

结果：`66 passed`，仍有既有 SQLite `ResourceWarning`。

```bash
python -m ruff check app/agent/harness/loop.py tests/test_harness_service.py app/services/harness_checkpoint.py tests/test_harness_checkpoint.py
```

结果：`All checks passed!`

## 当前结论

checkpoint 功能的核心恢复语义现在可以收尾：

- 白名单步骤可从正确的下一步继续。
- 非白名单步骤默认不会自动重放工具。
- 仍需要继续任务时，可以由用户再次确认，或通过 `HARNESS_CHECKPOINT_REPLAY=true` 开启激进恢复。
