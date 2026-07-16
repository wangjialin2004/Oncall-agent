# 预生产 Runbook + 回滚 L4（M3 W12）

> **日期**：2026-07-15  
> **产品定位**：**只读 L3 预生产白名单值班副驾（Conditional）** — **非**无人值守生产唯一主路径  
> **H3**：真人 OnCall **继续豁免**（`ONCALL_ESCALATION_CONTACTS` 默认真空；升级块输出「未配置」）  
> **关联**：[deploy-checklist](./deploy-checklist.md) · [W12 计划](../../plan/2026-07-15-m3-w12-l3-exit.md) · [L3 出口](./l3-exit-review-2026-07-15.md)

---

## 1. 白名单范围（建议）

| 允许 | 禁止 |
|---|---|
| 内部试点服务 / 非关键路径诊断 | 作为唯一 OnCall 主路径无人值守 |
| 只读查指标 / 日志 / 知识 / 变更缺口声明 | 自动重启、回滚、扩缩容、删改数据 |
| 半自动经验 draft + 人工 confirm | 静默 `AUTO_DISTILL=true` 全量入库 |
| HITL 建议动作（确认仅审计） | 确认后触发真实处置 |

---

## 2. 拉起（30–60 分钟目标）

### 2.1 密钥

```bash
python scripts/check_env_secrets.py
# 严格：
python scripts/check_env_secrets.py --strict
```

必填：`LLM_API_KEY` 或 `DASHSCOPE_API_KEY`（对话）；向量默认本地 BGE-M3（`pip install -e ".[embedding]"`）。`AUTH_TOKEN_SECRET`（勿默认）；建议改掉 `admin:admin`。切换 embedding 后须 `rebuild_vector_collection.py --yes` + 经验记忆 reindex。

### 2.2 进程（Windows 主路径）

```bat
start-all-windows.bat
```

**坑**：`/health` 已绿时 bat 可能跳过 backend 重启 → 合码后需手动结束 9900 再启。

### 2.3 健康

```bash
curl -sS http://127.0.0.1:9900/health
curl -sS http://127.0.0.1:9900/metrics | findstr agent_
```

期望：health 200；有流量后可见 `agent_runs_total` / `agent_tokens_total` / `agent_tool_calls_total`。

### 2.4 Compose 草案（可选）

见 [deploy-checklist §3.2](./deploy-checklist.md) — Redis/backend profile；MCP/Milvus 常外置。

---

## 3. 预生产推荐默认开关

```text
# 产品红线
CHANGE_SOURCE_POLICY=unavailable
LONG_TERM_MEMORY_AUTO_DISTILL=false
LONG_TERM_MEMORY_DISTILL_REQUIRE_CONFIRM=true

# 质量（可开）
HARNESS_RE_EVIDENCE_ENABLED=true
HARNESS_REPLAN_ENABLED=true
HARNESS_PARALLEL_DELEGATION_ENABLED=true
HARNESS_FORCE_PARALLEL_ON_CROSS_DOMAIN=true
HARNESS_SHARED_KERNEL_DELEGATION=true
HITL_SUGGESTED_ACTIONS_ENABLED=true
HARNESS_STATEFUL_CONTEXT_ENABLED=true

# 昂贵导出（默认关）
HARNESS_TRACE_EXPORT_ENABLED=false
HARNESS_TRACE_SAMPLE_RATE=0.0
OTEL_EXPORTER_OTLP_ENDPOINT=

# H3 — 本试点继续豁免时保持空
ONCALL_ESCALATION_CONTACTS=
```

看板 / 抽样：

- [cost-dashboard.md](./cost-dashboard.md)
- [online-eval-template.md](./online-eval-template.md) · `python scripts/sample_online_runs.py --n 3 --dry-run`

---

## 4. 回滚 L4（一键语义 · env 降级）

按故障面由轻到重：

### L4-1 观测 / 学习

```text
HARNESS_TRACE_EXPORT_ENABLED=false
HARNESS_TRACE_SAMPLE_RATE=0
OTEL_EXPORTER_OTLP_ENDPOINT=
LONG_TERM_MEMORY_DISTILL_ENABLED=false
HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=false
```

### L4-2 编排增强

```text
HARNESS_RE_EVIDENCE_ENABLED=false
HARNESS_REPLAN_ENABLED=false
HARNESS_FORCE_PARALLEL_ON_CROSS_DOMAIN=false
HARNESS_PARALLEL_DELEGATION_ENABLED=false
HARNESS_PARALLEL_TOOL_CALLS=false
HARNESS_TIMEOUT_SOFT_CLOSE_ENABLED=false
HITL_SUGGESTED_ACTIONS_ENABLED=false
```

### L4-3 内核 / 数据面

```text
HARNESS_SHARED_KERNEL_DELEGATION=false
HARNESS_MCP_ENABLED=false
HARNESS_STATEFUL_CONTEXT_ENABLED=false
```

### L4-4 停服

停止 backend / 前端；保留 Redis/DB 以便 checkpoint 审计；**不要**为「修 Agent」去执行生产变更。

改开关后必须 **重启 backend** 使 Settings 重载。

---

## 5. 事故话术（给值班）

1. Agent 是 **只读副驾**，结论需人确认。  
2. 变更源 **不可用**（option B）— 禁止编造版本/操作人。  
3. 建议动作确认 **不执行**。  
4. 升级联系人 **未配置**（H3 豁免）— 走你们现有 OnCall 渠道。  
5. 异常时先 L4-1/2 降级，再查 `/health` 与 MCP。

---

## 6. 验收自检（接手 15 分钟）

| # | 检查 | 期望 |
|---|---|---|
| 1 | `/health` | 200 / healthy 或可解释 degraded |
| 2 | 登录 + 一条诊断 | SSE complete；无「已执行变更」 |
| 3 | `/metrics` | `agent_*` 系列存在 |
| 4 | distill（若开） | draft pending；confirm 不执行 |
| 5 | 回滚清单 | 本文 §4 可复制 |

---

## 7. 非目标

- 不在此 runbook 承诺 SLA / 7×24 无人值守  
- 不替代真人事件指挥官  
- 不部署正式 Grafana 集群（见 cost-dashboard PromQL）
