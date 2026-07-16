# OnCall L1 试点 — Day0 基线快照

> 日期：2026-07-11  
> 来源命令与探活结果；配套 [任务板](./2026-07-11-pilot-readiness-task-board.md)

---

## 1. Pytest

```text
命令：python -m pytest tests --tb=no --no-cov
结果：242 passed / 9 failed
耗时：约 106s
```

### 失败用例（与验收文档 §1.1 一致）

| # | 用例 | 任务板映射 |
|---|---|---|
| 1 | `tests/test_harness_service.py::test_router_semantic_parser_tolerates_malformed_aux_routes` | T1.5 |
| 2 | `tests/test_harness_service.py::test_assistant_stream_injects_attachment_context_into_message` | T1.2 |
| 3 | `tests/test_harness_service.py::test_assistant_history_keeps_attachment_context_for_follow_up` | T1.2 |
| 4 | `tests/test_harness_service.py::test_assistant_harness_two_turn_flow_persists_and_reloads_history` | T1.1 |
| 5 | `tests/test_harness_service.py::test_harness_verify_marks_answer_without_tool_evidence_as_degraded` | T1.3 |
| 6 | `tests/test_harness_service.py::test_harness_asks_for_missing_metric_subject_after_plan` | T1.4 |
| 7 | `tests/test_harness_service.py::test_context_builder_folds_oversized_rolling_summary_input_turn` | T1.6 |
| 8 | `tests/test_harness_service.py::test_assistant_keyword_resolves_historical_attachment_without_new_upload` | T1.2 |
| 9 | `tests/test_harness_service.py::test_assistant_history_resolves_attachment_by_keyword_and_reloads_full_content` | T1.2 |

> 附件相关失败 4 条均归 T1.2；两轮历史 T1.1；verify T1.3；metric 澄清 T1.4；malformed aux_routes T1.5；rolling summary T1.6。

---

## 2. 依赖可达性（本机探活）

| 组件 | 可达 | 观测 |
|---|---|---|
| Redis | 端口开 | `redis-cli ping` → `NOAUTH Authentication required`（需带密码；config 默认 `redis://:123456@localhost:6379/0`） |
| Prometheus | 是 | `/-/ready` Ready；`/api/v1/alerts` 成功但 **alerts 为空** |
| Milvus | 是 | 返回 collections：`experience_memory`, `biz`（**未见明确 aiops-docs 专用确认**，知识索引需再验） |
| MCP cls :8003 | 否 | 端口超时 |
| MCP monitor :8004 | 否 | 端口超时 |
| Backend :9900 | 否 | 未启动 |
| Frontend :5173 | 否 | 未启动 |
| LLM API | 未测 | `.env` 已配置 `LLM_API_KEY`（值不入库） |

---

## 3. 配置对照（代码默认 vs 当前 `.env` vs L1 建议）

| 配置 | 代码默认 (`app/config.py`) | 当前 `.env` | L1 建议 | 差距 |
|---|---|---|---|---|
| `HARNESS_ENABLED` | `true` | `true` | `true` | OK |
| `HARNESS_MCP_ENABLED` | `false` | **`true`（倾向方案 A）** | A=true / B=false+委派 | MCP 进程未起 → 方案 A 未闭环 |
| `HARNESS_FORCE_EXPERT_DELEGATION` | `false` | `false` | 若实现可用则 true | 未强制委派 |
| `HARNESS_CORRECTIVE_VERIFY_ENABLED` | `true` | 未覆写 | `true` | 默认 OK |
| `HARNESS_LLM_PLANNING_ENABLED` | `false` | 未覆写 | 先 false | OK |
| `HARNESS_LLM_VERIFY_ENABLED` | `false` | 未覆写 | 先 false | OK |
| `HARNESS_CHECKPOINT_ENABLED` | `true` | 未覆写 | `true` | 依赖 Redis 密码连通 |
| `HARNESS_CHECKPOINT_REPLAY` | `false` | 未覆写 | `false` | OK（conservative） |
| `HARNESS_STATEFUL_CONTEXT_ENABLED` | `true` | 未覆写 | true（修好多轮后） | 多轮测试仍红 |
| `MONITOR_TARGET_MODE` | **`self`** | **未设置** | **`prometheus`** | **P0 缺口** |
| `LOG_PROVIDER` | `local` | 未设置 | 试点真实源 | 待决策 |
| `REDIS_ENABLED` | `true` | 未覆写 | `true` | 需验证带密连接 |
| `LONG_TERM_MEMORY_ENABLED` | `true` | 未覆写 | `true` | OK |
| `AUTH_TOKEN_SECRET` | **`dev-auth-token-secret`** | **未设置** | 强随机 | **P0 安全缺口** |
| CORS | 代码写死 `allow_origins=["*"]` | — | 试点白名单 | **P0 安全缺口** |
| `llm_api_key` 默认字面量 | 代码默认 `get.env('LLM_API_KEY')` | `.env` 已覆盖 | 无占位进入运行时 | 默认值仍危险；运行时依赖 .env |

### 安全代码现状（摘录）

- 登录：`app/api/auth.py` — **任意非空用户名+密码即发 token**（无用户库校验）
- Token：`auth_service.py` — payload 仅有 `sub`/`iat`，**无 TTL/过期校验**
- CORS：`app/main.py` — `allow_origins=["*"]`

---

## 4. 评测资产

| 路径 | 状态 |
|---|---|
| `evals/oncall/cases.jsonl` | 已落地 12 条（文档 §6 最小包） |
| `evals/oncall/README.md` | 已落地 |
| `evals/oncall/fixtures/` | 空目录占位 |
| `evals/results/` | 占位 `.gitkeep` |
| `scripts/evaluate_oncall_local.py` | **未实现**（T4.3） |

---

## 5. Day0 结论

| 门禁 | 结论 | 说明 |
|---|---|---|
| 工程 | **未过** | 9 failed 仍在 |
| 安全 | **未过** | 任意密码登录、无 TTL、默认 secret、CORS `*` |
| 数据面 | **未过** | MCP 未起；Prom 无告警；`MONITOR_TARGET_MODE` 仍 self；backend 未起 |
| 上下文 | **未过** | 两轮/附件相关单测红 |
| 评测资产 | **部分完成** | 最小 cases 已就绪，可并行评分脚本/手工跑 |

**当前判定：No-Go（仅 L0 演示层）。**  
下一步关键路径：修 9 失败（T1.1–T1.8）∥ 安全底线（T1.10–T1.16）∥ 起 MCP + 改 `MONITOR_TARGET_MODE`（T1.17–T1.19）。

---

## 6. 前置决策建议（供确认）

| ID | 建议默认 | 理由 |
|---|---|---|
| D1 数据面 | **方案 A**（与当前 `.env` `HARNESS_MCP_ENABLED=true` 一致） | 需先把 monitor/cls 进程拉起 |
| D2 变更 | **明确标注不可用** | change 仍为骨架，`CHANGE_SOURCE_AVAILABLE` 类能力未接 |
| D3 交付档 | **C**（完整 Go/No-Go），本迭代先完成 **A+B 主干** | 资产已起，P0 为阻塞 |
| D4 Conditional | 允许「仅知识+告警 / 白名单服务」作过渡 | 变更与日志源可能短期不齐 |
| D5 联系人 | 待你指定 | 运维门禁 P0 |
