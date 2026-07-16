# Completion Review Report: 全项目审查与综合打分

- Review date: 2026-07-13
- Review scope: 全仓库（后端 Harness / 安全 / 数据面 / 评测 / 前端 / 文档与运维门禁）
- Source material:
  - `README.md`、`AGENTS.md`、`docs/README.md`
  - `docs/pilot/go-nogo-20260712.md`、`docs/pilot/handoff-2026-07-12-l1-pilot.md`
  - `docs/pilot/2026-07-10-pilot-readiness-checklist-and-eval-suite.md`
  - 主证据：`evals/results/oncall_minimal_20260712_171736.json`
  - H6：`logs/checkpoint_resume_drill_result.json`
  - 代码：`app/agent/harness/`、`app/agent/context/`、`app/api/*`、`app/services/auth_service.py`、`app/tools/change_tool.py`
- Verification commands（本轮实测）:
  - `python -m pytest tests --tb=line --no-cov` → **264 passed / 0 failed**（82.7s）
  - `cd frontend && npm test -- --run` → **56 passed / 9 files**
  - `GET http://127.0.0.1:9900/health` → healthy；Milvus connected；MCP cls/monitor reachable；`monitor.target_mode=prometheus`
  - Frontend `:5173` → HTTP 200
  - Prometheus `/-/ready` Ready；firing alerts=3（pilot seed）
  - 评测主证据 JSON 已存在：passed=8/8，p50=138.32，p95=158.19，complete_rate=1.0
  - Checkpoint H6 JSON：`passed=true`，`step_resume_evidence=true`，`checkpoint_step_after_restart=2`，resume≈20.7s
  - 本轮 **未重跑** 严格 8 题（沿用 2026-07-12 主证据；运行态服务当前在线）

## Overall Conclusion

**结论：L1 技术试点「有条件就绪 / 正式 Go 已开启」——综合评分 7.9 / 10。**

相对 07-11 的 No-Go，项目在工程、安全、数据面、质量与 checkpoint 上完成了闭环，并已在 07-13 由项目方勾选 **正式 L1 Go（H3 豁免）**。本轮复验显示：

| 维度 | 状态 |
|---|---|
| 工程单测 | ✅ 264 passed（优于 handoff 记录的 258） |
| 前端单测 | ✅ 56 passed（优于 handoff 的 52） |
| 运行态 | ✅ backend/frontend/MCP/Milvus/Prometheus 当前可用 |
| 质量门禁 H1 | ✅ 严格连续 8/8，无 `llm_provider_degraded` |
| 时延 H2 | ⚠️ P50=138s > 90s 目标，已书面接受 |
| 恢复 H6 | ✅ tool 后 kill → resumable step=2 |
| 真人 OnCall H3 | ⚠️ 项目方豁免，无固定值班兜底 |
| 生产无人值守 | ❌ 明确不允许 |

**不宜**把本评分理解为「可替换正式 OnCall / 可接写操作」。评分按 **L1 影子/辅助试点目标** 加权；若按 **L3 生产主路径** 计分，约 **5.5 / 10**。

---

## 综合打分卡（满分 10）

| 维度 | 权重 | 得分 | 说明 |
|---|---:|---:|---|
| 1. 架构与功能完整度 | 15% | **8.5** | FastAPI + Harness + 专家路由 + RAG + 记忆 + MCP + 前端控制台闭环清晰；变更源仍为骨架（已文档化） |
| 2. Agent / Harness 编排 | 15% | **8.5** | 统一主循环、规划/澄清/验证、状态化 context、checkpoint 续跑均落地；S3 偶发 knowledge 路由仍不稳 |
| 3. 安全与鉴权 | 12% | **7.5** | 固定账号 + TTL + 非默认 secret + CORS 白名单 + 写接口 owner；自研 token 非标准 JWT；GET memory 列表仍无鉴权；默认码仍写在 config 默认值 |
| 4. 数据面与集成 | 12% | **8.0** | Prom pilot 3 firing、MCP 可达、Milvus 在线、`MONITOR_TARGET_MODE=prometheus`；变更/CMDB 未接入 |
| 5. 评测与质量门禁 | 12% | **7.5** | 最小 8 题 8/8 + 脚本完善；完整 23 题仅 12 用例；P50 未达 90s；上游 LLM 503 历史复发 |
| 6. 前端 / 体验 | 8% | **8.0** | 工作台 + 过程侧栏 + 基线管理 + 56 测全绿；时延体感偏慢 |
| 7. 测试与工程卫生 | 10% | **8.5** | backend/frontend 全绿；pre-commit/ruff/pytest-cov 齐备；强制 CI 流水线未见；git worktree 元数据断链 |
| 8. 文档与运维可交接性 | 10% | **8.5** | pilot 交接/Go-NoGo/门禁表/拉起步骤质量高；H3 豁免风险写明 |
| 9. 生产就绪度 | 6% | **5.5** | 明确仅 L1 辅助；无真人兜底；无写操作；SLA 不可外宣 |
| **加权总分（L1 目标）** | 100% | **7.9** | **B+ / 良好，可试点，不可生产主路径** |

### 分档对照

| 分数段 | 含义 | 本项目 |
|---|---|---|
| 9.0–10 | 生产可主路径 | — |
| 8.0–8.9 | 试点稳、接近影子值班 | 部分维度已达 |
| **7.0–7.9** | **L1 技术试点可用，有已知豁免** | **← 当前** |
| 6.0–6.9 | 演示/局部能力 | 07-11 附近 |
| <6 | No-Go | 早期 |

---

## Findings

### P0 - 无真人 OnCall 兜底（已豁免，仍记风险）

- Evidence: `docs/pilot/go-nogo-20260712.md` H3/D5 两次跳过；正式 Go 签字栏「无真人姓名/升级路径」
- Impact: 故障升级无固定联系人；不可作为无人值守主路径
- Recommendation: 进入有人值班前补 H3 模板一行（姓名/联系/备份/升级路径/覆盖时段）
- Verification: go-nogo 签字栏 H3 由豁免改为已指定

### P1 - P50 显著高于 90s 目标线

- Evidence: `evals/results/oncall_minimal_20260712_171736.json` p50=**138.32s**，p95=158.19s；慢题 S2/S4/M1
- Impact: 值班体验慢；不可对外承诺 90s SLA
- Recommendation: 压 `HARNESS_MAX_STEPS`、减 tool 扇出、M1 第二轮限工具、校准 S3 路由
- Verification: 严格连续 8 题 p50≤90 或继续书面接受并限场景

### P1 - 完整评测套件仅 12/23

- Evidence: `evals/oncall/cases.jsonl` 共 12 行；清单要求 K/N/M/R 全量 23 题
- Impact: 扩大场景与负例覆盖不足，影子值班前证据偏窄
- Recommendation: 补齐 K2/K3、N2/N4/N5、M2–M4、R 系列并出分
- Verification: cases=23 且可加载跑通

### P1 - 上游 LLM 503 历史复发风险

- Evidence: 同日 `160315`/`165213` 曾因 `llm_provider_degraded` 掉到 7/8
- Impact: 连续评测/值班时段可能突然失败
- Recommendation: 值班前 LLM 探活；短题失败自动重试；备用端点
- Verification: 连续 8 题无 degraded；探活脚本 exit 0

### P2 - Memory 读接口无鉴权

- Evidence: `GET /memory/experiences`、`GET /memory/services` 等无 `require_session_owner`（写接口已有）
- Impact: 试点内可被未登录读取经验/服务知识（信息泄露面）
- Recommendation: 读接口至少要求已登录 principal；敏感项再加 owner/admin
- Verification: 无 token 访问 GET 返回 401

### P2 - S3 路由不稳定（knowledge vs diagnosis）

- Evidence: 主证据 S3-disk-high 路由=`knowledge` 仍得分 10；单题重试可为 diagnosis
- Impact: 诊断路径与证据工具链不一致，可解释性下降
- Recommendation: 路由关键词/语义分层再校准磁盘类告警
- Verification: 连续跑 S3 稳定落 diagnosis（或文档声明 knowledge 可接受）

### P2 - 变更能力骨架 + 生产默认值残留

- Evidence: `CHANGE_SOURCE_AVAILABLE = False`；`app/config.py` 默认 `auth_users=admin:admin`、`auth_token_secret=dev-...`（运行时 `.env` 已覆盖）
- Impact: 新人误用默认配置会退回弱安全；变更类问题无真实证据
- Recommendation: 启动时若仍默认 secret/弱账号直接 fail-fast（非 debug）；变更源接入或保持产品声明
- Verification: 空 `.env` 启动拒绝服务；有源后 CHANGE 标志 True

### P3 - 工程外围债

- Evidence: git worktree 元数据断链；README 提到缺 `LICENSE` 全文；PyMilvus ORM API deprecation warnings；强制远程 CI 未见
- Impact: 协作/合规/依赖升级成本
- Recommendation: 修复 git 链接；补 LICENSE；迁 `MilvusClient`；固化 `make check-all` 到 CI
- Verification: `git status` 可用；CI 绿

### P3 - 本轮未重跑 8 题

- Evidence: 沿用 07-12 `171736`；服务当前 healthy 但质量分未当日重测
- Impact: 若上游模型/索引漂移，运行质量可能偏离文档
- Recommendation: 重大变更或首次值班前按 handoff §4 抽测 2～8 题
- Verification: 新 JSON 汇总 passed≥7/8

---

## Completion Matrix

| Item | Status | Notes |
| --- | --- | --- |
| L1 正式 Go 决策 | Complete | 2026-07-13；H3 豁免 |
| 工程：pytest 全绿 | Complete | **264 passed**（本轮） |
| 工程：frontend test 全绿 | Complete | **56 passed**（本轮） |
| 安全：固定账号登录 | Complete | AUTH_USERS 2 账号；错误密码拒绝 |
| 安全：token TTL + 非默认 secret | Complete | TTL=86400；secret_is_default=False |
| 安全：CORS 白名单 | Complete | localhost:5173 only |
| 安全：写接口 owner | Complete | memory/file/conversations/checkpoint/assistant |
| 安全：读接口鉴权 | Partial | experiences/services GET 无 owner |
| 安全：工具只读 | Complete | 无处置工具；change 占位 |
| 数据面：Prometheus pilot | Complete | 3 firing seed |
| 数据面：MCP cls/monitor | Complete | health reachable |
| 数据面：Milvus/RAG | Complete | collection `biz` available |
| 数据面：MONITOR_TARGET_MODE | Complete | prometheus |
| 数据面：变更源 | Partial | 明确不可用（D2） |
| 上下文：状态化 context | Complete | `app/agent/context/*` 已落地 |
| 上下文：M1 两轮 | Complete | 评测 M1 pass score=10 |
| Checkpoint conservative | Complete | replay 默认 false |
| Checkpoint H6 step resume | Complete | step=2 resumable |
| 质量：最小 8 题 | Complete | 8/8，complete=100% |
| 质量：P50≤90s | Waived | 138s 书面接受 |
| 质量：23 题套件 | Partial | 用例 12/23 |
| 运维：一键拉起/探活 | Complete | 本轮服务在线 |
| 运维：回滚 L4 MCP | Complete | 文档已演练 |
| 运维：真人 OnCall | Waived | 项目方豁免 |
| 产品：Harness 主路径 | Complete | harness_enabled=true |
| 产品：前端控制台 | Complete | 工作台 + 基线 + SSE |
| 生产主路径就绪 | Missing | 明确不允许 |
| 文档交接 | Complete | handoff + go-nogo + reviews |

---

## 架构速览（审查所见）

```text
React (Vite:5173) ──SSE──▶ FastAPI (9900)
                              ├─ auth / conversations / files / memory / checkpoint
                              ├─ assistant → HarnessService 主循环
                              │     ├─ Router / Planner / Clarifier / Verifier
                              │     ├─ Experts: knowledge/metric/log/change/diagnosis
                              │     ├─ ContextState (Redis + snapshot)
                              │     └─ Checkpoint store
                              ├─ SQLite 会话/记忆
                              ├─ Milvus 向量 (biz / experience_memory)
                              └─ MCP: CLS(:8003) / Monitor(:8004) → Prometheus(:9090)
```

代码体量印象：Harness `loop.py` ~1630 行；router ~500 行；后端测试 23 个文件；前端组件 7 + 9 个测试文件。整体是「可运行的中型 Agent 平台」，而非 demo 脚本。

---

## Test And Verification Notes

| 命令 / 检查 | 结果 |
| --- | --- |
| `python -m pytest tests --tb=line --no-cov` | **264 passed**, 6 warnings, 82.71s, exit 0 |
| `cd frontend && npm test -- --run` | **56 passed / 9 files**, 4.42s |
| `GET /health` | healthy；Milvus connected；MCP reachable；LLM configured；monitor=prometheus |
| Frontend `:5173` | 200 |
| Prometheus alerts | 3 pilot firing |
| Eval `171736` | passed 8/8；core 5/5；must N1/N3/M1 true；p50 138.32；p95 158.19；complete 1.0 |
| Checkpoint drill | passed；after_tool；step=2；resume 20.68s |
| Runtime config 抽样 | users=2；ttl=86400；cors 白名单；secret 非默认；checkpoint_replay=false |

相对 handoff（258/52）：本轮单测数量上升，说明近期仍有测试增量且保持全绿。

---

## Next Steps

1. **（可选但建议）** 补 H3 真人值班信息，去掉「无兜底」风险标签。  
2. **H2b**：压 S2/S4/M1 时延，争取 P50 回到 90s 附近。  
3. **H7**：补全 23 题用例并出分。  
4. **安全收口**：memory GET 加登录；默认弱账号/默认 secret 在非 debug fail-fast。  
5. **路由**：稳定 S3→diagnosis。  
6. **工程**：修 git worktree、补 CI、处理 PyMilvus deprecation。  
7. 首次值班前按 handoff 拉起并抽测 1～2 题，确认无 LLM 503。

---

## One-liner

> **综合 7.9/10（L1 良好）**：工程/安全/数据面/最小质量/H6 已闭环，正式 L1 Go 成立；扣分主因是 P50 偏高、23 题未齐、H3 豁免与明确非生产主路径。
