# 下季度 Backlog（M3 后 · 2026-07-15）

> 来源：W12 L3 **Conditional** 出口残留 + 路线图未做 P2。  
> **不是** W12 实施范围；无单独批准不改 harness 冲这些项。

---

## P0 — 出口强化

| ID | 项 | 说明 |
|---|---|---|
| Q-P0-1 | **S1 复跑 / LLM provider 稳定** | W12 S1=`llm_provider_degraded`（502 unknown provider）；排除瞬时故障 |
| Q-P0-2 | **N4-prompt-inject 评分加固** | W12 score 6.6 失败；防披露/注入门禁 |
| Q-P0-3 | **P50 ≤75s / P95 ≤150s** | W12 P50 89 / P95 172；N2/N3 回收 |
| Q-P0-4 | H3 解豁免 | 真实 `ONCALL_ESCALATION_CONTACTS` + 产品签字取消豁免 |

## P1 — 平台与学习

| ID | 项 | 说明 |
|---|---|---|
| Q-P1-1 | Online 人工分 ≥1 轮 | 填 [online-eval-template](../docs/pilot/online-eval-template.md) |
| Q-P1-2 | 蒸馏成功率度量 | N7 从「可演示」→ 统计 ≥50% 成功 run draft 率 |
| Q-P1-3 | Compose 从草案到可复现 | MCP/Milvus profile 文档化或容器化 |
| Q-P1-4 | 真 OTLP exporter | W11 skeleton → 可选 extra 依赖 |
| Q-P1-5 | 审计「谁何时跑何 session」列表 | WP-H3 加深 |

## P2 — 能力演进

| ID | 项 | 说明 |
|---|---|---|
| Q-P2-1 | WP-A5 `RE_EVIDENCE_MAX_ROUNDS=2` | 仅 high-severity；默认仍 1 |
| Q-P2-2 | 变更源 option A | 真源接入前禁止 pretend |
| Q-P2-3 | 偏好/策略迁移 WP-D3 | 用户详略、服务默认排查序 |
| Q-P2-4 | 物理收敛 legacy ContextBuilder | 有流量数据后再删 |
| Q-P2-5 | L2 书面 Conditional 改判 | 产品项；与 L3 分开 |

## 明确不做（除非新产品决策）

- 自动处置执行器  
- 无人值守生产唯一主路径  
- 默认 `AUTO_DISTILL=true`  

---

## 建议下一迭代顺序

1. full 复跑固化数字  
2. P95 专项（独立计划）  
3. H3 真实联系人  
4. Online 人工分 + 蒸馏率看板  

---

## 当前正式计划

- **[Q-Next W1：L3 出口强化](./2026-07-15-q-next-w1-exit-hardening.md)**（2026-07-15 落盘，待批准）  
  - 覆盖：Q-P0-1 S1 · Q-P0-2 N4 · Q-P0-3 时延基线（Phase B 可选）  
  - 默认不做：H3 解豁免 / Online 人工分 / 无条件 L3 改判
