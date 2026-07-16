# M1 Close the Loop — Day1 进度

> 日期：2026-07-13  
> **正式实施计划（补录）**：[2026-07-13-m1-w1-close-the-loop-implementation.md](./2026-07-13-m1-w1-close-the-loop-implementation.md)  
> 对应路线图：[2026-07-13-complete-agent-system-3-month-roadmap.md](./2026-07-13-complete-agent-system-3-month-roadmap.md)  
> 工作流约定：[CLAUDE.md](../CLAUDE.md)（先计划后编码；本进度对应的实现曾先于计划落盘，计划已补录）  
> 状态：**W1 主干已开工并合入代码**（单测绿）

## 已完成

| WP | 内容 | 状态 |
|---|---|---|
| WP-H1 | `config.py` / `.env.example` 注释与默认对齐（harness 固定入口、stateful 默认开） | ✅ |
| WP-C1 | 变更工具结构化 `gap=missing_change_datasource`；change expert 话术硬化 | ✅ |
| WP-A4 | `HARNESS_FORCE_EXPERT_DELEGATION=true` 时 seed `delegate_dispatch` + 首委派 | ✅ |
| WP-A1 | Verify 后低置信/有缺口 → `stage=re_evidence` 再 tool 一轮再 verify | ✅ |
| WP-F1 | 评测 +`RE1-re-evidence-gap`；MINIMAL 纳入 N6 + RE1（10 题） | ✅ |

## 新开关

```text
HARNESS_RE_EVIDENCE_ENABLED=true          # 默认开
HARNESS_RE_EVIDENCE_MAX_ROUNDS=1
HARNESS_FORCE_EXPERT_DELEGATION=false     # 默认关；开则确定性首委派
```

## 关键文件

- `app/agent/harness/loop.py` — re-evidence 循环 + force seed
- `app/config.py` / `.env.example`
- `app/tools/change_tool.py` / `app/agent/experts/change.py`
- `tests/test_m1_close_the_loop.py`
- `evals/oncall/cases.jsonl` / `scripts/evaluate_oncall_local.py`

## 验证

```bash
python -m pytest tests/test_m1_close_the_loop.py -q --no-cov
# 5 passed
```

相关既有 harness 测试（verify / soft delegation / missing subject）同步通过。

## 未做（W2+）

- WP-A2 required_evidence 细匹配  
- WP-A3 mid-loop replan  
- WP-I1 checkpoint timeout 落盘 + ContextState rehydrate  
- WP-CTX1 多轮 recent_turns 刷新  
- WP-F2 CI 门禁  
- WP-L1 时延（planner 轻量模型对照）  
- 全量 18/23 补题  

## 建议下一步（W2）

1. WP-CTX1 + WP-I1（多轮/恢复）  
2. WP-A2 证据细匹配  
3. 本机最小 10 题 eval 对照基线 P50  
4. 视情况开启 `HARNESS_FORCE_EXPERT_DELEGATION` 做试点对照  
