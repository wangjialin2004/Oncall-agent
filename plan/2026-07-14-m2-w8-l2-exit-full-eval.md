# M2 W8 实施计划：合并去重收口 · 全量评测 · L2 出口

> **日期**：2026-07-14  
> **状态**：**主干已实施**（见 [progress](./2026-07-14-m2-w8-l2-exit-progress.md)）  
> **上级**：[路线图 §3.3–3.4](./2026-07-13-complete-agent-system-3-month-roadmap.md) · [W7 进度](./2026-07-14-m2-w7-shared-kernel-change-progress.md)

---

## 0. 摘要

| 项 | 内容 |
|---|---|
| 主题 | B4 合并进 aux/白板；23 题全量可跑；**L2 出口书面评审** |
| 不做 | 真变更源、自动蒸馏入库、HITL 执行、M3 平台化 |
| 出口 | 单测绿 + live 报告（能跑则写实数）+ L2 Go/Conditional/No-Go 表 |

## 1. 工作包

| WP | 动作 |
|---|---|
| B4b | aux 路径用 `merge_delegate_results`；折叠工具写入 observed_facts（framework） |
| F3b | eval `suite=full` 明确 23 题顺序；README 周基线命令 |
| EXIT | `docs/pilot/l2-exit-review-2026-07-14.md` |
| L2a | 健康则跑 minimal + full（或 overnight 标记） |

## 2. 验收

```bash
python -m pytest tests/test_m2_w7_shared_kernel.py tests/test_m2_w6_parallel_delegation.py tests/test_m2_w8_merge_eval.py -q --no-cov
python scripts/evaluate_oncall_local.py --suite full --timeout-extra 90
```

## 3. L2 门禁（路线图）

| 门禁 | Go |
|---|---|
| 并行委派 | 开关+单测+跨域 |
| 共享内核 | 默认 true |
| 全量评测 | ≥19/23，Core 5/5 |
| P50 | ≤85s |
| 变更 | B 已落地 |

未达标 → Conditional + M3 backlog。

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-14 | 初稿开工 |
