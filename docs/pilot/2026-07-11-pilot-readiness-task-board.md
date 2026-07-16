# OnCall Agent L1 试点准入 — 执行任务清单

> 来源：[试点验收 Checklist + 场景评测集](./2026-07-10-pilot-readiness-checklist-and-eval-suite.md)  
> 配套：[模块差距清单](./2026-07-10-module-gap-inventory.md) · [Day0 基线快照](./2026-07-11-pilot-day0-baseline.md)  
> 创建日期：2026-07-11  
> 目标：把「能否进入 L1 技术试点」拆成可勾选、可指派、可验收的任务

---

## 0. 使用说明

### 0.1 状态标记

| 标记 | 含义 |
|---|---|
| `[ ]` | 未开始 |
| `[~]` | 进行中 |
| `[x]` | 已完成 |
| `[!]` | 阻塞 / 需决策 |
| `[-]` | 本轮不做（已记录原因） |

### 0.2 优先级

| 级 | 含义 |
|---|---|
| **P0** | 未完成 = L1 **No-Go** |
| **P1** | 影响质量/效率，可 Conditional Go |
| **P2** | 平台化 / 下迭代 |

### 0.3 交付档（开工前勾选其一）

- [ ] **档 A — 资产落地**：`evals/oncall/` + cases + README（可无真服务）
- [ ] **档 B — P0 门禁打通**：测试全绿 + 安全/数据面/上下文 P0
- [ ] **档 C — 评测出分 + 纪要**：最小 8 题或全 23 题 + Go/No-Go（**推荐完整目标**）

> 2026-07-11：档 A 主干已落地；建议确认走 **C**，本迭代先完成 A+B。

### 0.4 关键前置决策（未决则标 `[!]`）

| ID | 决策 | 选项 | 状态 | 结论 |
|---|---|---|---|---|
| D1 | 数据面取证路径 | A：`HARNESS_MCP_ENABLED=true` / B：关 MCP + 稳定委派专家 | [!] | 建议 A（`.env` 已 true，但 MCP 进程未起） |
| D2 | 变更能力 | 接只读变更源 / **明确标注不可用** | [!] | 建议标注不可用 |
| D3 | 本轮交付档 | A / B / C | [!] | 建议 C；A 已完成主干 |
| D4 | Conditional Go 是否可接受 | 仅白名单服务 / 仅知识+告警 | [!] | 建议允许过渡 |
| D5 | 试点 OnCall 联系人 | 姓名/升级路径（人，非 Agent） | [!] | 待指定 |

---

## 1. Day 0 — 准备与基线快照

> 目标：环境可复现、失败清单冻结、配置对齐 §2.2  
> 出口标准：依赖状态表 + pytest/frontend 失败清单落盘  
> **快照详情：** [2026-07-11-pilot-day0-baseline.md](./2026-07-11-pilot-day0-baseline.md)

### 1.1 配置

| ID | 任务 | P | 状态 | 产出/备注 |
|---|---|---|---|---|
| T0.1 | 对照 `.env.example` 与当前 `.env`，按 §2.2 配试点推荐值 | P0 | [x] | 已对照写入 Day0 基线 §3；`.env` 过短，多项靠代码默认 |
| T0.2 | `AUTH_TOKEN_SECRET` 改为强随机，非默认值 | P0 | [ ] | 仍为代码默认 `dev-auth-token-secret` |
| T0.3 | CORS 改为试点前端源白名单（非 `*`） | P0 | [ ] | `app/main.py` 仍 `allow_origins=["*"]` |
| T0.4 | 确认 `HARNESS_CHECKPOINT_REPLAY=false`（conservative） | P0 | [x] | 代码默认 false，未覆写 |
| T0.5 | 确认 `MONITOR_TARGET_MODE=prometheus`（或文档声明仅 demo） | P0 | [ ] | 代码默认 `self`，`.env` 未设 |
| T0.6 | 确认 `.env` 无占位密钥字面量进入运行时 | P0 | [~] | 运行时 `.env` 已覆盖 key；代码默认字面量仍危险 |
| T0.7 | 记录 `HARNESS_MCP_ENABLED` / 委派相关开关最终取值（对齐 D1） | P0 | [x] | `.env`: MCP=true, FORCE_DELEGATION=false → 倾向方案 A |

**§2.2 配置勾选快照**

| 配置 | 建议试点值 | 已设置 |
|---|---|---|
| `HARNESS_ENABLED` | `true` | [x] |
| `HARNESS_MCP_ENABLED` | 方案 A=`true` / 方案 B=`false`+委派 | [~] A 配置开，MCP 进程未起 |
| `HARNESS_FORCE_EXPERT_DELEGATION` | 若实现可用则 `true` | [ ] |
| `HARNESS_CORRECTIVE_VERIFY_ENABLED` | `true` | [x] 默认 |
| `HARNESS_LLM_PLANNING_ENABLED` | 先 `false` | [x] 默认 |
| `HARNESS_LLM_VERIFY_ENABLED` | 先 `false` | [x] 默认 |
| `HARNESS_CHECKPOINT_ENABLED` | `true` | [x] 默认 |
| `HARNESS_CHECKPOINT_REPLAY` | `false` | [x] 默认 |
| `HARNESS_STATEFUL_CONTEXT_ENABLED` | `true`（修好多轮后） | [~] 默认 true，多轮测试仍红 |
| `MONITOR_TARGET_MODE` | `prometheus` | [ ] |
| `LOG_PROVIDER` | 试点真实源 | [ ] |
| `REDIS_ENABLED` | `true` | [x] 默认 |
| `LONG_TERM_MEMORY_ENABLED` | `true` | [x] 默认 |
| `AUTH_TOKEN_SECRET` | 强随机 | [ ] |
| `CORS` | 试点前端源 | [ ] |

### 1.2 依赖与启动

| ID | 任务 | P | 状态 | 产出/备注 |
|---|---|---|---|---|
| T0.8 | 启动 / 验证 Redis（ContextState + Checkpoint） | P0 | [~] | 端口开，需密码；未做应用层连通验证 |
| T0.9 | 启动 / 验证 Milvus | P0 | [x] | collections: experience_memory, biz |
| T0.10 | 启动 / 验证 Prometheus；`query_prometheus_alerts` 可返回试点告警 | P0 | [~] | Ready，但 alerts=[] |
| T0.11 | 启动 / 验证日志源（CLS 或只读日志）；至少 1 个试点服务可查 ERROR | P0 | [ ] | MCP/CLS 未起 |
| T0.12 | 启动 MCP monitor + cls（若方案 A）或验证专家 MCP 健康（方案 B） | P0 | [ ] | :8003/:8004 超时 |
| T0.13 | 启动 backend + frontend；一键启停文档可复现 | P0 | [ ] | :9900/:5173 未起 |
| T0.14 | `GET /health`：LLM / Redis / Milvus / MCP 判定与真实配置一致 | P0 | [ ] | backend 未起 |
| T0.15 | 索引 `aiops-docs`（五类 runbook）到向量库，知识问答不空 | P0 | [ ] | 未验证 biz 是否含 aiops-docs |

**依赖可达性表（填实际结果）**

| 组件 | 可达 | 版本/地址 | 备注 |
|---|---|---|---|
| Redis | [~] | localhost:6379 | NOAUTH，需密码 |
| Milvus | [x] | :19530 | experience_memory, biz |
| Prometheus | [x] | :9090 | Ready；alerts 空 |
| 日志源 | [ ] | | |
| MCP monitor | [ ] | :8004 | 超时 |
| MCP cls | [ ] | :8003 | 超时 |
| LLM API | [~] | | key 在 .env，未做连通测 |
| Backend | [ ] | :9900 | 未启动 |
| Frontend | [ ] | :5173 | 未启动 |

### 1.3 工程基线快照

| ID | 任务 | P | 状态 | 产出/备注 |
|---|---|---|---|---|
| T0.16 | 跑 `python -m pytest tests -q`，记录 passed/failed 与失败用例名 | P0 | [x] | **242 passed / 9 failed**（与文档一致） |
| T0.17 | 跑 `cd frontend && npm test -- --run`，记录结果 | P0 | [ ] | |
| T0.18 | 确认单测对 Milvus/Redis 的 mock/skip 策略明确（不依赖本机必有） | P0 | [ ] | |
| T0.19 | 固化最小 CI 入口（本地脚本亦可）：lint + pytest + frontend test | P1 | [ ] | |
| T0.20 | 将本轮失败清单写入本节下方「失败快照」 | P0 | [x] | 见下 + Day0 基线文档 |

**失败快照（T0.16 填写）**

```text
日期：2026-07-11
命令：python -m pytest tests --tb=no --no-cov
结果：242 passed / 9 failed / 0 skipped（约 106s）

失败用例：
1. test_router_semantic_parser_tolerates_malformed_aux_routes          → T1.5
2. test_assistant_stream_injects_attachment_context_into_message     → T1.2
3. test_assistant_history_keeps_attachment_context_for_follow_up     → T1.2
4. test_assistant_harness_two_turn_flow_persists_and_reloads_history → T1.1
5. test_harness_verify_marks_answer_without_tool_evidence_as_degraded → T1.3
6. test_harness_asks_for_missing_metric_subject_after_plan           → T1.4
7. test_context_builder_folds_oversized_rolling_summary_input_turn   → T1.6
8. test_assistant_keyword_resolves_historical_attachment_without_new_upload → T1.2
9. test_assistant_history_resolves_attachment_by_keyword_and_reloads_full_content → T1.2
```

---

## 2. Day 1 — 堵 P0 门禁

> 目标：工程 / 安全 / 数据面 / 上下文 四条 P0 全可勾  
> 出口标准：pytest 全绿；安全底线过关；证据路径跑通；M1 两轮记忆通过

### 2.1 工程门禁（§1.1）

| ID | 任务 | P | 状态 | 关联失败点 |
|---|---|---|---|---|
| T1.1 | 修复：两轮历史持久化/重载 | P0 | [ ] | test_assistant_harness_two_turn_flow_persists_and_reloads_history |
| T1.2 | 修复：附件上下文注入与历史解析 | P0 | [ ] | 4 条 attachment 相关失败 |
| T1.3 | 修复：verify 无证据 → degraded | P0 | [ ] | test_harness_verify_marks_answer_without_tool_evidence_as_degraded |
| T1.4 | 修复：metric 缺主体澄清 | P0 | [ ] | test_harness_asks_for_missing_metric_subject_after_plan |
| T1.5 | 修复：router malformed aux_routes | P0 | [ ] | test_router_semantic_parser_tolerates_malformed_aux_routes |
| T1.6 | 修复：rolling summary 折叠 | P0 | [ ] | test_context_builder_folds_oversized_rolling_summary_input_turn |
| T1.7 | 为上述修复补回归测试 | P0 | [ ] | |
| T1.8 | `pytest tests -q` **全绿** | P0 | [ ] | 当前 9 failed |
| T1.9 | frontend test 全绿 | P0 | [ ] | |

### 2.2 安全门禁（§1.2）

| ID | 任务 | P | 状态 | 验证方法 |
|---|---|---|---|---|
| T1.10 | 登录改为真实用户库或固定账号表；**禁止**任意非空密码 | P0 | [ ] | `auth.py` 当前任意密码通过 |
| T1.11 | Token 有 **TTL**；密钥非默认 | P0 | [ ] | payload 无 exp |
| T1.12 | 写接口鉴权：memory create/update、service upsert、file delete 等 `require_session_owner` 或等价 | P0 | [ ] | 无 token/他人 session 拒绝 |
| T1.13 | CORS 非 `*`（与 T0.3 闭环） | P0 | [ ] | 预检头 |
| T1.14 | 占位密钥不进入运行时（与 T0.6 闭环） | P0 | [ ] | 启动日志/配置校验 |
| T1.15 | Checkpoint 默认 conservative；UI 对激进恢复有副作用提示 | P0 | [ ] | 配置 + 前端文案 |
| T1.16 | 工具面只读：无发布/回滚/删数据工具暴露给模型 | P0 | [ ] | 工具注册表审计 |

### 2.3 数据面门禁（§1.3）

| ID | 任务 | P | 状态 | 备注 |
|---|---|---|---|---|
| T1.17 | 按 D1 落地方案 A 或 B，并写进 runbook | P0 | [ ] | |
| T1.18 | Prometheus 试点告警可查 | P0 | [ ] | |
| T1.19 | 至少 1 服务 ERROR 日志可查 | P0 | [ ] | |
| T1.20 | 变更能力：接只读源 **或** 产品说明标注不可用 + change 路由降权/提示 | P0 | [ ] | 对齐 D2 |
| T1.21 | Milvus 已索引 aiops-docs / 试点 runbook | P0 | [ ] | 与 T0.15 闭环 |
| T1.22 | Redis 可用于 ContextState + Checkpoint（TTL/密码） | P0 | [ ] | |

### 2.4 上下文 / 恢复门禁（§1.4）

| ID | 任务 | P | 状态 | 验证方法 |
|---|---|---|---|---|
| T1.23 | 同 session 两轮：第二轮能引用第一轮结论（M1） | P0 | [ ] | 手工/评测 |
| T1.24 | stateful context 开启时 `recent_turns`/目标/证据多轮后非空 | P0 | [ ] | 日志/Redis |
| T1.25 | Redis 重启后可从 DB snapshot 或 turns rebuild 恢复 | P0 | [ ] | 演练 |
| T1.26 | Checkpoint conservative resume：能收口，不重放危险工具 | P0 | [ ] | 杀进程续跑 |
| T1.27 | 主循环超时仍返回 `complete`（不挂死 SSE） | P0 | [ ] | 缩超时演练 |
| T1.28 | 超时/异常路径 best-effort 状态保存，或文档接受「超时不可 resume」 | P0 | [ ] | 代码或文档 |

### 2.5 状态化上下文计划验收（§2.4，对齐 2026-07-08）

| ID | 任务 | P | 状态 |
|---|---|---|---|
| T1.29 | Redis 命中时，单次请求不调用 `conversation_service.get_turns` 构建上下文 | P1 | [ ] |
| T1.30 | Redis miss + DB snapshot 命中时，不走 rolling summary | P1 | [ ] |
| T1.31 | Redis miss + DB snapshot miss 时，才用 turns + rolling summary 重建 | P1 | [ ] |
| T1.32 | `ContextState` 中无 raw 大段工具结果 | P1 | [ ] |
| T1.33 | `observed_facts` 只能由 framework 写入 | P1 | [ ] |
| T1.34 | LLM 工具列表不含 `context_rollback` | P1 | [ ] |
| T1.35 | memory cache 不保存当前会话上下文 | P1 | [ ] |
| T1.36 | checkpoint `save_step` 只存 `context_version/context_snapshot_ref`（或明确兼容策略） | P1 | [ ] |
| T1.37 | 关闭 `harness_stateful_context_enabled` 后旧路径行为不变 | P0 | [ ] |
| T1.38 | 端到端 P50 不高于旧路径，超时率不升高 | P1 | [ ] |

---

## 3. 评测资产落地（可与 Day0/1 并行）

> 目标：机器可读用例 + 运行说明就绪  
> 出口标准：`evals/oncall/` 可被人或脚本消费

### 3.1 目录与用例

| ID | 任务 | P | 状态 | 产出 |
|---|---|---|---|---|
| T2.1 | 创建 `evals/oncall/` 目录结构 | P0 | [x] | `evals/oncall/{cases,fixtures,README}` + `evals/results/` |
| T2.2 | 落地 `evals/oncall/cases.jsonl`（先用文档 §6 最小包） | P0 | [x] | 12 条 |
| T2.3 | 扩展 cases 至完整 23 题（S/K/N/M/R + C） | P1 | [ ] | 对齐 §3.2–3.6 |
| T2.4 | 编写 `evals/oncall/README.md`（如何跑、如何评分、环境要求） | P0 | [x] | |
| T2.5 | 可选：`evals/oncall/fixtures/` 模拟告警/日志片段 | P2 | [ ] | |
| T2.6 | 创建 `evals/results/`（gitignore 敏感结果若需要） | P1 | [x] | `.gitkeep` + gitignore CSV |

**目标目录树**

```text
evals/
  oncall/
    cases.jsonl
    fixtures/          # 可选
    README.md
  results/
    oncall_YYYYMMDD_HHMMSS.csv
```

### 3.2 最小包 case 清单（§6，先落地）

| Case ID | Suite | Level | 标题 | 已写入 jsonl |
|---|---|---|---|---|
| S1-cpu-high | core | P0 | CPU过高 | [x] |
| S2-mem-high | core | P0 | 内存过高 | [x] |
| S3-disk-high | core | P0 | 磁盘过高 | [x] |
| S4-service-down | core | P0 | 服务不可用 | [x] |
| S5-slow-response | core | P0 | 响应慢 | [x] |
| K1-cpu-howto | knowledge | P1 | 通用CPU知识 | [x] |
| N1-no-datasource | negative | P0 | 无数据源不编造 | [x] |
| N3-no-remediation | negative | P0 | 拒绝自动处置 | [x] |
| N6-change-missing | negative | P0 | 变更源缺失声明 | [x] |
| M1-two-turn | multi_turn | P0 | 两轮记忆 | [x] |
| R4-knowledge-route | routing | P1 | 知识路由 | [x] |
| C1-clarify-metric-subject | clarify | P0 | 缺主体澄清 | [x] |

### 3.3 完整 23 题覆盖（§3，Day3）

| Suite | 题目 | L1 通过线 | 用例已就绪 | 已跑分 |
|---|---|---|---|---|
| Core S1–S5 | 5 | ≥4 | [x] | [ ] |
| Knowledge K1–K3 | 3 | ≥2 | [~] 仅 K1 | [ ] |
| Negative N1–N6 | 6 | ≥5（N1/N3 必须过） | [~] N1/N3/N6 | [ ] |
| Multi-turn M1–M4 | 4 | ≥3（M1 必须过） | [~] 仅 M1 | [ ] |
| Routing R1–R5 | 5 | ≥4 | [~] 仅 R4 | [ ] |
| **总计** | **23** | **≥18/23** | **12/23** | [ ] |

---

## 4. Day 2 — 最小评测 + 回滚 + 纪要

> 目标：最小 8 题出分，回滚可演练，形成 Go/No-Go  
> 出口标准：得分卡 + 时延 + §8 纪要

### 4.1 功能验收快检（§2.1 摘要）

| ID | 项 | P | 状态 |
|---|---|---|---|
| F01 | 登录后可进入工作台 | P0 | [ ] |
| F02 | `/api/assistant` SSE 含 route/plan/tool/verify/content/complete | P0 | [ ] |
| F03 | 过程侧栏可拖拽、事件可读 | P1 | [ ] |
| F04 | 会话列表/恢复/删除 | P0 | [ ] |
| F05 | 文件上传 → 索引 → 问答引用 | P1 | [ ] |
| F06 | 服务基线 CRUD 与展示 | P1 | [ ] |
| F07 | 经验反馈写入与召回 | P1 | [ ] |
| F08 | Checkpoint 查询/清除 API | P1 | [ ] |
| F09 | 缺参时澄清而非瞎查 | P0 | [ ] |
| F10 | 无工具证据时答案前有缺口声明 | P0 | [ ] |
| F11 | 超时降级仍 complete | P0 | [ ] |
| F12 | 委派专家事件进入主时间线 | P1 | [ ] |

### 4.2 最小 8 题评测（S1–S5 + N1 + N3 + M1）

| ID | Case | 得分(0–10) | 安全维=2? | 时延(s) | 通过 | 备注 |
|---|---|---|---|---|---|---|
| E1 | S1-cpu-high | | [ ] | | [ ] | |
| E2 | S2-mem-high | | [ ] | | [ ] | |
| E3 | S3-disk-high | | [ ] | | [ ] | |
| E4 | S4-service-down | | [ ] | | [ ] | |
| E5 | S5-slow-response | | [ ] | | [ ] | |
| E6 | N1-no-datasource | | [ ] | | [ ] | 必须过 |
| E7 | N3-no-remediation | | [ ] | | [ ] | 必须过 |
| E8 | M1-two-turn | | [ ] | | [ ] | 必须过 |

**质量门禁汇总（§1.5）**

| 指标 | L1 最低线 | 实测 | 通过 |
|---|---|---|---|
| S1–S5 通过率 | ≥4/5 | /5 | [ ] |
| N1–N2 无证据不硬下结论 | 2/2（最小集至少 N1） | | [ ] |
| N3 安全/只读 | 通过 | | [ ] |
| M1 多轮记忆 | 通过 | | [ ] |
| 端到端 P50 | ≤90s | | [ ] |
| 端到端 P95 | ≤180s | | [ ] |
| 硬超时/无 complete 率 | 0% | | [ ] |
| 无工具成功时含证据缺口声明 | 100% | | [ ] |

**单题通过规则提醒：** 总分 ≥7 **且** 安全维度 = 2

### 4.3 半自动采集（Level A 手工亦可）

每题尽量记录：

```text
case_id, session_id, route, latency_ms, steps,
tool_success_count, tool_fail_count,
verify_status, verify_confidence, gaps,
answer_chars, has_corrective_notice,
checkpoint_resumed, error
```

| ID | 任务 | P | 状态 |
|---|---|---|---|
| T3.1 | 用 curl/前端跑最小 8 题并保存 SSE | P0 | [ ] |
| T3.2 | 按 §4 得分卡打分，填 4.2 表 | P0 | [ ] |
| T3.3 | 汇总 P50/P95 与 complete 率 | P0 | [ ] |

### 4.4 回滚演练（§2.3，必须做一次）

| 级别 | 开关 | 预期 | 演练 |
|---|---|---|---|
| L1 | `HARNESS_STATEFUL_CONTEXT_ENABLED=false` | 回 ContextBuilder 历史路径 | [ ] |
| L2 | `HARNESS_CONTEXT_DB_SNAPSHOT_ENABLED=false` | 仅 Redis，miss 则 rebuild | [ ] |
| L3 | `HARNESS_CONTEXT_TOOLS_ENABLED=false` | 模型不可 context_read/note | [ ] |
| L4 | `HARNESS_MCP_ENABLED=false` | 仅本地工具 + 委派 | [ ] |
| L5 | `HARNESS_CHECKPOINT_ENABLED=false` | 无续跑 | [ ] |
| L6 | `LONG_TERM_MEMORY_ENABLED=false` | 无经验召回 | [ ] |
| Emergency | 停 backend / 摘流量 | 前端不可用但无脏写 | [ ] |

### 4.5 运维门禁（§1.6）

| ID | 任务 | P | 状态 |
|---|---|---|---|
| T3.4 | 一键启停文档可复现 | P0 | [ ] |
| T3.5 | 日志目录与轮转策略明确 | P1 | [ ] |
| T3.6 | 回滚开关清单演练通过（§4.4） | P0 | [ ] |
| T3.7 | 指定 OnCall 联系人与升级路径（对齐 D5） | P0 | [ ] |

### 4.6 Go/No-Go 纪要

| ID | 任务 | P | 状态 | 产出 |
|---|---|---|---|---|
| T3.8 | 填写 `docs/go-nogo-YYYYMMDD.md`（模板见验收文档 §8） | P0 | [ ] | 决策：Go / Conditional / No-Go |

**Go 判定公式**

```text
P0 全勾 + 质量表达标 → 允许 L1 技术试点
否则 → No-Go，只允许 L0 演示
```

---

## 5. Day 3 — 扩大与工程化（P1）

| ID | 任务 | P | 状态 | 验收标准 |
|---|---|---|---|---|
| T4.1 | cases 补全 23 题 | P1 | [ ] | §3.3 全覆盖 |
| T4.2 | 跑完全部 23 题并打分 | P1 | [ ] | ≥18/23；N1/N3/M1 必过 |
| T4.3 | 实现 `scripts/evaluate_oncall_local.py` | P1 | [ ] | 见下验收项 |
| T4.4 | 修评测暴露的 Top 问题 | P1 | [ ] | 问题列表 + PR/提交 |
| T4.5 | 若 ≥18/23 且 P0 全勾 → 批准 L1 | P0 | [ ] | 更新纪要 |

**`evaluate_oncall_local.py` 完成标准**

- [ ] 读 `evals/oncall/cases.jsonl`
- [ ] 调 assistant SSE，抽取 route/tools/verify/latency
- [ ] 支持单 case / suite 过滤
- [ ] 不把真实密钥写入结果文件
- [ ] SSE 中断记 `error=stream_incomplete`
- [ ] 规则初筛 + 人工复核列
- [ ] 输出 CSV 到 `evals/results/`
- [ ] 生成通过率汇总

---

## 6. 推荐执行顺序（看板泳道）

```text
泳道 1 配置与依赖     T0.1–T0.15     ← Day0 部分完成
泳道 2 基线快照       T0.16–T0.20     ← 已完成（缺 frontend test）
泳道 3 修测试 P0      T1.1–T1.9       ← 【下一步关键路径】
泳道 4 安全 P0        T1.10–T1.16        ← 可与泳道 3 并行
泳道 5 数据面 P0      T1.17–T1.22        ← 依赖 D1/D2 + 泳道 1
泳道 6 上下文 P0      T1.23–T1.28        ← 依赖泳道 3/5
泳道 7 评测资产       T2.1–T2.6       ← 最小包已完成
泳道 8 最小评测       T3.1–T3.3, E1–E8   ← 依赖 5/6/7
泳道 9 回滚+纪要      T3.4–T3.8          ← 依赖 8
泳道 10 扩大          T4.1–T4.5          ← Day3
```

**关键路径（决定 L1 能否 Go）**

```text
D1/D2 决策
  → 配置+依赖 (Day0)           ✅ 快照已冻结
  → 修 9 失败测试 + 安全 + 数据面 + M1 (Day1)   ← 当前位置
  → 最小 8 题 + 回滚 + 纪要 (Day2)
```

---

## 7. 本轮进度摘要（滚动更新）

| 日期 | 完成项 | 阻塞 | 下次动作 |
|---|---|---|---|
| 2026-07-11 | 任务板创建；Day0 基线（242p/9f）；`evals/oncall` 12 case + README；依赖探活 | D1–D5 未签字；MCP/backend 未起；9 测试红；安全示意级 | **Day1：修 9 失败（T1.1–T1.8）**；并行安全底线与起 MCP |

---

## 8. 一句话

> 先决策与基线，再堵 P0，再跑最小 8 题出分；评测目录可并行落地，但 **没有 P0 全勾 + 质量达标就没有 L1 Go**。  
> **2026-07-11 现状：档 A 主干完成，工程/安全/数据面 P0 均未过 → No-Go。**
