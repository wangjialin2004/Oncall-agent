# OnCall Agent 模块差距清单（文件 / 开关对照）

> 审查日期：2026-07-10  
> 范围：`super_biz_agent_py` 当前工作树  
> 目的：把“系统到了什么程度”落到**每个模块 / 每个开关 / 每个文件**的可执行差距清单  
> 配套文档：[试点验收 Checklist + 场景评测集](./2026-07-10-pilot-readiness-checklist-and-eval-suite.md)

## 0. 读法

| 字段 | 含义 |
|---|---|
| 状态 | `已落地` / `部分落地` / `骨架` / `缺失` / `漂移` |
| 默认 | 当前 `app/config.py` 默认值（可被 `.env` 覆盖） |
| 差距 | 距离“值班试点可用 / 生产可用”还差什么 |
| P | `P0` 试点阻断 · `P1` 试点质量 · `P2` 平台化 |

状态口径：

- **已落地**：主路径可运行，有代码 + 基本测试/文档
- **部分落地**：主逻辑在，但默认关闭、仅观测、依赖未接通、或有已知空洞
- **骨架**：接口/专家/UI 存在，真实数据源或行为未实现
- **缺失**：产品目标需要，但仓库无实质实现
- **漂移**：代码/配置/注释/测试三者不一致

---

## 1. 总览矩阵

| 子系统 | 状态 | 成熟度 | 关键文件 | 关键开关 |
|---|---|---|---|---|
| HTTP 入口 / SSE | 已落地 | 高 | `app/api/assistant.py`, `app/main.py` | — |
| Harness 主循环 | 部分落地 | 中高 | `app/agent/harness/loop.py` | `HARNESS_*` |
| 路由 | 部分落地 | 中 | `app/services/router_service.py` | `ROUTER_*` |
| 规划 / 澄清 / 自检 | 部分落地 | 中 | `planner.py` / `clarifier.py` / `verifier.py` | `HARNESS_LLM_*` / `CORRECTIVE_*` |
| 专家体系 | 部分落地 | 中 | `app/agent/experts/*` | `EXPERT_TIMEOUT` / MCP |
| 本地工具 | 部分落地 | 中 | `app/tools/*` | Prometheus / Redis |
| MCP 数据面 | 部分落地 | 中低 | `mcp_servers/*`, `mcp_client.py` | `HARNESS_MCP_ENABLED`, `MONITOR_TARGET_MODE` |
| 状态化上下文 | 部分落地 | 中高（集成有洞） | `app/agent/context/*` | `HARNESS_STATEFUL_CONTEXT_*` |
| Checkpoint | 部分落地 | 中 | `harness_checkpoint.py` | `HARNESS_CHECKPOINT_*` + Redis |
| 长期记忆 / 服务知识 | 部分落地 | 中高 | `experience_*`, `service_knowledge_*` | `LONG_TERM_MEMORY_*` |
| RAG | 部分落地 | 中 | `vector_*`, `document_*` | `RAG_*`, Milvus |
| 前端控制台 | 已落地 | 中高 | `frontend/src/*` | — |
| 认证 / 安全 | 骨架 | 低 | `auth.py`, `auth_service.py` | `AUTH_TOKEN_SECRET` |
| 评测 / CI / 部署 | 缺失/弱 | 低 | `scripts/*`, `Makefile` | — |
| 变更关联 | 骨架 | 低 | `change_tool.py`, `change.py` | `CHANGE_SOURCE_AVAILABLE` |

---

## 2. 编排内核（Harness）

### 2.1 主循环

| 项 | 内容 |
|---|---|
| 文件 | [`app/agent/harness/loop.py`](../app/agent/harness/loop.py)（~1612 行）、[`state.py`](../app/agent/harness/state.py)、[`agent_loop.py`](../app/agent/agent_loop.py) |
| 状态 | **部分落地** |
| 已有 | route → context → plan → N-step tool loop → verify → complete；总超时 / 单步超时 / token 预算 / message 压缩 / 无进展检测 / 多级 fallback；SSE 事件齐全 |
| 差距 | ① plan 只在开头跑一次，不 mid-loop replan；② verify 只做缺口前缀，不 re-evidence；③ 外层 timeout 路径不 best-effort 写 checkpoint；④ 专家与 harness 双循环 |
| P | P0（闭环与稳定性）、P1（规划/自检深度） |

**关键开关**

| 开关 | 默认 | 作用 | 差距 |
|---|---|---|---|
| `HARNESS_ENABLED` | `True` | 历史含义是“是否走 harness” | **漂移**：`/api/assistant` 现已**固定**绑 `harness_service`，该开关主要影响 checkpoint 激活等，不再是 HTTP 入口双路径开关；注释仍写“默认关闭，旧 RouterService 可回滚” |
| `HARNESS_MAX_STEPS` | `6` | 最大 tool 轮次 | 复杂跨域排查可能不够 |
| `HARNESS_TOKEN_BUDGET` | `80000` | 累计 token 预算 | 与 `HARNESS_MESSAGE_TOKEN_BUDGET=60000` 双预算，需文档化 |
| `HARNESS_TIMEOUT_SECONDS` | `240` | 主循环总闸门 | 多委派 + 慢模型仍可能吃紧 |
| `HARNESS_STEP_TIMEOUT_SECONDS` | `60` | 单步 LLM 决策超时 | 超时直接收尾，不补取证 |
| `HARNESS_FALLBACK_TIMEOUT_SECONDS` | `30` | 降级路径超时 | 合理 |
| `HARNESS_NO_PROGRESS_LIMIT` | `2` | 重复工具签名提前停 | 已落地 |
| `HARNESS_TOOL_MAX_RETRIES` | `1` | 瞬时错误重试 | 已落地；鉴权类不重试 |
| `HARNESS_LOG_PIPELINE_ENABLED` | `True` | 大日志走 `analyze_logs` | 已落地 |
| `HARNESS_CORRECTIVE_VERIFY_ENABLED` | `True` | 低置信答案前插缺口声明 | **仅纠正展示，不重查** |
| `HARNESS_LLM_PLANNING_ENABLED` | `False` | LLM 规划 | 默认规则版 |
| `HARNESS_LLM_VERIFY_ENABLED` | `False` | LLM 自检 | 默认规则版 |
| `HARNESS_MCP_ENABLED` | `False` | harness 直连 MCP | **默认关** → metric/log/diagnosis 主路径本地工具为主 |
| `HARNESS_DELEGATION_ENABLED` | `True` | 注册 `delegate_to_expert` | 已落地 |
| `HARNESS_FORCE_EXPERT_DELEGATION` | `False` | 路由后强制先委派专家 | **漂移**：配置与注释存在，主循环未见强制 seed-delegation 实现（或未接入 happy path） |
| `HARNESS_DELEGATE_TIMEOUT_SECONDS` | `90` | 子专家超时 | 已落地 |
| `LLM_PLANNER_MODEL` / `LLM_REASONER_MODEL` | `""` / `gpt-5.4` | 模型分层 | planner 空则回落默认模型 |

### 2.2 规划 / 澄清 / 自检

| 模块 | 文件 | 状态 | 已有 | 差距 | P |
|---|---|---|---|---|---|
| Planner | `app/agent/harness/planner.py` | 部分落地 | 规则 todos + required_evidence + required_params；可 LLM 精炼 | 不约束后续 tool 选择；不驱动 replan | P1 |
| Clarifier | `app/agent/harness/clarifier.py` | 部分落地 | 缺参追问；无工具证据时再检 | 启发式（IP/标识符/key=value）易误判；非结构化多轮填槽 | P1 |
| Verifier | `app/agent/harness/verifier.py` | 部分落地 | 按成功/失败 tool_event 定 status/confidence/gaps | 不匹配 required_evidence 细项；不触发补取证 | P0/P1 |

### 2.3 工具注册 / 委派

| 模块 | 文件 | 状态 | 已有 | 差距 | P |
|---|---|---|---|---|---|
| Registry | `app/agent/harness/registry.py` | 部分落地 | 按 route 选本地工具；MCP 可选；delegate 工具；context tools | `HARNESS_MCP_ENABLED=False` 时 strip MCP | P0 |
| Subagent | `app/agent/harness/subagent.py` | 部分落地 | 串行 `expert.run` + 超时 + 嵌套事件 | 仍调旧专家循环，非共享 harness 内核；无并行 fan-out | P1 |
| GuardedToolExecutor | `app/agent/agent_loop.py` | 已落地 | 超时/截断/allowlist/重试 | — | — |

**Route → 工具面**

| route | 本地工具包 | MCP（仅 harness_mcp=True） |
|---|---|---|
| knowledge | `KNOWLEDGE_LOCAL_TOOLS` | 无 |
| metric | `METRIC_LOCAL_TOOLS` | `monitor` |
| log | `LOG_LOCAL_TOOLS` | `cls` |
| change | `CHANGE_LOCAL_TOOLS` | 无 |
| diagnosis（默认） | `DIAGNOSIS_LOCAL_TOOLS` | `monitor` + `cls` |

---

## 3. 专家体系

| 专家 | 文件 | 状态 | 工具 | 差距 | P |
|---|---|---|---|---|---|
| 共享内核 | `experts/base.py` | 已落地 | 3 轮 tool loop + GuardedToolExecutor | 与 harness 双实现 | P1 |
| knowledge | `knowledge.py` | 已落地 | retrieve_knowledge / recall_experience / time | 依赖 Milvus | P1 |
| metric | `metric.py` | 部分落地 | alerts +（MCP）monitor + service knowledge | harness 默认不带 MCP；深度指标依赖 provider 模式 | P0 |
| log | `log.py` + `log_pipeline.py` | 部分落地 | CLS MCP + 聚类/Map-Reduce | 默认 harness 不直连 CLS；需委派 | P0 |
| change | `change.py` + `change_tool.py` | **骨架** | `query_recent_changes` 占位 | `CHANGE_SOURCE_AVAILABLE=False`；明确无 CI/CD/CMDB | **P0** |
| diagnosis | `diagnosis.py` | 部分落地 | 宽工具集 + 日志预处理 | 受数据源与 MCP 默认限制 | P0 |
| registry | `experts/registry.py` | 已落地 | 5 路由 + DEFAULT=diagnosis | — | — |

---

## 4. 本地工具与 MCP 数据面

### 4.1 本地工具

| 工具 | 文件 | 状态 | 说明 | P |
|---|---|---|---|---|
| `retrieve_knowledge` | `knowledge_tool.py` | 已落地 | Milvus 检索 | P1（可用性依赖向量库） |
| `recall_experience` | `recall_experience.py` | 已落地 | 长期经验召回 | P1 |
| `lookup_service_knowledge` | `lookup_service_knowledge.py` | 已落地 | 服务知识/基线 | P1 |
| `query_prometheus_alerts` | `query_metrics_alerts.py` | 已落地 | 真实 `/api/v1/alerts` | P0（需 Prometheus） |
| `get_current_time` | `time_tool.py` | 已落地 | — | — |
| `check_redis_health` | `redis_health.py` | 已落地 | Redis 探活 | — |
| `query_recent_changes` | `change_tool.py` | **骨架** | 固定返回未接入 | **P0** |

### 4.2 MCP

| 服务 | 文件 | 默认 | 状态 | 差距 | P |
|---|---|---|---|---|---|
| CLS 日志 | `mcp_servers/cls_server.py` | `LOG_PROVIDER=local` | 部分落地 | 本地日志 provider，非生产 CLS 租户；harness 默认不直连 | P0 |
| Monitor | `mcp_servers/monitor_server.py` | `MONITOR_TARGET_MODE=self` | 部分落地 | 默认本机 psutil；`prometheus` 模式可选但非默认 | P0 |
| MCP Client | `app/agent/mcp_client.py` | URL localhost:8003/8004 | 部分落地 | 启动预热 best-effort；失败懒加载 | P1 |

**关键开关**

| 开关 | 默认 | 差距 |
|---|---|---|
| `HARNESS_MCP_ENABLED` | `False` | 试点若要真日志/指标，应评估打开或强制委派专家 |
| `MCP_CLS_URL` / `MCP_MONITOR_URL` | localhost | 部署需改 |
| `MONITOR_TARGET_MODE` | `self` | 生产应 `prometheus` |
| `LOG_PROVIDER` | `local` | 生产需真实日志源 |
| `PROMETHEUS_BASE_URL` | `http://127.0.0.1:9090` | 需可达 |

---

## 5. 路由

| 文件 | 状态 | 已有 | 差距 | P |
|---|---|---|---|---|
| `app/services/router_service.py` | 部分落地 | 强/弱关键词分级 + LLM 语义分类 + multilabel aux_routes + confidence 回退 diagnosis | aux_routes **只标注不执行**；语义解析对 malformed JSON 仍有测试失败 | P1 |
| 开关 `ROUTER_KEYWORD_TIERING_ENABLED` | `True` | 已落地 | — | — |
| 开关 `ROUTER_MULTILABEL_ENABLED` | `True` | 部分 | 多标签不驱动并行专家 | P1 |
| 开关 `ROUTER_MIN_CONFIDENCE` | `0.55` | 已落地 | — | — |

---

## 6. 状态化上下文（ContextState）

| 文件 | 职责 | 状态 | 差距 | P |
|---|---|---|---|---|
| `context/state.py` | 七段白板 schema | 已落地 | — | — |
| `context/operations.py` | framework/LLM 分权 patch | 已落地 | `observed_facts` firm-only 正确 | — |
| `context/store.py` | Redis 热 + DB 冷 + rebuild | 已落地 | fail-soft | — |
| `context/persistence.py` | 序列化 | 已落地 | schema_version 兼容需持续 | P1 |
| `context/views.py` | 渲染 + token budget | 已落地 | — | — |
| `context/tools.py` / `tools_evidence.py` | context_read/note + 证据摘要 | 已落地 | LLM 不可 rollback / 不可写 observed_facts | — |
| `context/integration.py` | harness 接入门面 | **部分落地** | happy path 对 `recent_turns` 刷新不足：`stamp_recent_turns` 主要在 cold rebuild；多轮白板可能空心化 | **P0** |
| `services/context_snapshot_service.py` | DB snapshot | 已落地 | — | — |

**开关**

| 开关 | 默认 | 说明 |
|---|---|---|
| `HARNESS_STATEFUL_CONTEXT_ENABLED` | `True` | 主路径已开 |
| `HARNESS_CONTEXT_REBUILD_FROM_TURNS_ENABLED` | `True` | Redis+DB miss 才 rebuild |
| `HARNESS_CONTEXT_DB_SNAPSHOT_ENABLED` | `True` | 冷备 |
| `HARNESS_CONTEXT_TOOLS_ENABLED` | `True` | 暴露 read/note |
| ~~`HARNESS_CONTEXT_LLM_PATCH_ENABLED`~~ | 已删除（从未接线） | 历史占位，2026-07-16 清理 |
| `HARNESS_CONTEXT_VIEW_TOKEN_BUDGET` | `4000` | 视图预算 |
| `HARNESS_CONTEXT_REDIS_TTL_SECONDS` | `86400` | 热状态 TTL |

**计划验收（`plan/2026-07-08-stateful-agent-context.md` §12）仍未全部勾选**，尤其：

- Redis 命中时不读 turns
- Redis+DB miss 才 rolling summary
- checkpoint 只存 ref
- P50/超时率对照

---

## 7. Checkpoint 断点续跑

| 文件 | 状态 | 已有 | 差距 | P |
|---|---|---|---|---|
| `app/services/harness_checkpoint.py` | 部分落地 | Redis step 快照、fail-soft、conservative/aggressive、幂等白名单 | ① 默认幂等白名单几乎只有 `delegate_to_expert` → 多数恢复变成 **close-only**；② 状态化路径 `persist_messages=False`，resume 可能丢 tool 对话；③ 写了 `context_version/ref` 但 loop **未 rehydrate ContextState**；④ 外层 timeout 不落盘 | **P0** |
| `app/api/checkpoint.py` | 已落地 | GET/DELETE | — | — |
| 前端 resume UI | 部分落地 | 激进恢复勾选 | 需产品说明副作用 | P1 |

**开关**

| 开关 | 默认 | 说明 |
|---|---|---|
| `HARNESS_CHECKPOINT_ENABLED` | `True` | 需同时 `harness_enabled` + `redis_enabled` |
| `HARNESS_CHECKPOINT_TTL_SECONDS` | `1800` | 30min |
| `HARNESS_CHECKPOINT_REPLAY` | `False` | 保守默认正确 |
| `REDIS_ENABLED` | `True` | checkpoint/context 共用 |
| `REDIS_URL` | 含默认密码示例 | 生产必须改 |

---

## 8. 长期记忆 / RAG / 存储

### 8.1 长期记忆

| 模块 | 文件 | 状态 | 差距 | P |
|---|---|---|---|---|
| 经验记忆 | `experience_memory_service.py` + index | 部分落地 | 创建/合并/召回/反馈齐全；自动 L2 学习与 PII 策略可加强 | P1 |
| 服务知识/基线 | `service_knowledge_service.py` | 部分落地 | CRUD + compare；`import_from_monitor_mcp` 依赖 MCP 模式 | P1 |
| 用户偏好 | `user_preference_service.py` | 已落地 | — | — |
| L1 cache | `memory_cache.py` | 已落地 | 与 ContextState 边界：不应缓存当前会话白板 | P1（静态约束） |
| Memory API | `app/api/memory.py` | **部分落地/安全弱** | 部分写接口 owner 鉴权不完整（审查指出 experience/service 写路径风险） | **P0** |

**开关**：`LONG_TERM_MEMORY_ENABLED=True`、`USER_PREFERENCES_ENABLED=True`、`MEMORY_CACHE_*`（`SERVICE_KNOWLEDGE_ENABLED` 已删除，工具始终注册）

### 8.2 RAG

| 模块 | 文件 | 状态 | 差距 | P |
|---|---|---|---|---|
| 向量检索 | `vector_search_service.py` 等 | 部分落地 | dense/bm25/hybrid 能力在；默认 `dense` | P1 |
| 文档切分/抽取 | `document_*` | 已落地 | — | — |
| 文件存储 | `file_storage_service.py` + S3 adapter | 部分落地 | 本地默认；对象存储可配 | P2 |
| 离线评测 | `scripts/evaluate_rag_local.py` | 部分落地 | **仅 RAG**，无端到端 Agent 评测 | **P0**（试点质量） |

**开关**：`RAG_TOP_K=3`、`RAG_RETRIEVAL_MODE=dense`、`MILVUS_*`、`CHUNK_*`

### 8.3 旧路径共存（复杂度债）

| 机制 | 新定位 | 状态 |
|---|---|---|
| `ContextBuilder` rolling summary / token window | 应降级为 rebuild only | 仍完整存在；stateful 开启后双路径并存 |
| `memory_cache` 会话上下文 | 不应参与 | 仍服务 LTM；需保证不进 ContextState 主链 |
| 注释 vs 默认值 | 多处“默认关闭”注释与 `True` 默认冲突 | **漂移** |

---

## 9. API / 前端 / 安全 / 运维

### 9.1 API 面

| 路由 | 文件 | 状态 | 差距 |
|---|---|---|---|
| `POST /api/assistant` | `assistant.py` | 已落地 | 固定 harness；附件上下文有测试失败 |
| conversations | `conversations.py` | 已落地 | — |
| files | `file.py` | 已落地 | — |
| memory | `memory.py` | 部分 | 写鉴权 |
| checkpoint | `checkpoint.py` | 已落地 | — |
| auth | `auth.py` | **骨架** | 非空用户名密码即可发 token |
| health | `health.py` | 部分 | LLM 检查仍偏 dashscope 字段，与通用 `LLM_*` 漂移 |

### 9.2 前端

| 组件 | 状态 | 说明 |
|---|---|---|
| ChatWorkspace / AgentProcessPanel | 已落地 | SSE 过程、可拖拽侧栏 |
| ServiceBaselineManager | 已落地 | 基线管理 |
| Login / Sidebar / 会话恢复 | 已落地 | — |
| 前端测试 | 部分 | Vitest 有组件测；非完整 E2E 门禁 |

### 9.3 安全

| 项 | 状态 | 证据 | P |
|---|---|---|---|
| 登录校验 | 骨架 | 任意非空密码可登录 | **P0** |
| Token | 骨架 | HMAC 有 iat，缺 TTL/吊销/刷新；默认 `dev-auth-token-secret` | **P0** |
| CORS | 弱 | `allow_origins=["*"]` | **P0** |
| 默认密钥 | 弱 | Redis/MinIO/Auth 示例密钥 | **P0** |
| LLM key 占位 | 漂移 | 默认字面量 `get.env('LLM_API_KEY')` 风险 | **P0** |
| 工具只读取向 | 较好 | planner 禁止处置动作；checkpoint 默认不激进重放 | — |
| 多租户 | 弱 | 基本靠 username scope | P1 |

### 9.4 部署 / 可观测 / CI

| 项 | 状态 | 差距 | P |
|---|---|---|---|
| Makefile / bat 启动 | 部分 | 本地 nohup 风格，非 K8s | P1 |
| Docker compose（Milvus/Prom） | 部分 | 有向量库/监控 compose | P1 |
| `/metrics` | 部分 | app 暴露；Agent 质量指标弱 | P1 |
| CI | **缺失** | 无强制 pytest+前端+SSE 冒烟流水线 | **P0** |
| 结构化 tracing | 弱 | 有 trace_id/span_id 事件，无统一 APM | P2 |
| 评测闭环 | 弱 | 仅 local RAG script | **P0** |

---

## 10. 测试现状（2026-07-10 实跑）

```text
python -m pytest tests -q
→ 242 passed, 9 failed
```

### 失败用例（主链路风险）

| 测试 | 风险含义 |
|---|---|
| `test_router_semantic_parser_tolerates_malformed_aux_routes` | 语义路由健壮性 |
| `test_assistant_stream_injects_attachment_context_into_message` | 附件上下文注入 |
| `test_assistant_history_keeps_attachment_context_for_follow_up` | 多轮附件 |
| `test_assistant_harness_two_turn_flow_persists_and_reloads_history` | **两轮历史**（出现 `pop from empty list` / timeout fallback） |
| `test_harness_verify_marks_answer_without_tool_evidence_as_degraded` | 自检降级契约 |
| `test_harness_asks_for_missing_metric_subject_after_plan` | 缺参澄清 |
| `test_context_builder_folds_oversized_rolling_summary_input_turn` | 滚动摘要折叠文案/行为 |
| `test_assistant_keyword_resolves_historical_attachment_without_new_upload` | 历史附件关键词解析 |
| `test_assistant_history_resolves_attachment_by_keyword_and_reloads_full_content` | 附件全文重载 |

> 部分失败日志伴随 **Milvus 未启动**（localhost:19530），说明测试对外部依赖隔离仍不足。

---

## 11. 配置漂移速查（必须修文档或代码）

| 位置 | 问题 |
|---|---|
| `config.py` 注释 “Harness 默认关闭” | 实际 `harness_enabled=True` |
| “旧 RouterService 可回滚” | `assistant.py` 已固定 harness |
| `HARNESS_FORCE_EXPERT_DELEGATION` | 文档有，主路径实现可疑/未生效 |
| `HARNESS_STATEFUL_CONTEXT` 注释 “Disabled by default” | 实际 `True` |
| health LLM 检查 | 仍偏 dashscope，与 `LLM_*` 主配置不一致 |
| `.env.example` 大量注释默认 | 与代码默认不完全同步 |

---

## 12. 按优先级的差距清单（执行板）

### P0 — 试点前阻断

1. **修 9 个失败测试**，并隔离外部依赖（Milvus/Redis）  
2. **安全底线**：真实登录、token TTL、写 API owner 鉴权、CORS、替换默认密钥、清理 LLM key 占位  
3. **接通数据面策略**（二选一写进 runbook）：  
   - A：`HARNESS_MCP_ENABLED=true` + 真实 CLS/Prom；或  
   - B：强制专家委派 + 专家侧 MCP 必达  
4. **变更源**：至少接一个只读发布/工单源，或产品声明“变更能力不可用”并路由降权  
5. **ContextState happy path**：每轮刷新 `recent_turns` / 答案摘要；checkpoint resume rehydrate  
6. **Checkpoint 可靠性**：timeout 落盘；扩大幂等工具白名单；状态化 resume 保留必要 messages  
7. **CI 门禁**：pytest + 关键 harness 集成 + 前端 test  
8. **场景评测集**：见配套文档，最低 5 类 aiops 场景可重复跑  

### P1 — 试点质量

9. verify 低置信 → 强制一轮补取证  
10. planner/required_evidence 细匹配  
11. 专家收敛共享 harness 内核，或正式接受双循环并写 ADR  
12. 落地或删除 `force_expert_delegation`  
13. aux_routes 执行策略（串行补充 or 明确只展示）  
14. health/配置/注释去漂移  
15. 时延/超时率基线（对照 logs 中 harness 历史）  

### P2 — 平台化

16. K8s/compose 生产部署清单  
17. APM + Agent 质量看板  
18. hybrid RAG 默认与 query rewrite  
19. 自动经验蒸馏 / 偏好抽取  
20. 多租户与审计  

---

## 13. 模块 → 负责人关注点（建议拆分）

| 模块 | 建议 Owner 焦点 |
|---|---|
| Harness loop | 闭环、超时、checkpoint、force-delegation |
| ContextState | 多轮刷新、resume rehydrate、验收 10 条 |
| Experts/Tools/MCP | 真实数据面、变更源、默认路径证据 |
| Memory/RAG | 鉴权、索引健康、评测 |
| Frontend | 过程可信展示、resume 安全提示 |
| Platform | Auth/CI/CORS/密钥/健康检查 |

---

## 14. 一句话结论

> **大脑（Harness + 状态白板 + 记忆/RAG + 控制台）已成型；手脚（真实日志/监控/变更）默认未握紧；体检（测试全绿 + 场景评测 + 安全）未过关。**  
> 模块差距不是“缺目录”，而是 **默认路径证据不足 + 集成空洞 + 安全/评测门禁缺失**。

下一步执行请直接使用：[`2026-07-10-pilot-readiness-checklist-and-eval-suite.md`](./2026-07-10-pilot-readiness-checklist-and-eval-suite.md)
