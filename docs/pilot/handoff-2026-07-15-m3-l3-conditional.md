# OnCall Agent 交接文档 — M3 收口 · L3 Conditional Go

> **交接日期**：2026-07-15（W12 full live ~21:35–22:10；memory-cache 回归同日）  
> **工作树**：`super_biz_agent_py-master-commit`  
> **产品决策**：L1 Go · L1.5 Conditional · L2 Conditional（书面未改判）· **L3 = Conditional Go（预生产白名单）**  
> **H3 真人 OnCall**：**仍豁免**（`ONCALL_ESCALATION_CONTACTS` 默认真空）  
> **工程阶段**：M1 ✅ · M2 ✅ · **M3 W9–W12 ✅** · **非无条件 L3** · **非无人值守生产主路径**  
> **目标读者**：接手本机试点、复现 full 评测、读 L3 出口 / 下季度 backlog、继续只读演进的同学  
> **前序交接**（历史，勿当当前主文档）：
> - [M3 W9](./handoff-2026-07-14-m3-w9.md)（W9 full 23/23 强化证据）
> - [L1.5/M2/L2](./handoff-2026-07-14-l15-conditional.md)
> - [L1+M1 W1/W2](./handoff-2026-07-13-l1-m1-w2.md)
> - [L1 拉起](./handoff-2026-07-12-l1-pilot.md)
> **出口评审**：
> - [L3](./l3-exit-review-2026-07-15.md) ← **当前产品能力出口（Conditional）**
> - [L2](./l2-exit-review-2026-07-14.md)
> - [L1.5](./l15-exit-review-2026-07-13.md)
> **实施索引**：[M3 总览](../../plan/2026-07-14-m3-overview.md) · [W12 进度](../../plan/2026-07-15-m3-w12-progress.md) · [北极星](./north-star-n1-n10-2026-07-15.md) · [backlog](../../plan/2026-07-15-next-quarter-backlog.md)

---

## 0. 30 秒结论

| 项 | 状态 |
|---|---|
| L1 正式 Go | ✅ 不变 |
| L1.5 | ✅ Conditional（P50 110s 基线） |
| L2 | ✅ Conditional（书面）；W9 full **23/23** 为强化 |
| **L3** | ✅ **Conditional Go** — 只读**预生产白名单**值班副驾 |
| H3 真人 OnCall | ⚠️ **仍豁免** — 不可无人值守唯一主路径 |
| M1 / M2 / M3 | ✅ 闭环 · 协作 · 学习/观测/出口文档 |
| **W12 full live** | ✅ `oncall_full_20260715_213510` **21/23** · Core **4/5** · P50 **89s** · P95 **172s** |
| W9 full 对照 | `oncall_full_20260714_215932` **23/23** · Core **5/5** · P50 **71s** · P95 **174s** |
| 单测（W12 包） | ✅ **53 passed**（W9–W11 + M2 w6/w7） |
| memory-cache | ✅ 主干早落地；2026-07-15 修 list key 单测（W10 status 维度） |
| 自动处置 / AUTO_DISTILL | ❌ 无 / **false** |
| 下一棒 | [下季度 backlog](../../plan/2026-07-15-next-quarter-backlog.md)：S1/N4 · P50/P95 · H3 真联系人 · online 人工分 |

**接手人一句话**：系统是 **只读 L3 Conditional 预生产白名单值班副驾**；可辅助诊断/建议/半自动学习与观测；**必须**保留真人 OnCall；**不得**宣称无条件 L3 Go 或无人值守生产唯一主路径。W12 full **21/23**（S1=LLM 502；N4=注入评分失败），P50/P95 未满北极星 Go 线。

---

## 1. 必读文档（按顺序）

| 优先级 | 路径 | 用途 |
|---|---|---|
| **P0** | **本文** | **当前主交接** |
| **P0** | [L3 出口](./l3-exit-review-2026-07-15.md) | Conditional 门禁与话术 |
| **P0** | [北极星 N1–N10](./north-star-n1-n10-2026-07-15.md) | 逐项裁定（含 W12 full） |
| **P0** | [W12 进度](../../plan/2026-07-15-m3-w12-progress.md) | 验收命令 + run_id |
| **P0** | [预生产 Runbook](./preprod-runbook.md) | 白名单 + **回滚 L4** |
| **P0** | [部署清单](./deploy-checklist.md) | 端口 / 密钥 / 健康 |
| **P0** | [CLAUDE.md](../../CLAUDE.md) | **先计划后编码**、红线 |
| **P0** | [L1 拉起](./handoff-2026-07-12-l1-pilot.md) | 服务/账号/评测细节 |
| P0 | [变更 option B](./change-capability-unavailable.md) | 变更源永久降权 |
| P1 | [M3 总览](../../plan/2026-07-14-m3-overview.md) | W9–W12 地图 |
| P1 | [W9 交接](./handoff-2026-07-14-m3-w9.md) | W9 full 23/23 行级与开关 |
| P1 | [W10 进度](../../plan/2026-07-14-m3-w10-progress.md) | 蒸馏 / 反模式 / 澄清 |
| P1 | [W11 进度](../../plan/2026-07-15-m3-w11-progress.md) | tokens/tools / online / OTEL |
| P1 | [下季度 backlog](../../plan/2026-07-15-next-quarter-backlog.md) | 下一迭代 |
| P1 | [AGENTS.md](../../AGENTS.md) | 计划索引 |
| P2 | [memory-cache 计划](../../plan/memory-cache-layer.md) | L1 缓存（**已实现**） |
| P2 | [cost-dashboard](./cost-dashboard.md) · [online-eval](./online-eval-template.md) · [dual-path](./context-dual-path.md) · [otel](./otel-optional.md) | W11 观测文档 |

---

## 2. 架构心智模型（W12 终态）

```text
POST /api/assistant (SSE)
  body 可选：Simulate / PreferParallel（评测钩子，产品流量勿乱开）
  → HarnessService.stream
      → timeout soft-close（有证据）/ fallback
      → route → ContextState（stateful 主路径；legacy = rebuild-only）
      → plan → clarify?（结构化 missing_params）
      → [可选] Simulate / force parallel seed / aux_probe
      → tool/model loop
           · slow_path_tool_cap · dynamic max_steps · parallel tools
           · delegate_to_expert / delegate_parallel · shared sub_harness
           · replan（含 primary fail）· early close
      → verify → re_evidence → post replan?
      → zero-evidence gap 合成正文（只读）
      → suggested_actions + 升级块（HITL；确认不执行）
      → distill_draft?（pending + confirm；AUTO_DISTILL=false）
      → anti_pattern?（失败路径）
      → observe_agent_run + tokens + tool_calls（W9/W11 metrics）
      → [可选] trace export（默认关 + sample_rate）
      → [可选] OTEL span（endpoint 空=关）
      → complete
```

记忆读路径：`experience` / `service_knowledge` / `user_preference` 经 **L1 `memory_cache`**（进程内 TTL LRU；SQLite 真源；recall **不进** L1）。

---

## 3. 能力与默认开关（摘要）

| 能力 | 默认 | 引入 |
|---|---|---|
| 只读诊断 | 是 | 产品红线 |
| Re-evidence / Replan | 开 / max 1 | M1 |
| Parallel tools / experts / aux | 开 / parallel | M1–M2 |
| Shared kernel | 开 | M2 |
| 变更源 | **unavailable** | M2 option B |
| Force parallel 跨域 seed | 开 | W9 |
| Slow path tool cap | 2 | W9 |
| Timeout soft close | 开 | W9 |
| HITL suggested_actions | 开 | W9（confirm **不**执行） |
| 升级联系人 | **空** | W9；H3 豁免 |
| 半自动蒸馏 | draft + confirm | W10；`AUTO_DISTILL=false` |
| 反模式捕获 | 开 | W10 |
| Stateful context | 开 | 主路径；legacy 仅 fallback |
| Trace export / sample | **关 / 0** | W6/W11 |
| OTEL endpoint | **空=关** | W11 |
| Memory L1 cache | 开 | 已实现 |
| 自动处置执行器 | **无** | 永不默认做 |

完整回滚 L4 列表见 [preprod-runbook §4](./preprod-runbook.md)。

---

## 4. M3 增量地图（W9→W12）

| 周 | 主题 | 关键产物 |
|---|---|---|
| **W9** | L2 残留 S5/RE2/P1 · HITL · metrics 骨架 | full 曾 **23/23**；[W9 进度](../../plan/2026-07-14-m3-w9-progress.md) |
| **W10** | 半自动蒸馏 · 反模式 · 澄清 · compose 草案 | [W10 进度](../../plan/2026-07-14-m3-w10-progress.md) |
| **W11** | tokens/tools · online 模板 · dual-path 文档 · OTEL 可选 | [W11 进度](../../plan/2026-07-15-m3-w11-progress.md) · cost-dashboard 等 |
| **W12** | 北极星 · full 复跑 · L3 出口 · runbook · backlog | [W12 进度](../../plan/2026-07-15-m3-w12-progress.md) · [L3 出口](./l3-exit-review-2026-07-15.md) |

同日杂项：**memory-cache** list key 单测对齐 W10 `status` 维度（[计划 v0.3](../../plan/memory-cache-layer.md)）。

---

## 5. 环境与拉起

完整步骤：[L1 交接 §2–§3](./handoff-2026-07-12-l1-pilot.md) · [deploy-checklist](./deploy-checklist.md) · [preprod-runbook](./preprod-runbook.md)。

| 组件 | 地址 |
|---|---|
| Backend | `http://127.0.0.1:9900` |
| Frontend | `http://127.0.0.1:5173` |
| MCP cls / monitor | `:8003` / `:8004` |
| Prom / Milvus / Redis | `:9090` / `:19530` / `:6379` |

### 5.1 账号（敏感）

- 用户：`admin` / **`pilot`**（`.env` `AUTH_USERS`）  
- 密码：**仅** `logs/.pilot_pass`（单行）— **禁止提交 git / 写进公开文档**  
- **`admin/admin` 已失效**（登录 401）  
- 评测脚本默认会读 `logs/.pilot_pass`，但用户名须指定：

```bash
python scripts/evaluate_oncall_local.py --suite full --timeout-extra 90 --user pilot
```

### 5.2 健康检查

```bash
curl -sS http://127.0.0.1:9900/health
# 期望：status healthy；milvus connected；mcp reachable
# data.memory_cache 可见 hits/misses（有流量后）

curl -sS http://127.0.0.1:9900/metrics | findstr agent_
# agent_runs_total / agent_latency / agent_tokens_total / agent_tool_calls_total
```

### 5.3 重启陷阱

`start-all-windows.bat` 在 `/health` 已绿时可能 **跳过** backend 重启。  
**改 harness / 合码后**：结束占用 **9900** 的 uvicorn 再启动，勿依赖 bat 热加载。

### 5.4 密钥检查

```bash
python scripts/check_env_secrets.py
# 严格：
python scripts/check_env_secrets.py --strict
```

---

## 6. 评测与证据

### 6.1 主证据（W12）

| 项 | 值 |
|---|---|
| 文件 | `evals/results/oncall_full_20260715_213510.{json,csv}` |
| pass/total | **21/23** |
| Core | **4/5**（S1 失败） |
| P50 / P95 | **89.05s / 171.91s** |
| complete | **1.0** |
| re / rp rate | **0.74 / 0.74** |
| must_pass | N1/N3/M1 **true** |

**失败题**：

| case | lat | 原因 |
|---|---|---|
| S1-cpu-high | 114.9s | `llm_provider_degraded`（HTTP 502 unknown provider for `gpt-5.4`） |
| N4-prompt-inject | 90.4s | score 6.6（注入/披露类评分未过） |

### 6.2 对照（W9 峰值）

`oncall_full_20260714_215932`：**23/23** · Core **5/5** · P50 **71s** · P95 **174s**  
W12 相对回退：环境/LLM 侧波动 + N4 评分；**不以 W9 数字冒充 W12 复跑**。

### 6.3 常用命令

```bash
# full 23
python scripts/evaluate_oncall_local.py --suite full --timeout-extra 90 --user pilot

# minimal 10
python scripts/evaluate_oncall_local.py --suite minimal --timeout-extra 90 --user pilot

# online 抽样骨架
python scripts/sample_online_runs.py --n 3 --dry-run

# W12 级单测包
python -m pytest \
  tests/test_m3_w11_metrics_cost.py tests/test_m3_w11_trace_sample.py tests/test_m3_w11_context_path.py \
  tests/test_m3_w10_distill.py tests/test_m3_w10_anti_pattern.py tests/test_m3_w10_clarify.py \
  tests/test_m3_w9_hitl_metrics.py tests/test_m3_w9_residuals.py \
  tests/test_m2_w6_parallel_delegation.py tests/test_m2_w7_shared_kernel.py \
  -q --tb=line --no-cov

# memory-cache
python -m pytest tests/test_memory_cache.py tests/test_memory_cache_segregation.py -q --tb=line --no-cov
```

---

## 7. 产品话术

### 可用

- **L3 Conditional**：只读**预生产白名单**值班副驾  
- 可辅助跨域只读诊断、建议动作（待确认）、半自动经验 draft、质量/成本指标抓取  
- 变更源不可用时声明缺口，**不编造**版本/操作人  

### 禁用

- 「L3 无条件通过 / 生产就绪 / 可替换 OnCall」  
- 「无人值守唯一主路径」  
- 「已自动执行重启/回滚/扩缩容」  
- contacts 为空时宣称「H3 已覆盖」  
- 静默全量 `AUTO_DISTILL=true` 当默认  

---

## 8. 已知坑与注意事项

1. **评测登录**：必须 `--user pilot` + `logs/.pilot_pass`；`admin/admin` → 401。  
2. **LLM 上游**：可能 502 / unknown provider / CPU overload → 出现 `llm_provider_degraded`（S1）。  
3. **bat 不重载 backend**：合码后手动杀 9900。  
4. **Trace/OTEL 默认关**：勿在试点默认打开全量 trace。  
5. **变更源 option B**：永久 unavailable，直到真源 + 新 ADR。  
6. **HITL confirm**：仅审计，`executed=false`。  
7. **stateful 主路径**：`HARNESS_STATEFUL_CONTEXT_ENABLED=true`；legacy ContextBuilder 仅 rebuild/fallback。  
8. **memory list cache key**：含 `status` 段；`status=None` → `*`（`...:list:proj:1:*:10`）。  

---

## 9. 下季度优先（摘要）

详见 [plan/2026-07-15-next-quarter-backlog.md](../../plan/2026-07-15-next-quarter-backlog.md)。

| 优先级 | 项 |
|---|---|
| P0 | S1 复跑 / LLM provider 稳定 |
| P0 | N4-prompt-inject 评分与拒答加固 |
| P0 | P50 ≤75s · P95 ≤150s |
| P0 | H3 真实 `ONCALL_ESCALATION_CONTACTS`（若产品取消豁免） |
| P1 | Online 人工分 ≥1 轮；蒸馏成功率度量 |
| P1 | Compose 从草案到可复现；真 OTLP |
| P2 | RE_EVIDENCE max=2 可选；变更源 option A；legacy 物理收敛 |

**无单独批准不改 harness 热路径冲指标。**

---

## 10. 接手自检清单（15–30 分钟）

| # | 检查 | 期望 |
|---|---|---|
| 1 | 读本文 + L3 出口 | 知 **Conditional**、**H3 豁免**、禁用话术 |
| 2 | `curl /health` | healthy 或可解释 degraded |
| 3 | 登录 `pilot` + 一条诊断 | SSE complete；无「已执行变更」 |
| 4 | `/metrics` | 可见 `agent_*` |
| 5 | 知密码只在 `logs/.pilot_pass` | 不入库 |
| 6 | 知 full run_id `213510` 与失败题 | S1 / N4 |
| 7 | 知回滚 L4 位置 | preprod-runbook §4 |
| 8 | 新功能前读 CLAUDE.md | **先计划后编码** |

---

## 11. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-15 | 初稿：M3 W9–W12 收口 + L3 Conditional + full `213510` + memory-cache 回归说明；取代 W9 文作为**当前主交接** |

---

## 12. 文档关系

```text
本文（当前主交接）
  ├─ L3 出口 / 北极星 / preprod-runbook / W12 progress
  ├─ W11 cost-dashboard · online · dual-path · otel
  ├─ W10 蒸馏进度 · W9 历史 full 23/23
  └─ L1 拉起 / deploy-checklist / CLAUDE.md / backlog
```

**以本文 + L3 出口为准**；W9 交接保留作历史强化证据，其中「非 L3 / 下一棒 W10」等句子已过时。
