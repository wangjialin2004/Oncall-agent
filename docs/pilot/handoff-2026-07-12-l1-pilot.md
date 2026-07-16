# OnCall Agent L1 试点 — 交接文档（终版）

> **交接日期**：2026-07-12（晚间收口）；**2026-07-13 升正式 L1 Go**  
> **工作树**：`super_biz_agent_py-master-commit`  
> **当前决策**：**Go L1 技术试点（正式）** — 技术门禁 H1/H2/H6 已过；**H3 真人 OnCall 项目方豁免**（无固定值班兜底）  
> **目标读者**：接手运维本机试点、复现评测、或运维正式 L1 的同学  
> **决策主文档**：[`docs/pilot/go-nogo-20260712.md`](./go-nogo-20260712.md)（以 go-nogo 签字栏为准）  
> **演进续交接（M1 W1/W2，2026-07-13）**：[`docs/pilot/handoff-2026-07-13-l1-m1-w2.md`](./handoff-2026-07-13-l1-m1-w2.md) ← **继续开发/路线图请改读该文档**

---

## 0. 30 秒结论

| 项 | 状态 |
|---|---|
| 工程单测 | ✅ backend **258 passed**；frontend **52 passed** |
| 安全底线 | ✅ 固定账号 + token TTL + CORS 白名单 + 写接口鉴权；**已改强密码** |
| 数据面 | ✅ Prometheus pilot seed **3 firing** + 可能另有本机告警；ERROR 日志种子；MCP/Milvus/Redis 可用 |
| 质量（严格连续 8 题） | ✅ **8/8**（`20260712_171736`，**无** `llm_provider_degraded`） |
| Core S1–S5 | ✅ **5/5** |
| complete 率 | ✅ 100% |
| P50 / P95 | ⚠️ 实测 P50 **138.32s** / P95 **158.19s**；**H2 已书面接受 P50** |
| 回滚 L4 | ✅ MCP off/on |
| Checkpoint | ✅ 杀进程可恢复 complete；**H6 tool 后 kill：`resumable=true` step=2 resume≈20.7s** |
| 真人 OnCall（H3/D5） | ⚠️ **项目方豁免 / 跳过**（2026-07-13 升 Go 时再次确认）— **无固定值班人** |
| **正式 L1 Go** | ✅ **已开启（2026-07-13）** — H3 豁免后项目方指示升 Go |

**接手人核心认知**：技术门禁 H1/H2/H6 已关；**正式 L1 Go 已开启**；H3 无真人姓名/升级路径（项目方接受无真人兜底）。**不可**当作生产无人值守唯一主路径。

---

## 1. 必读文档（按顺序）

| 优先级 | 路径 | 用途 |
|---|---|---|
| **P0** | [`docs/pilot/go-nogo-20260712.md`](./go-nogo-20260712.md) | **当前决策、门禁表、H2 接受记录、H6 摘要、签字栏** |
| **P0** | 本文 `docs/pilot/handoff-2026-07-12-l1-pilot.md` | 如何拉起、评测、运维、剩余工作 |
| P0 | [`docs/pilot/2026-07-10-pilot-readiness-checklist-and-eval-suite.md`](./2026-07-10-pilot-readiness-checklist-and-eval-suite.md) | 验收标准与 23 题定义 |
| P1 | [`docs/pilot/2026-07-11-pilot-readiness-task-board.md`](./2026-07-11-pilot-readiness-task-board.md) | 任务板（部分状态可能落后，以 go-nogo + 本文为准） |
| P1 | [`docs/pilot/change-capability-unavailable.md`](./change-capability-unavailable.md) | 变更能力明确不可用（D2） |
| P1 | [`evals/oncall/README.md`](../evals/oncall/README.md) | 评测用例说明 |
| P2 | `docs/reviews/completion-review-2026-07-12-*.md` | 当日过程纪要（历史） |
| P2 | `docs/reviews/completion-review-2026-07-11.md` | 最初 No-Go 基线（历史） |

---

## 2. 环境与账号

### 2.1 本机依赖

| 组件 | 地址 | 说明 |
|---|---|---|
| Backend | `http://127.0.0.1:9900` | uvicorn `app.main:app` |
| Frontend | `http://127.0.0.1:5173` | Vite |
| MCP cls | `:8003` | `python mcp_servers/cls_server.py` |
| MCP monitor | `:8004` | `python mcp_servers/monitor_server.py` |
| Prometheus | `:9090` | `docker compose -f monitoring.yml up -d` |
| Milvus | `:19530` | docker：`milvus-standalone` + etcd + minio |
| Redis | `:6379` | 密码 **`123456`**（应用 URL：`redis://:123456@localhost:6379/0`，protocol=2） |
| LLM | `.env` 中 `LLM_*` | 上游可能 **HTTP 503 system cpu overloaded** |

### 2.2 登录凭据（敏感）

- 账号：`admin` / `pilot`（见 `.env` 的 `AUTH_USERS`）
- 密码：**不在本文写明文**
  - 本机文件：`logs/.pilot_pass`（单行密码）
  - 或：`logs/pilot_auth_once.txt`
- **旧密码 `admin:admin` 已失效**（应 401）
- **禁止**把 `logs/.pilot_pass`、`.env` 提交到 git 或发到公开渠道

### 2.3 关键 `.env` 试点值（非密钥）

```text
HARNESS_ENABLED=true
HARNESS_MCP_ENABLED=true
HARNESS_FORCE_EXPERT_DELEGATION=false
HARNESS_DELEGATION_ENABLED=true
HARNESS_STEP_TIMEOUT_SECONDS=45
HARNESS_DELEGATE_TIMEOUT_SECONDS=75
HARNESS_TOOL_TIMEOUT_SECONDS=30
HARNESS_TIMEOUT_SECONDS=180
HARNESS_MAX_STEPS=5
HARNESS_NO_PROGRESS_LIMIT=2
EXPERT_TIMEOUT_SECONDS=90
LLM_TIMEOUT=90
LLM_MAX_RETRIES=2
MONITOR_TARGET_MODE=prometheus
LOG_PROVIDER=local
PROMETHEUS_BASE_URL=http://127.0.0.1:9090
AUTH_TOKEN_TTL_SECONDS=86400
CORS_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

建议确认 checkpoint 相关开关仍为开启（默认/既有配置）：

```text
HARNESS_CHECKPOINT_ENABLED=true
# conservative：不要轻易开 harness_checkpoint_replay=true
```

### 2.4 交接时服务探活快照（2026-07-12 ~18:26 本机）

| 组件 | 状态 |
|---|---|
| Backend `/health` | healthy；Milvus connected；MCP cls/monitor reachable |
| Frontend | 200 |
| Prometheus firing | **4**（含 3 条 pilot seed + 本机 `HighMemoryUsage` 等；以 `/api/v1/alerts` 为准） |
| 密码文件 | `logs/.pilot_pass` 存在 |

> 若交接时服务已停，按 §3 重拉。H6 演练会 **kill/restart backend**，pid 会变属正常。

---

## 3. 如何拉起全套服务

在仓库根目录、已激活 `.venv` 的前提下：

```bash
# 0) Docker Desktop 必须已启动
docker info

# 1) 基础设施
docker start milvus-etcd milvus-minio milvus-standalone aiops-prometheus
# 若 aiops-prometheus 不存在：
#   docker compose -f monitoring.yml up -d

# Redis：若本机已有 Redis 且需密码
#   redis-cli CONFIG SET requirepass 123456
# 或确保应用能 PING redis://:123456@localhost:6379/0

# 2) 等 Milvus healthy
docker inspect -f '{{.State.Health.Status}}' milvus-standalone

# 3) 应用进程（Windows Git Bash 示例）
mkdir -p logs
source .venv/Scripts/activate
python mcp_servers/cls_server.py > logs/mcp_cls_run.log 2>&1 &
python mcp_servers/monitor_server.py > logs/mcp_monitor_run.log 2>&1 &
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900 > logs/backend_run.log 2>&1 &
echo $! > logs/backend_run.pid
cd frontend && npm run dev -- --host 127.0.0.1 --port 5173 > ../logs/frontend_run.log 2>&1 &

# 4) 探活
curl -s http://127.0.0.1:9900/health
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5173/
curl -s http://127.0.0.1:9090/api/v1/alerts
# pilot seed 至少应有：
#   PilotCheckoutApiCpuHigh / PilotPaymentServiceMemoryHigh / PilotUserServiceSlowResponse
```

### 3.1 已知坑

1. **Docker Desktop 关闭**后 Prometheus/Milvus 全挂；backend 会 unhealthy。  
2. **Redis 无密码时**应用带密会失败；之前用过 `CONFIG SET requirepass 123456`。  
3. **LLM 503**：上游 `system cpu overloaded`，不是本仓库 bug；评测脚本会标 `llm_provider_degraded` 并判失败。  
4. **git worktree 元数据断链**：`git` 历史可能不可用，以本地文件为准。  
5. Windows 控制台 **GBK** 打印中文答案可能炸；脚本已尽量写 UTF-8 文件。  
6. 连续 8 题墙钟约 **15–20 分钟**；IDE/工具默认 10 分钟超时会杀前台进程 — **请用后台 nohup 跑评测**。  
7. H6 会 `taskkill` 占用 `:9900` 的 backend，跑完后确认 `/health` 仍 healthy。

---

## 4. 评测怎么跑

### 4.1 脚本与用例

- 脚本：[`scripts/evaluate_oncall_local.py`](../scripts/evaluate_oncall_local.py)
- 用例：[`evals/oncall/cases.jsonl`](../evals/oncall/cases.jsonl)（最小：S1–S5 + N1 + N3 + M1）
- 结果目录：`evals/results/`

### 4.2 推荐命令（严格连续）

```bash
source .venv/Scripts/activate
export PYTHONUNBUFFERED=1
export ONCALL_EVAL_PASSWORD="$(cat logs/.pilot_pass)"

# 可选：先用短问题探活 LLM，确认答案里没有 503 / system cpu overloaded

# 严格连续 8 题（建议间隔 12~15s，减轻上游 503）
# 注意：请后台跑，避免前台超时截断
nohup python -u scripts/evaluate_oncall_local.py \
  --timeout-extra 90 \
  --inter-case-sleep 15 \
  > logs/eval_strict_continuous_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 单题重试
python -u scripts/evaluate_oncall_local.py --case S3-disk-high --timeout-extra 90
```

### 4.3 脚本能力（已实现）

- 登录用 `logs/.pilot_pass` 或 `ONCALL_EVAL_PASSWORD`
- SSE CRLF 兼容
- 答案含 `HTTP 503` / `system cpu overloaded` / `请稍后重试` 等 → **`llm_provider_degraded`，不算通过**
- `--timeout-extra`、`--inter-case-sleep`
- 输出 CSV + JSON 汇总（passed / core_pass / must_pass / p50 / p95 / complete_rate）

### 4.4 关键结果文件（主证据优先）

| 文件 | 含义 |
|---|---|
| **`evals/results/oncall_minimal_20260712_171736.json`** | **当前主证据：严格连续 8/8**，无 degraded；P50=138.32s |
| `evals/results/oncall_minimal_20260712_171455.json` | S3 单题重试验证 10.0 / diagnosis / 52.65s |
| `evals/results/oncall_minimal_20260712_165213.json` | 严格连续 7/8，**S3=LLM 503**（对照：工具成功但最终生成 503） |
| `evals/results/oncall_minimal_20260712_160315.json` | 严格连续 7/8，N3=503（历史） |
| `logs/checkpoint_resume_drill_result.json` | **H6 主证据**（after_tool / resumable=true / step=2） |
| `logs/checkpoint_resume_drill_result_20260712_174934.json` | H6 结果戳记副本 |
| `logs/eval_strict_continuous_20260712_171736.log` | 严格 8/8 控制台日志 |

### 4.5 严格连续单题明细（171736）

| Case | pass | score | route | latency_s |
|---|---|---|---|---|
| S1-cpu-high | ✅ | 9.0 | diagnosis | 138.32 |
| S2-mem-high | ✅ | 9.0 | diagnosis | 158.19 |
| S3-disk-high | ✅ | 10.0 | knowledge | 97.65 |
| S4-service-down | ✅ | 9.0 | diagnosis | 152.23 |
| S5-slow-response | ✅ | 10.0 | diagnosis | 105.51 |
| N1-no-datasource | ✅ | 8.1 | metric | 124.58 |
| N3-no-remediation | ✅ | 9.1 | change | 13.07 |
| M1-two-turn | ✅ | 10.0 | log | 176.37 |

汇总：`passed=8/8`，`core_pass=5`，`must_pass` 全 true，`complete_rate=1.0`，`p50=138.32`，`p95=158.19`。

### 4.6 质量口径纪律

- **严格连续** = 单进程一次跑完 8 题、中间不挑失败题重跑拼满分。  
- **禁止**用「多次重试合并最佳」冒充严格连续稳态。  
- 上游 503 时失败是**预期门禁行为**，不是评测脚本误杀。

---

## 5. 已完成改动 / 已关闭门禁（不要重复做）

### 5.1 工程

- 修复 `tests/test_harness_service.py` IndentationError / BOM
- 前端 `loadSession` 与流式发送竞态（`frontend/src/App.tsx`）
- frontend 测试对齐 UI 文案 → 52/52

### 5.2 安全

- `app/config.py`：`auth_users` / `auth_token_ttl_seconds` / `cors_allow_origins`
- `app/services/auth_service.py`：token `exp`
- `app/api/auth.py`：固定账号校验
- `app/api/memory.py`：写接口 `require_session_owner`
- `tests/test_auth_security.py`（7 条）
- `.env` 强密码 + 强 `AUTH_TOKEN_SECRET`

### 5.3 数据面

- `MONITOR_TARGET_MODE=prometheus`
- `deploy/prometheus/alerts.yml` pilot seed 组（`vector(1)` 常亮 firing）  
  - `PilotCheckoutApiCpuHigh` / `PilotPaymentServiceMemoryHigh` / `PilotUserServiceSlowResponse`
- 种子 ERROR 日志：`logs/checkout-api_pilot.log`、`logs/payment-service_pilot.log`、`logs/user-service_pilot.log`

### 5.4 运维脚本

- `scripts/evaluate_oncall_local.py`（含 503 质量门禁）
- `scripts/checkpoint_resume_drill.py`（**默认 after-tool / H6**）
- L4 回滚记录：`logs/rollback_drill_20260712.log`
- Checkpoint 结果：`logs/checkpoint_resume_drill_result.json`

### 5.5 产品边界

- 变更源仍不可用（`docs/pilot/change-capability-unavailable.md`），勿接写操作

### 5.6 本轮已关闭的交接任务

| ID | 结论 |
|---|---|
| **H1** | ✅ 严格连续 8/8（`171736`） |
| **H2** | ✅ 书面接受 P50=138.32s（见 go-nogo「H2 时延门禁 — 书面接受记录」） |
| **H3** | ⚠️ **项目方豁免**（2026-07-12 暂缓；2026-07-13 升 Go 时再次跳过）— 无值班人姓名 |
| **H4** | ✅ **2026-07-13 升正式 Go L1**（H3 豁免路径） |
| **H6** | ✅ tool 后 kill：`resumable=true` step=2 resume complete≈20.7s |

---

## 6. 你接手后可做的工作（按优先级）

### P0 — 正式 L1 Go 已开启后的运行纪律

| ID | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| H3b | （可选）补齐真人 OnCall | 姓名+联系+备份+升级路径+覆盖时段 写入 go-nogo | 豁免中；需要有人兜底时再做 |
| H4 | 正式 Go L1 | go-nogo 已勾选 | ✅ 2026-07-13 |

H3 将来补齐时的最小回复模板：

```text
H3 值班代表：姓名=____；联系=____；备份=____；升级路径=____；覆盖时段=____；日期=YYYY-MM-DD
```

### P1 — 质量与运维加深（不阻断 L1 Go）

| ID | 任务 | 验收标准 |
|---|---|---|
| H5 | 路由校准 S3（及偶发 knowledge 漂移） | 资源类题优先 metric/diagnosis（171736 中 S3 仍为 knowledge） |
| H7 | 扩展 cases 至 23 题并抽测 | checklist §3；L1 建议 ≥18/23 |
| H8 | L1/L2/L3/L5/L6 回滚逐项重启演练 | 任务板 §4.4 打勾（L4 已演练） |
| H2b | （可选）压 P50 | 慢题：S2/S4/M1；手段：降 `HARNESS_MAX_STEPS`、减 tool 扇出 |

### P2 — 工程化

| ID | 任务 |
|---|---|
| H9 | 最小 CI：lint + pytest + frontend test |
| H10 | 一键启停脚本（Windows / make）固化到 README |
| H11 | aiops-docs 五类 runbook 索引验收 |
| H12 | 任务板勾选状态与 go-nogo 对齐回写 |

---

## 7. Checkpoint 演练（H6）

```bash
source .venv/Scripts/activate
export ONCALL_EVAL_PASSWORD="$(cat logs/.pilot_pass)"
python -u scripts/checkpoint_resume_drill.py
# 结果：logs/checkpoint_resume_drill_result.json
# 日志：logs/checkpoint_resume_drill_20260712.log
# 脚本会 kill :9900 backend 并自动拉起，结束后确认 /health
```

### 7.1 H6 实测摘要（主证据）

| 项 | 值 |
|---|---|
| session | `ckpt-drill-tool-1783849719` |
| drill_mode | `after_tool` |
| 杀前 | `saw_tool=true` / `saw_tool_done=true`；tool=`context_read` |
| Redis keys | 5（meta / messages / steps:1 / steps:2 / context） |
| 重启后 API | **enabled=true，resumable=true，step=2，route=diagnosis** |
| 续跑 | **complete=true**，latency≈**20.68s**，err 空 |
| 续跑后 | completed=true，resumable=false，step=2 |
| step_resume_evidence | **true** |
| passed | **true** |

### 7.2 历史对照（route-only kill）

- session `ckpt-drill-1783844343`：杀前无 tool → `resumable=false`，同 session 再 POST 仍 complete（≈142.8s，fresh-run 风格）

### 7.3 API

- `GET /api/checkpoint/{session_id}`（需 Bearer）
- `DELETE /api/checkpoint/{session_id}`
- 真正 resume：再次 `POST /api/assistant`，同一 `Id`，`CheckpointReplay=false`（conservative）

---

## 8. 回滚开关速查

| 级别 | 环境变量 | 预期 | 本轮 |
|---|---|---|---|
| L1 | `HARNESS_STATEFUL_CONTEXT_ENABLED=false` | 回旧 ContextBuilder | 未全量重启演练 |
| L2 | `HARNESS_CONTEXT_DB_SNAPSHOT_ENABLED=false` | 仅 Redis / rebuild | 未全量重启演练 |
| L3 | `HARNESS_CONTEXT_TOOLS_ENABLED=false` | 无 context_read/note | 未全量重启演练 |
| L4 | `HARNESS_MCP_ENABLED=false` | 仅本地工具+委派 | ✅ 已演练 |
| L5 | `HARNESS_CHECKPOINT_ENABLED=false` | 无续跑 | 开关齐 |
| L6 | `LONG_TERM_MEMORY_ENABLED=false` | 无经验召回 | 开关齐 |
| Emergency | 停 backend | 无脏写 | ✅ 演练中多次 |

改 `.env` 后需 **重启 uvicorn** 才生效。

---

## 9. 决策升级公式

```text
正式 L1 Go 当且仅当：
  工程 P0 全绿
  AND 安全凭据非默认弱口令
  AND 数据面有 firing 告警 + 可查 ERROR 日志
  AND 严格连续最小 8 题：S1–S5≥4/5 且 N1/N3/M1 过 且 complete=100% 且无 llm_provider_degraded
  AND （P50≤90s 或书面接受）          ← H2 已书面接受 138.32s
  AND （指定真人 OnCall 并签字 或 项目方书面豁免 H3）  ← 2026-07-13 项目方豁免
  AND 至少 1 次崩溃恢复演练             ← 已有；H6 tool 后 resume 已验证

否则 → Conditional Go 或 No-Go
```

**当前**：**Go L1 技术试点（正式）** — 2026-07-13  
- 允许：受控 L1 技术值班辅助、影子诊断、告警/日志取证、N3 安全拒绝、对内演示  
- 不允许 / 风险：无真人值班兜底（H3 豁免）；唯一无人值守生产主路径；外传试点密码；在 503 频发时宣称永久达标；生产写操作；把 P50=138s 当正式 SLA  

---

## 10. 建议接手第一天日程

```text
上午
  1. 读完本文 + docs/pilot/go-nogo-20260712.md（注意：已升正式 Go，H3 豁免）
  2. 确认 Docker + 全套服务；/health 与 pilot seed alerts
  3. 用 logs/.pilot_pass 登录前端点一次诊断题（勿外传密码）

下午（按目标选做）
  A. 维持 L1 运行：抽测 1~2 题 + 确认 rollback 开关
  B. （可选）补 H3 真人值班信息到 go-nogo
  C. 质量加深：压 S2/M1 时延，或校准 S3 路由，或扩展 23 题抽测

不要
  - 用合并重试冒充严格连续
  - 在 H3 豁免下对外宣称「有人 7×24 兜底」
  - 当作唯一生产写操作入口
```

---

## 11. 联系与未决产品决策

| ID | 决策 | 当前结论 |
|---|---|---|
| D1 | 数据面路径 | **方案 A**：`HARNESS_MCP_ENABLED=true` + MCP 进程 |
| D2 | 变更能力 | **标注不可用**（勿接写操作） |
| D3 | 交付档 | 目标 **C**（评测出分+纪要）；A/B 主干已完成 |
| D4 | Conditional / 正式 Go | **2026-07-13 已升正式 Go L1**（H3 豁免） |
| D5 | OnCall 联系人 | ⚠️ **豁免**（要有人兜底时再指定） |

---

## 12. 本会话关键故障时间线（便于排障）

| 时间（约） | 事件 | 结论 |
|---|---|---|
| 16:31–16:41 | 严格连续被 10min 超时截断；S1 曾 503 | 应用后台 nohup |
| 16:52–17:07 | 严格连续 `165213` → **7/8**，S3=`llm_provider_degraded` | 工具成功，最终 LLM 503 |
| 17:14 | S3 单题重试 `171455` → **10.0 / diagnosis / 52.65s** | S3 能力本身 OK |
| 17:17–17:35 | 严格连续 `171736` → **8/8** | H1 关闭 |
| 同日晚 | H2 项目方书面接受 P50=138.32s | H2 关闭 |
| 同日晚 | H3 项目方指示跳过 | 当时维持 Conditional |
| 17:48–17:49 | H6 after-tool drill | `resumable=true` step=2 resume 20.7s |
| **2026-07-13** | 项目方指示「正式l1go」+ 跳过 H3 | **正式 Go L1 开启**（H3 豁免落档） |

S3 失败根因备忘：答案为「知识问答专家…HTTP 503 system cpu overloaded (93.9%>90%)」；数据面未坏。

---

## 13. 交接检查清单（签字用）

- [ ] 接手人已能本地拉起 backend/frontend/MCP/Prometheus/Milvus/Redis  
- [ ] 接手人已知 `logs/.pilot_pass` 位置且不会外传  
- [ ] 接手人已读 `docs/pilot/go-nogo-20260712.md`  
- [ ] 接手人清楚：**H1/H2/H6 已过；H3 豁免；决策=正式 Go L1（无真人兜底）**  
- [ ] 接手人清楚：**不要用合并重试冒充严格连续 8/8**  
- [ ] 当前服务是否保持运行：_______（是/否；否需按 §3 重拉）  

| 角色 | 姓名 | 日期 | 签字 |
|---|---|---|---|
| 交方 | Claude Code 会话执行 | 2026-07-12 | 自动产出本文 + 回写 go-nogo |
| 正式 Go 决策 | 项目方（「正式l1go」+ 跳过 H3） | **2026-07-13** | 升正式 L1 Go |
| 接方 | | | |
| 值班代表（D5/H3） | **豁免** | 2026-07-12 / 2026-07-13 | 项目方两次跳过 |

---

## 14. 一句话

> **正式 L1 Go（2026-07-13）**：严格连续 **8/8**、P50 **已书面接受**、checkpoint **tool 后可 resumable 续跑**；**H3 真人 OnCall 项目方豁免** → **正式 L1 技术试点开启（无固定值班兜底）**。允许受控 L1 辅助诊断；**不可**当作唯一无人值守生产主路径；上游 LLM 503 仍可能在高负载复发。
