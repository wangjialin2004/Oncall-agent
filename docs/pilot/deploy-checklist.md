# 部署 / 密钥检查清单（M3 W9 骨架 · W10 compose · W11 观测 · W12 预生产）

> 目标：新机器 30–60 分钟内复现 L2/W9 评测基线。  
> **W10**：增加 compose **草案**；Windows 主路径仍为 bat。  
> **W11**：成本/工具指标 + online 抽样模板；trace/OTEL **默认关**。  
> **W12**：预生产 runbook + 回滚 L4 → [preprod-runbook.md](./preprod-runbook.md) · L3 出口 → [l3-exit-review-2026-07-15.md](./l3-exit-review-2026-07-15.md)

## 1. 依赖端口

| 组件 | 默认地址 |
|---|---|
| Backend | `http://127.0.0.1:9900` |
| Frontend | `http://127.0.0.1:5173` |
| MCP cls / monitor | `:8003` / `:8004` |
| Prometheus | `:9090` |
| Milvus | `:19530` |
| Redis | `:6379` |

## 2. 启动前检查

```bash
python scripts/check_env_secrets.py
# 可选严格模式（默认密钥也失败）：
python scripts/check_env_secrets.py --strict
```

必填 / 强烈建议：

- `LLM_API_KEY` 或 `DASHSCOPE_API_KEY`（对话模型）
- 向量嵌入默认 **本地 BGE-M3**（`EMBEDDING_PROVIDER=local_bge_m3`）：`pip install -e ".[embedding]"`，首次会下载 `BAAI/bge-m3`
- 若回退云 embedding：`EMBEDDING_PROVIDER=dashscope` + `DASHSCOPE_API_KEY`
- 切换 embedding 模型后必须重建：`python scripts/rebuild_vector_collection.py --yes`，并重建经验记忆索引
- `AUTH_TOKEN_SECRET`（勿用默认 `dev-auth-token-secret`）
- `AUTH_USERS`（勿长期 `admin:admin`）
- `PROMETHEUS_BASE_URL` + `MONITOR_TARGET_MODE=prometheus`
- `HARNESS_MCP_ENABLED=true`（试点）

### W10 蒸馏 / 反模式（默认安全）

```text
LONG_TERM_MEMORY_DISTILL_ENABLED=true
LONG_TERM_MEMORY_AUTO_DISTILL=false
LONG_TERM_MEMORY_DISTILL_REQUIRE_CONFIRM=true
HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=true
```

### W11 观测 / 抽样（默认关昂贵导出）

```text
HARNESS_TRACE_EXPORT_ENABLED=false
HARNESS_TRACE_SAMPLE_RATE=0.0
OTEL_EXPORTER_OTLP_ENDPOINT=
HARNESS_STATEFUL_CONTEXT_ENABLED=true
```

看板与抽样文档：

- [cost-dashboard.md](./cost-dashboard.md) — PromQL / `/metrics`
- [online-eval-template.md](./online-eval-template.md) + `python scripts/sample_online_runs.py --n 3 --dry-run`
- [context-dual-path.md](./context-dual-path.md) · [otel-optional.md](./otel-optional.md)

## 3. 启动路径对照

### 3.1 Windows 主路径（推荐）

```bat
start-all-windows.bat
```

**注意**：`/health` 已绿时 bat **会跳过** backend 重启。改 harness / 合码后必须：

1. 结束占用 9900 的 uvicorn  
2. 再启动 backend  

### 3.2 Compose 草案（Docker Desktop / Linux）

```bash
# 仅 Redis
docker compose -f deploy/compose/docker-compose.pilot.yml up -d redis

# 实验 full profile（backend Dockerfile 为草案，MCP 仍建议本机 bat）
docker compose -f deploy/compose/docker-compose.pilot.yml --profile full up -d
```

文件：

- `deploy/compose/docker-compose.pilot.yml`
- `deploy/compose/Dockerfile.backend`

Milvus / MCP 默认 **外置**：继续用本机进程或既有部署，不在本草案强制容器化。

## 4. 健康检查

```bash
curl -sS http://127.0.0.1:9900/health
curl -sS http://127.0.0.1:9900/metrics | findstr agent_
# 期望可见：agent_runs_total / agent_latency / agent_tokens_total / agent_tool_calls_total（有流量后）
```

## 5. 评测

```bash
python scripts/evaluate_oncall_local.py --suite minimal --timeout-extra 90
python scripts/evaluate_oncall_local.py --suite full --timeout-extra 90
```

## 6. 密钥红线

- 不提交 `.env`、`logs/.pilot_pass`
- 不在文档写生产密码
- 变更源保持 `CHANGE_SOURCE_POLICY=unavailable`
- 默认 `LONG_TERM_MEMORY_AUTO_DISTILL=false`（禁止静默全量蒸馏）

## 7. 回滚（M3 W9 + W10 + W11）

```text
HARNESS_FORCE_PARALLEL_ON_CROSS_DOMAIN=false
HARNESS_REPLAN_ON_PRIMARY_FAIL=false
HITL_SUGGESTED_ACTIONS_ENABLED=false
ONCALL_ESCALATION_CONTACTS=
HARNESS_TIMEOUT_SOFT_CLOSE_ENABLED=false
LONG_TERM_MEMORY_DISTILL_ENABLED=false
LONG_TERM_MEMORY_AUTO_DISTILL=false
HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=false
HARNESS_TRACE_EXPORT_ENABLED=false
HARNESS_TRACE_SAMPLE_RATE=0
OTEL_EXPORTER_OTLP_ENDPOINT=
```
