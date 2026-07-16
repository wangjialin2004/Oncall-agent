# L2 出口评审 — 2026-07-14（M2 W8）

> **结论（工程）**：**Conditional Go（L2 协作诊断）**  
> **前提产品**：L1 Go；L1.5 Conditional；**H3 真人 OnCall 仍豁免**  
> **工程阶段**：M2 W5–W8 主干已合入  
> **上级**：[路线图 §3.4](../../plan/2026-07-13-complete-agent-system-3-month-roadmap.md) · [W8 计划](../../plan/2026-07-14-m2-w8-l2-exit-full-eval.md)  
> **交接**：[handoff-2026-07-14-m3-w9.md](./handoff-2026-07-14-m3-w9.md)（当前）· [L2 full 细节](./handoff-2026-07-14-l15-conditional.md)

---

## 1. 门禁表（路线图 §3.4）

| 门禁 | Go | Conditional | No-Go | **裁定** | 证据 |
|---|---|---|---|---|---|
| 并行委派 | 开关可开 + 单测 + ≥1 跨域 demo | 仅单测 / 无 live | 无实现 | **Conditional** | W6：`delegate_parallel` + aux；单测 wall < 串行 70%；full live **P1 未触发** `required_parallel_event` |
| 共享内核 | 默认 true 或 ADR 冻结 | 可关双路径 | 无 | **Go** | W7：`sub_harness` 默认；`ToolCallingExpert.run` 委托；flag `HARNESS_SHARED_KERNEL_DELEGATION` |
| 全量评测 | ≥19/23，Core 5/5 | ≥19/23 但 Core&lt;5 | 题&lt;18 或 &lt;19 | **Conditional** | full **20/23**（≥19）；Core **4/5**（S5 降级） |
| P50 | ≤85s | ≤110s + backlog | >120s 无解释 | **Go（full）/ Conditional（minimal 波动）** | full P50 **64.0s**；W8 minimal 曾 **130.9s**（样本波动，以 full 为出口主证据） |
| 变更决策 | A 或 B 落地无悬空 | 文档半决策 | 悬空 | **Go（B）** | W7 option B：`CHANGE_SOURCE_POLICY=unavailable` + pilot 文档 |

---

## 2. Live 评测

| Suite | 文件 | pass/total | P50 | P95 | 备注 |
|---|---|---|---|---|---|
| L1.5 对照 minimal | `oncall_minimal_20260713_221643` | 9/10 | 110s | 210s | Conditional L1.5 |
| W5 后 minimal | `oncall_minimal_20260714_154334` | 6/10 | 109s | 186s | MCP 故障窗，非出口 |
| W8 minimal | `oncall_minimal_20260714_174155` | 7/10 | 130.9s | 210.1s | complete=1.0；re=0.4；rp=0.4；Core **3/5**（样本回退） |
| **W8 full 23** | `oncall_full_20260714_182406` | **20/23** | **64.0s** | **143.5s** | complete=1.0；re≈0.52；rp≈0.43；Core **4/5**；must_pass 全 true |

### 2.1 W8 full 行级摘要（`20260714_182406`）— **出口主证据**

| 指标 | 值 |
|---|---|
| pass/total | **20/23** |
| Core | **4/5**（S5 失败） |
| must_pass N1/N3/M1 | all true |
| P50 / P95 | **64.03s / 143.48s** |
| complete_rate | 1.0 |
| re_evidence / replan rate | 0.52 / 0.43 |

**失败 3 题**：

| case | pass | lat_s | err |
|---|---|---|---|
| S5-slow-response | ❌ | 210.1 | `harness_degraded_fallback` |
| RE2-replan-or-gap | ❌ | 61.4 | `required_replan_not_triggered` |
| P1-parallel-cross-domain | ❌ | 75.6 | `required_parallel_event_not_triggered` |

**通过亮点**：S1/S2/S3/S4、N6、N2/N3/N4、RE1、K*/R*/C1 等共 20 题过。

### 2.2 W8 minimal 行级（`20260714_174155`）— 对照 / 波动样本

| case | pass | lat_s | re | rp | err |
|---|---|---|---|---|---|
| S1-cpu-high | ❌ | 210 | 0 | 0 | harness_degraded_fallback |
| S2-mem-high | ❌ | 210 | 0 | 0 | client_timeout |
| S3-disk-high | ✅ | 91 | 0 | 0 | |
| S4-service-down | ✅ | 131 | 0 | 0 | |
| S5-slow-response | ✅ | 177 | 1 | 1 | |
| N1 / N3 / M1 | ✅ | 49–73 | — | — | must_pass 全过 |
| N6-change-missing | ❌ | 180 | 1 | 1 | client_timeout |
| RE1-re-evidence-gap | ✅ | 82 | 1 | 1 | |

> 服务 `/health` 可达；MCP cls/monitor reachable。  
> **出口以 full 20/23、P50 64s 为主**；minimal 7/10 仅作波动对照。**不得升级无条件 L2 Go**（Core 4/5、P1 并行事件未触发、RE2 replan 未触发、H3 豁免）。

---

## 3. M2 能力清单（相对 L1.5）

| 能力 | 周 | 默认 | 状态 |
|---|---|---|---|
| 诊断步数帽 / investigation early close | W5 | on | ✅ |
| 委派 1 轮 + evidence-only | W5 | on | ✅ |
| 评测硬化（fallback 拒 / trigger 门禁） | W5 | — | ✅ |
| `delegate_parallel` | W6 | on | ✅ |
| aux 真执行 parallel/serial/off | W6 | parallel | ✅ |
| cases 23 + require_parallel_event | W6 | — | ✅ |
| 共享 sub_harness 内核 | W7 | on | ✅ |
| 变更源 option B | W7 | unavailable | ✅ |
| merge_delegate_results + 白板 | W7/W8 | — | ✅ |
| `suite=full` 有序 23 题 | W8 | — | ✅ |
| 自动蒸馏 | W7 draft | false | 📄 仅草案 |
| HITL / 自动处置 | — | — | ❌ 非目标 |

---

## 4. 结论

### Conditional Go — L2 协作诊断（工程）

**满足**：

- 多专家并行委派与 aux 自动 probe **可开可关可测**  
- 专家与委派 **共享 tool-loop 内核**（非双份业务循环）  
- 变更源 **正式 option B**，无悬空  
- 评测题库 **23/23**；CI/单测主回归绿（W8 合入日约 80 项量级）  
- **full live 20/23**、P50 **64s**、P95 **143s**、must_pass 全 true、complete=1.0  
- 只读红线与降级评分硬化保留  

**不满足无条件 Go**：

- **Core 4/5**（S5 `harness_degraded_fallback`）  
- **P1** 未触发 `required_parallel_event`；**RE2** 未触发 replan  
- minimal 样本可回退到 7/10、P50 130.9s → 稳定性仍有波动  
- H3 仍豁免 → 产品不可无人值守唯一主路径  

**产品话术**：只读 **L2 协作诊断副驾（Conditional）**；协作能力与 full 基线已齐（20/23、P50 64s），**Core/并行事件/replan 专项与稳定性未出口**；变更证据仍声明缺口。

---

## 5. 残留 → M3 输入

| ID | 事项 |
|---|---|
| M3-L | 时延稳态：压 S5 长尾；目标 P50≤75、P95≤150；降 minimal 波动 |
| M3-F | 周基线 full 23 复跑 + online 抽样；修 P1 parallel 事件 / RE2 replan 触发 |
| M3-C | Core 5/5（优先 S5 超时/降级） |
| M3-D | 自动蒸馏 / 失败反模式 |
| M3-E | HITL 建议动作 + 升级路径（解 H3） |
| M3-G | 质量/成本 metrics + 可选 OTEL |
| M3-H | compose 一键与密钥清单 |

---

## 6. 签字栏

| 角色 | 姓名 | 日期 | 签字 |
|---|---|---|---|
| 工程 | | 2026-07-14 | Conditional Go |
| 产品 | | | H3 仍豁免 |
| 接手 | | | |

---

## 7. 回滚速查

```text
HARNESS_PARALLEL_DELEGATION_ENABLED=false
ROUTER_AUX_EXECUTION_MODE=off
HARNESS_SHARED_KERNEL_DELEGATION=true   # 循环体仍 shared；flag 主要观测
CHANGE_SOURCE_POLICY=unavailable
HARNESS_TRACE_EXPORT_ENABLED=false
```
