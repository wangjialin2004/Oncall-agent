# M3 W11 实施计划：成本看板 · Online 抽样 · 双路径收敛 · OTEL 可选

> **角色边界**：本文件描述「怎么演进 / 怎么做」。用户批准并明确授权「按计划实现 / 开始做」后，再改 harness / metrics / 文档业务代码。  
> **日期**：2026-07-15  
> **状态**：**主干已合入**（见 [progress](./2026-07-15-m3-w11-progress.md)）  
> **上级**：
> - [M3 总览](./2026-07-14-m3-overview.md)
> - [3 个月路线图 §4](./2026-07-13-complete-agent-system-3-month-roadmap.md)（WP-G2/G3/F4/H4/H3）
> - [W10 进度](./2026-07-14-m3-w10-progress.md)（蒸馏/反模式/澄清/compose 已合入 + 健康 live）
> - [W9 交接](../docs/pilot/handoff-2026-07-14-m3-w9.md)
> - [CLAUDE.md](../CLAUDE.md)

**本轮目标一句话**：把「可观测飞轮」补齐——成本/token/工具指标可抓取、online 抽样可打分、双路径上下文收敛有文档与降级、OTEL 可选接入；**不**宣布 L3，**不**默认打开昂贵导出。

---

## Context（现状与问题）

### 从哪到哪

| 维度 | 现在（W10 后） | W11 出口切片 |
|---|---|---|
| 产品 | 只读 L2 Conditional + 学习路径可演示 | 同上 + **运维可看成本/质量面板数据** |
| 评测 | full 23/23 强化证据；W10 健康 live 蒸馏闭环 | 不回退；online 模板可填 1 轮 |
| Agent 指标 | W9：`agent_runs` / latency / re_evidence / replan / parallel | + **tokens** + **tool_calls**；Prom 查询/文档看板 |
| Trace | `maybe_export_harness_trace` 默认 **关** | 采样率可配；可选 OTEL 出口 |
| Online 评测 | 无模板 | `docs/pilot/online-eval-template.md` + 抽样脚本骨架 |
| 双路径上下文 | stateful 默认开；legacy ContextBuilder 仍在 | **rebuild-only 文档 + deprecated 标注**；开关清晰 |
| 审计 | W10 confirm 有 owner 日志 | 浅层 run 审计列表（可选最小） |
| H3 / L3 | 仍豁免 / 非 L3 | **不变**（W12 出口再评） |

### 已有可复用资产

| 资产 | 路径 | W11 用法 |
|---|---|---|
| Agent 指标骨架 | `app/core/metrics.py` `observe_agent_run` | 扩展 tokens/tools；不改标签基数爆炸 |
| complete 钩子 | `loop.py` observe + usage_total | 读 `state.usage_total` 打点 |
| Trace 导出 | `app/agent/harness/trace_export.py` | 采样 + 字段补 distill/anti |
| Prom scrape | `deploy/prometheus/prometheus.yml` | 补 recording/示例 query 文档 |
| Stateful 上下文 | `app/agent/context/*` · `HARNESS_STATEFUL_CONTEXT_ENABLED` | H4 收敛文档与测试 |
| 部署清单 | `docs/pilot/deploy-checklist.md` | 补成本看板抓取段 |

### 缺口（代码事实）

1. **无** `agent_tokens_total` / `agent_tool_calls_total`  
2. **无** online 抽样模板与脚本  
3. ContextBuilder 滚动摘要路径未正式标 deprecated / rebuild-only 说明不足  
4. OTEL / Langfuse **未接**；`OTEL_EXPORTER_OTLP_ENDPOINT` 不存在  
5. 成本「看板」无前端页——W11 以 **PromQL + 文档面板** 为主，前端可选一行

---

## 设计决策（含默认开关）

| ID | 决策 | 选择 | 理由 |
|---|---|---|---|
| D-W11-1 | 成本指标来源 | 优先 `state.usage_total`（prompt/completion）；缺失则 skip | 不二次调 LLM 估 token |
| D-W11-2 | 工具指标标签 | `tool` 白名单截断 + `status`∈{ok,error,timeout,other} | 防高基数 |
| D-W11-3 | 看板形态 | **文档 + PromQL 片段**；可选 `docs/pilot/cost-dashboard.md` | 控制前端范围 |
| D-W11-4 | Trace 默认 | 仍默认 **false**；`HARNESS_TRACE_SAMPLE_RATE=0.0` | 防磁盘/隐私 |
| D-W11-5 | OTEL | **可选**；endpoint 空则关；失败不进 complete 路径 | WP-G3 P2 |
| D-W11-6 | Online 抽样 | 离线模板 + 可选 `scripts/sample_online_runs.py` 读 traces/结果 | 不绑生产写回 |
| D-W11-7 | 双路径 H4 | stateful=true 为主路径；legacy 保留开关；文档写清「rebuild-only」 | 不物理删 ContextBuilder |
| D-W11-8 | 只读红线 | 指标/抽样/OTEL **永不**触发处置 | 产品红线 |
| D-W11-9 | 时延 | 指标与 trace 必须 best-effort try/except | 不拖 P50 |
| D-W11-10 | 范围 | **不做** Grafana 正式部署、不做完整 SSO、不做 L3 出口 | 防 scope creep |

---

## 范围与非目标

### W11 in-scope

1. **WP-W11-1 · G2b 成本/工具指标**  
   - `agent_tokens_total{role}`（prompt/completion/total）  
   - `agent_tool_calls_total{tool,status}`（白名单）  
   - `observe_agent_run` 扩展或旁路 `observe_agent_tokens` / `observe_tool_call`  
   - 文档：PromQL 示例 + cost-dashboard.md  

2. **WP-W11-2 · F4 Online 抽样**  
   - `docs/pilot/online-eval-template.md`（打分表：正确性/证据/安全/时延/备注）  
   - `scripts/sample_online_runs.py`：从 `volumes/traces` 或 eval results 抽样 N 条输出 CSV/MD 骨架  

3. **WP-W11-3 · H4 双路径收敛**  
   - 文档 ADR/段落：stateful 主路径；legacy ContextBuilder = rebuild/fallback only  
   - config 注释 + `.env.example` 标 deprecated 语义  
   - 单测：stateful on/off 仍可构建上下文（不回归）  

4. **WP-W11-4 · G3 OTEL 可选（最小）**  
   - 若 `OTEL_EXPORTER_OTLP_ENDPOINT` 非空：complete 时 export span 骨架（或明确「仅依赖 trace JSON」并文档化选型）  
   - **允许** W11 只做「选型 ADR + 开关位 + no-op exporter」若接入成本过高——须在 progress 写明  

5. **WP-W11-5 · Trace 采样增强**  
   - `HARNESS_TRACE_SAMPLE_RATE`（0–1，默认 0）  
   - payload 增加 distill_draft / anti_pattern / usage 摘要（无密钥）  

6. **WP-W11-6 · 文档/索引/单测收口**

### 明确非目标

1. 宣布 L3 / 无条件 L2 Go  
2. Grafana/Kibana 正式集群  
3. 默认打开全量 trace / OTEL  
4. 自动处置 / HITL 执行  
5. 静默 `AUTO_DISTILL=true`  
6. 物理删除 RouterService / ContextBuilder  
7. full 23 强制重跑（仅在改 harness 热路径时抽样回归）  
8. 前端完整成本大盘（最多 metrics 链接说明）

---

## 实施步骤（可验收）

### WP-W11-1 · 成本与工具指标（P0）

1. `metrics.py` 增加 Counter/Histogram（低基数）  
2. loop 工具结果路径：成功/失败 inc tool counter（可在现有 stream_tool_results 后汇总 timeline 一次扫描，避免每事件热路径过重）  
3. complete：从 `usage_total` 累加 tokens  
4. 单测：inc 不抛；标签白名单  
5. `docs/pilot/cost-dashboard.md`：  
   - 抓取 `/metrics`  
   - 示例：`sum(rate(agent_runs_total[5m]))`、`histogram_quantile(0.5, … agent_latency_seconds)`、`sum(rate(agent_tokens_total[1h])) by (role)`  

**文件**：`metrics.py`、`loop.py`、`config.py`（可选）、`.env.example`、`docs/pilot/cost-dashboard.md`、`tests/test_m3_w11_metrics_cost.py`

### WP-W11-2 · Online 抽样（P1）

1. 模板字段：run_id/session、route、latency、pass?、证据充分、是否幻觉/越权、HITL 是否误「已执行」、备注、打分人、日期  
2. 脚本：`--from-traces volumes/traces --n 5 --out docs/pilot/samples/`  
3. README 链到 evals 对照  

**文件**：`docs/pilot/online-eval-template.md`、`scripts/sample_online_runs.py`、可选空 samples 目录 `.gitkeep`

### WP-W11-3 · 双路径收敛（P1）

1. 写 `docs/pilot/context-dual-path.md` 或 `plan/` 短 ADR：  
   - 主：`HARNESS_STATEFUL_CONTEXT_ENABLED=true`  
   - 关：legacy ContextBuilder（滚动摘要/token window）  
   - rebuild-from-turns 语义  
2. config 字段注释 deprecated 提示（不删开关）  
3. 单测或文档命令：两种开关 smoke import  

**文件**：文档 + `config.py` 注释 + 可选 `tests/test_m3_w11_context_path.py`

### WP-W11-4 · OTEL 可选（P2）

**优先最小方案 A**（推荐）：  
- 仅增加 config `otel_exporter_otlp_endpoint: str = ""`  
- `docs/pilot/otel-optional.md`：选型（OTLP vs 仅 JSON trace）；接入步骤  
- 无 endpoint 时零依赖  

**方案 B**（时间允许）：  
- `app/agent/harness/otel_export.py` no-op/optional import opentelemetry  
- complete 打 root span attributes：route/latency/re_*/tokens  

若 B 超预算 → 落 A 并在 progress 标注「W12 可续」。

### WP-W11-5 · Trace 采样（P1，可与 W11-1 合并）

1. `HARNESS_TRACE_EXPORT_ENABLED` 仍总闸  
2. `HARNESS_TRACE_SAMPLE_RATE`：enabled 且 random()<rate 才写  
3. 单测：rate=0 不写；rate=1 且 enabled 写  

### WP-W11-6 · 收口

1. `plan/2026-07-15-m3-w11-progress.md`  
2. 更新 AGENTS/CLAUDE/M3 overview/交接  
3. 验证命令见下  

---

## 文件清单

| 文件 | 动作 |
|---|---|
| `app/core/metrics.py` | tokens/tools 指标 |
| `app/agent/harness/loop.py` | observe 扩展；timeline 工具汇总 |
| `app/agent/harness/trace_export.py` | 采样 + 字段 |
| `app/agent/harness/otel_export.py` | 可选新建 |
| `app/config.py` / `.env.example` | 新开关 |
| `docs/pilot/cost-dashboard.md` | 新建 |
| `docs/pilot/online-eval-template.md` | 新建 |
| `docs/pilot/context-dual-path.md` | 新建 |
| `docs/pilot/otel-optional.md` | 新建（若做 G3） |
| `scripts/sample_online_runs.py` | 新建 |
| `tests/test_m3_w11_*.py` | 新建 |
| `plan/2026-07-15-m3-w11-observability-online.md` | 本计划 |
| `plan/2026-07-15-m3-w11-progress.md` | 实现后 |
| `AGENTS.md` / `CLAUDE.md` / M3 overview / handoff | 索引 |

---

## 开关一览

| 开关 | 默认 | 说明 |
|---|---|---|
| `HARNESS_TRACE_EXPORT_ENABLED` | **false** | 总闸（已有） |
| `HARNESS_TRACE_SAMPLE_RATE` | **0.0** | 0–1；仅 enabled 时生效 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `""` | 空=关 |
| `HARNESS_STATEFUL_CONTEXT_ENABLED` | **true** | 主路径；false=legacy |
| 既有 agent metrics | 保持 W9 | 回滚不删指标 |

回滚：

```text
HARNESS_TRACE_EXPORT_ENABLED=false
HARNESS_TRACE_SAMPLE_RATE=0
OTEL_EXPORTER_OTLP_ENDPOINT=
# 指标为只增 Counter，关不上但无害；极端可回退代码
```

---

## 验证方式

```bash
# 单测
python -m pytest tests/test_m3_w11_metrics_cost.py tests/test_m3_w11_trace_sample.py \
  tests/test_m3_w10_distill.py tests/test_m3_w9_hitl_metrics.py -q --tb=line --no-cov

# 指标
curl -sS http://127.0.0.1:9900/metrics | findstr agent_

# 抽样脚本（需 traces 或 dry-run）
python scripts/sample_online_runs.py --n 3 --dry-run
```

### W11 出口标准

| # | 标准 |
|---|---|
| 1 | tokens/tools 指标在跑过对话后 `/metrics` 可见 |
| 2 | cost-dashboard 文档可复制 PromQL |
| 3 | online 模板存在；脚本 dry-run 成功 |
| 4 | 双路径文档存在；stateful 默认行为不回归 |
| 5 | OTEL：空 endpoint 零行为；或 ADR 写明延后 |
| 6 | 单测绿；N2/N3 安全不回退 |
| 7 | **不**宣布 L3 |

---

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 指标高基数 | tool 名白名单 + other 桶 |
| usage 字段缺失 | 跳过 tokens，不报错 |
| trace 磁盘膨胀 | 默认关 + sample rate + timeline cap 200 |
| OTEL 依赖地狱 | 可选 import；默认不装 |
| H4 误删 legacy | 只文档+注释，不删代码 |

---

## 实现顺序（批准后）

1. WP-W11-1 指标 + 单测  
2. WP-W11-5 trace 采样  
3. WP-W11-2 online 模板/脚本  
4. WP-W11-3 双路径文档  
5. WP-W11-4 OTEL 最小  
6. 文档索引 + progress  

**不在本批准范围自动开工 W12 / L3 评审。**

---

## 与路线图映射

| 路线图 | W11 |
|---|---|
| WP-G2 | **补全** tokens/tools + 看板文档 |
| WP-G3 | 可选最小 / ADR |
| WP-F4 | 模板 + 抽样脚本 |
| WP-H4 | 文档收敛 + deprecated 标注 |
| WP-H3 | 浅层（沿用 W10；可补 run 审计一句） |

---

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-15 | 初稿；W10 健康 live 后下一棒；待批准 |
| 2026-07-15 | 用户「w11开始」批准实现；主干合入 + progress |
