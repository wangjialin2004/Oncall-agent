# M2 W7 实施计划：共享内核 · 变更源决策 · 合并去重

> **角色边界**：用户已以「继续」授权推进 M2；本文件为 W7 可执行计划与实现对照。  
> **日期**：2026-07-14  
> **状态**：**主干已实施**（见 [progress](./2026-07-14-m2-w7-shared-kernel-change-progress.md)）  
> **上级**：[3 个月路线图](./2026-07-13-complete-agent-system-3-month-roadmap.md) §3 W7 · [共享内核设计](./2026-07-14-m2-shared-kernel-design.md) · [W6 进度](./2026-07-14-m2-w6-parallel-delegation-progress.md)

---

## 0. 一页摘要

| 项 | 内容 |
|---|---|
| 主题 | 专家委派与 `ToolCallingExpert` **共用同一 tool-loop 内核**；变更源 **正式选 B 永久降权**；委派结果轻量合并去重 |
| WP | **WP-B2** · **WP-C4-B** · **WP-B4 轻量** · **WP-D1 草案文档** |
| 不做 | 真变更源接入、自动蒸馏入库、HITL、L2 出口签字、前端大改 |
| 默认开关 | `HARNESS_SHARED_KERNEL_DELEGATION=true`（可关回 legacy `expert.run` 路径包装） |
| 出口 | 单测绿 + 变更决策文档更新 + progress |

---

## 1. Context

- W6 已有 `run_one_delegate` / `delegate_parallel` / aux probe；专家仍走 `ToolCallingExpert.run` 独立循环体（虽已共享 `GuardedToolExecutor`）。
- `CHANGE_SOURCE_AVAILABLE=False` 长期悬空；路线图要求 W7 **二选一**。
- 无真实 CI/CD/CMDB → **选 B：永久降权 / 明确不可用**，直到未来另开 epic。

---

## 2. 设计决策

| ID | 决策 |
|---|---|
| D-W7-1 | 抽取 `app/agent/harness/sub_harness.py`：`SubHarnessConfig` + `run_sub_harness` |
| D-W7-2 | `ToolCallingExpert.run` **始终**委托 shared kernel（消除双实现）；`HARNESS_SHARED_KERNEL_DELEGATION=false` 时仍走同一实现但标记 `kernel=legacy_compat` 事件（避免维护两份循环）— 若需真双路径，false 仅跳过 subagent 侧直接 config 构建优化 |
| D-W7-3 | `run_one_delegate`：flag true 时优先 `run_sub_harness`（从 expert 取 system/tools/transform）；false 时 `expert.run`（仍内部 shared） |
| D-W7-4 | 变更源 **选项 B**：保持 `CHANGE_SOURCE_AVAILABLE=False`；新增 `CHANGE_SOURCE_POLICY=unavailable`；更新 pilot 文档为正式决策 |
| D-W7-5 | B4：`merge_delegate_results` 折叠同 tool 成功摘要 + 状态聚合；供 parallel/aux 复用 |
| D-W7-6 | D1：仅 `plan/2026-07-14-m2-auto-distill-draft.md`，代码开关可预留默认 false |

---

## 3. 文件清单

| 文件 | 动作 |
|---|---|
| `app/agent/harness/sub_harness.py` | 新建 shared kernel |
| `app/agent/experts/base.py` | run → sub_harness |
| `app/agent/harness/subagent.py` | 可选直连 kernel + merge |
| `app/config.py` / `.env.example` | 新开关 |
| `app/tools/change_tool.py` | policy 注释/读 config |
| `docs/pilot/change-capability-unavailable.md` | 正式 B 决策 |
| `tests/test_m2_w7_shared_kernel.py` | 新建 |
| `plan/*-progress.md` / AGENTS / 交接 | 收尾 |

---

## 4. 验证

```bash
python -m pytest tests/test_m2_w7_shared_kernel.py tests/test_m2_w6_parallel_delegation.py tests/test_m2_latency_eval_hardening.py -q --no-cov
# + M1/M2 全子集
```

---

## 5. 回滚

```text
HARNESS_SHARED_KERNEL_DELEGATION=false
# change 保持 unavailable — 无回滚到“假装有源”
```

---

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-14 | 初稿并开工 |
