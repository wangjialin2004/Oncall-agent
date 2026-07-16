# L1.5 出口评审 — 2026-07-13

> **结论**：**Conditional Go（L1.5）**  
> **基线产品**：L1 Go（H3 真人 OnCall **仍豁免**）  
> **工程阶段**：M1 W1–W4 主干已合入；对照 eval 已跑  
> **上级**：[路线图 §2.4](../../plan/2026-07-13-complete-agent-system-3-month-roadmap.md) · [W4 计划/进度](../../plan/2026-07-13-m1-w4-progress.md)  
> **交接**：[handoff-2026-07-14-l15-conditional.md](./handoff-2026-07-14-l15-conditional.md)

---

## 1. 门禁表

| 门禁 | Go | Conditional | No-Go | **裁定** | 证据 |
|---|---|---|---|---|---|
| 严格质量（minimal） | ≥9/10；Core≥4/5；N1/N3/M1 过 | 1 题可解释失败 | 安全负例失败 | **Conditional** | `oncall_minimal_20260713_221643`：9/10；S1=`client_timeout`（score=9） |
| re-evidence | 有事件 + 单测 | 实现可关 + 单测 | 无实现 | **偏 Go / Conditional** | 单测 ✅；真跑触发率 **0.2**（S4/RE1） |
| P50 | ≤100s | ≤120s + backlog | >120s 无解释 | **Conditional** | **110.4s**（W3 基线 165s → −33%） |
| Checkpoint | timeout 落盘 + rehydrate | 仅其一 | 回退 | **Go** | W2 落盘 + W4 `context_rehydrate*` |
| CI | pytest 自动 | 本地子集 | 无 | **Go** | `make ci-smoke` / GH workflow；**49 passed** |

## 2. 对照数字

| 指标 | L1 历史 8 题 | W3 10 题 `205114` | **W4 10 题 `221643`** |
|---|---|---|---|
| 通过 | 8/8 | 9/10 | **9/10** |
| P50 | ~138s | 165s | **110s** |
| P95 | ~158s | 210s | 210s |
| complete | 1.0 | 0.9 | **1.0** |
| re_evidence 率 | — | 0 | **0.2** |
| replan 率 | — | 0 | **0.3** |

## 3. 能力清单（M1 已落地）

| 能力 | 默认 | 状态 |
|---|---|---|
| re-evidence | true | ✅ |
| force_delegation | false | ✅ |
| evidence match | true | ✅ |
| recent_turns stamp | true | ✅ |
| timeout checkpoint | true | ✅ |
| replan max=1 | true | ✅ |
| dynamic max_steps | true | ✅ |
| parallel tool calls | true | ✅ |
| knowledge early close | true | ✅ |
| route timeout profile | true | ✅ |
| rehydrate 可观测 | — | ✅ |
| cases.jsonl | 20 | ✅ ≥18 |
| ci-smoke | — | ✅ |

## 4. 结论

### Conditional Go — L1.5 可靠副驾（工程）

**满足**：

- 闭环（re-evidence / replan）可测可关可观测  
- 安全只读 + 变更缺口声明稳定（N1/N3/N6）  
- 多轮白板 + checkpoint 超时落盘 + rehydrate 事件  
- 评测 ≥18 题落盘；minimal 9/10；**P50 110s ≤120s Conditional 线**  
- CI 子集可自动跑  

**不满足 Go 的项**：

- P50 **未 ≤100s**（差约 10s 量级，且 P95 仍 210s 挂顶）  
- S1 等诊断题仍可能 client_timeout  
- H3 真人 OnCall **仍豁免** → 产品上不可无人值守  

**产品话术**：仍是 **只读 L1/L1.5 诊断副驾**；不得宣称生产唯一主路径。

## 5. 残留 → M2 输入（按优先级）

| ID | 事项 | 说明 |
|---|---|---|
| M2-L | 时延：P50≤85～100；压 P95 | 并行委派、模型分层本机、RAG 超时 |
| M2-S1 | S1/长链路 timeout 对齐 | harness vs eval 预算 |
| M2-B1 | 并行委派 fan-out | 跨域 case |
| M2-C | 变更源接入 or 永久降权 ADR | 骨架维持则文档硬化 |
| M2-F | cases →23；周基线 | |
| M2-H3 | 指定真人 OnCall 或继续豁免 | 产品决策 |

## 6. 回滚与降级

```text
HARNESS_RE_EVIDENCE_ENABLED=false
HARNESS_REPLAN_ENABLED=false
HARNESS_KNOWLEDGE_EARLY_CLOSE=false
HARNESS_ROUTE_TIMEOUT_PROFILE=false
HARNESS_DYNAMIC_MAX_STEPS=false
HARNESS_EVIDENCE_MATCH_ENABLED=false
HARNESS_STATEFUL_CONTEXT_ENABLED=false
HARNESS_MCP_ENABLED=false
```

## 7. 签字栏

| 角色 | 姓名 | 日期 | 结论 | 签字 |
|---|---|---|---|---|
| 工程 | | 2026-07-13 | **Conditional Go L1.5** | |
| 产品 | | | H3 仍豁免；范围只读副驾 | |
| 复核 | | | 可用 `221643` 为对照基线 | |

---

## 8. 一句话

> **L1.5 Conditional：闭环与门禁够用，P50 110s 进 Conditional 未进 Go；下一棒 M2 用并行与数据面换时延，H3 未指定前不做无人值守承诺。**
