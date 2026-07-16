# Completion Review Report: OnCall Agent L1 试点准入完成情况

- Review date: 2026-07-11
- Review scope: L1 技术试点准入（工程 / 安全 / 数据面 / 上下文 / 评测 / 运维门禁），对照任务板与验收清单
- Source material:
  - [docs/pilot/2026-07-11-pilot-readiness-task-board.md](./2026-07-11-pilot-readiness-task-board.md)
  - [docs/pilot/2026-07-11-pilot-day0-baseline.md](./2026-07-11-pilot-day0-baseline.md)
  - [docs/pilot/2026-07-10-pilot-readiness-checklist-and-eval-suite.md](./2026-07-10-pilot-readiness-checklist-and-eval-suite.md)
  - [docs/pilot/2026-07-10-module-gap-inventory.md](./2026-07-10-module-gap-inventory.md)
  - README、`app/api/auth.py`、`app/services/auth_service.py`、`app/main.py`、`app/api/memory.py`、`app/tools/change_tool.py`、`evals/oncall/`
- Verification commands:
  - `python -m pytest tests --tb=no --no-cov` → **collection ERROR**（`tests/test_harness_service.py` IndentationError）
  - `python -m pytest tests --ignore=tests/test_harness_service.py --tb=no --no-cov -q` → **其余测试全绿**
  - `cd frontend && npm test -- --run` → **1 failed / 51 passed**（8 files 中 1 fail）
  - 依赖探活：Redis NOAUTH；Prometheus Ready + alerts=[]；Milvus collections=`experience_memory,biz`；MCP :8003/:8004 不可达；Backend :9900 / Frontend :5173 未起
  - 配置核对：`.env` 键名、`app/config.py` 默认值、CORS/Auth 源码

## Overall Conclusion

**结论：No-Go（仅 L0 演示层）。L1 技术试点准入未完成，且相对 2026-07-11 Day0 基线出现了工程门禁回退。**

产品大脑（Harness 主循环、状态化上下文、记忆/RAG、控制台）代码面大体成型，档 A「评测资产主干」已落地（`evals/oncall` 12 case + README）。但按验收文档「任一 P0 未勾 = No-Go」：

1. **工程门禁恶化**：Day0 为 242 passed / 9 failed；本次 `test_harness_service.py` 因 `IndentationError` **无法收集**，主链路 harness 测试整文件失效，不能再声称“9 条失败待修”，而是“主测文件损坏 + 失败项未修”。
2. **安全门禁未动**：任意非空密码登录、token 无 TTL、默认 `dev-auth-token-secret`、CORS `*`、多条 memory 写接口无 `require_session_owner`。
3. **数据面未闭环**：`.env` 开了 `HARNESS_MCP_ENABLED=true`，但 MCP/backend/frontend 未起；Prometheus 无告警；`MONITOR_TARGET_MODE` 仍默认 `self`；变更源仍为骨架（代码已返回“未接入”，产品说明/任务板 D2 未签字）。
4. **评测未出分**：最小 8 题未跑、无 Go/No-Go 纪要、`scripts/evaluate_oncall_local.py` 未实现；完整 23 题仅 12/23 用例就绪。

任务板勾选粗计：`[x]≈34` / `[ ]≈135` / `[~]≈12` / `[!]≈7`。交付档 C（评测出分 + 纪要）与 B（P0 门禁）均未完成；仅 A 主干可算完成。

## Findings

### P0 - 主链路测试文件语法损坏，pytest 无法收集

- Evidence: `tests/test_harness_service.py:4424-4425`  
  `async def test_expert_does_not_close_shared_llm_client():` 后缺少函数体缩进，`class _Expert` 顶格 → `IndentationError: expected an indented block`。  
  命令结果：`ERROR collecting tests/test_harness_service.py` / `Interrupted: 1 error during collection`。  
  同文件后半大量空行与编码乱码痕迹（如「鐭ヨ瘑涓撳」），疑似损坏/错误编辑后未校验。
- Impact: 工程门禁第一项失效；Day0 记录的 9 个失败用例**当前无法复现/修复验证**；主路径回归真空。
- Recommendation: 立即修复该文件语法（恢复函数体缩进；排查是否有整段格式被破坏）；修好后重跑全量 pytest，更新失败清单。优先用 git 历史恢复损坏段再 cherry-pick 有效改动。
- Verification: `python -m pytest tests/test_harness_service.py --collect-only` 成功；`python -m pytest tests -q` 可出完整 passed/failed 汇总。

### P0 - 安全底线仍为示意级（登录 / Token / CORS / 密钥）

- Evidence:
  - `app/api/auth.py`：仅校验非空用户名密码，任意账号即发 token。
  - `app/services/auth_service.py`：payload 仅 `sub`/`iat`，**无 `exp`、无 TTL 校验**。
  - `app/config.py`：`auth_token_secret: str = "dev-auth-token-secret"`；当前 `.env` **未设置** `AUTH_TOKEN_SECRET`。
  - `app/main.py:88`：`allow_origins=["*"]` 且 `allow_credentials=True`。
- Impact: 试点环境不可共享/不可对内开放；会话可伪造；跨域凭证风险。
- Recommendation: 固定账号表或用户库；token 加 TTL；`.env` 强随机 secret；CORS 白名单试点前端源。
- Verification: 错误密码 401；过期 token 401；预检 CORS 仅允许白名单源。

### P0 - 多条写接口缺少 owner 鉴权

- Evidence: `app/api/memory.py` 中带 `require_session_owner` 的主要是 feedback / preferences；以下写路径**无** owner 依赖：  
  `POST /memory/experiences`、`PATCH /memory/experiences/{id}`、`POST .../rebuild-index`、`PUT /memory/services/{name}`、`PUT .../baselines`、`DELETE .../baselines/{metric}`、`POST .../import-seed`。
- Impact: 未鉴权即可写入/改写经验与服务基线，污染知识面。
- Recommendation: 所有写接口统一 `Depends(require_session_owner)`（或 admin 角色）；补集成测试。
- Verification: 无 token / 他人 token 返回 401/403。

### P0 - 数据面方案 A 配置开、进程未起；监控默认仍 self

- Evidence:
  - `.env` 含 `HARNESS_MCP_ENABLED=true`；MCP :8003/:8004 探活超时。
  - Backend :9900、Frontend :5173 未监听。
  - Prometheus Ready 但 `alerts=[]`。
  - `app/config.py`：`monitor_target_mode` 默认 `"self"`，`.env` 未覆写为 `prometheus`。
  - Redis 端口开，`PING` → `NOAUTH Authentication required`（应用层带密连通未在本轮验证）。
  - Milvus 可达，collections：`experience_memory`, `biz`（aiops-docs 是否入索引仍未验证）。
- Impact: 无法对真实/准真实告警取证；评测与 F01–F02 手工验收无法进行。
- Recommendation: 按 D1 方案 A 启动 monitor/cls + backend/frontend；设 `MONITOR_TARGET_MODE=prometheus`；准备至少 1 条试点 firing 告警；验证 Redis URL 密码。
- Verification: `GET /health` 与真实依赖一致；`query_prometheus_alerts` 非空或明确无告警但仍成功；MCP 工具可调。

### P0 - Day1 主链路 9 项失败 + 上下文/附件/澄清/verify 未关闭

- Evidence: Day0 失败清单（T1.1–T1.6）仍在任务板为 `[ ]`；因测试文件损坏，本轮无法确认是否有局部修复。覆盖：两轮历史、附件上下文、verify degraded、metric 缺主体澄清、malformed aux_routes、rolling summary 折叠。
- Impact: M1 多轮记忆、F09/F10 证据缺口与澄清等 L1 质量门禁无法声称通过。
- Recommendation: 修复测试文件后按 T1.1–T1.8 逐项修绿；先修 T1.1/T1.3/T1.4（多轮/verify/澄清）。
- Verification: 对应 9 用例全绿 + 手工 M1。

### P0 - 前端测试未全绿；最小评测与 Go/No-Go 未做

- Evidence:
  - `npm test -- --run`：`App.test.tsx` 找不到「工具执行」文案，**1 failed / 51 passed**。
  - 任务板 E1–E8、T3.1–T3.8、`docs/go-nogo-*.md` 均未完成。
  - `scripts/evaluate_oncall_local.py` 不存在；`evals/results/` 仅 `.gitkeep`。
- Impact: 质量表无实测；无法按「P0 全勾 + 质量达标」判定 Go。
- Recommendation: 修前端断言或对齐 UI 文案；先手工跑最小 8 题填分；再写半自动脚本。
- Verification: frontend test 全绿；得分卡 + 时延表 + `docs/go-nogo-20260711.md`。

### P1 - 变更能力仍为骨架，前置决策 D1–D5 未签字

- Evidence: `app/tools/change_tool.py` `CHANGE_SOURCE_AVAILABLE = False`，返回结构化“暂未接入”；任务板 D1–D5 均为 `[!]`。
- Impact: S4/N6 可部分靠工具消息过关，但产品边界与试点范围未锁定，Conditional Go 也无法正式采用。
- Recommendation: D2 建议明确「变更不可用」写入产品说明与 runbook；其余决策签字后更新任务板。
- Verification: UI/README/runbook 有可见声明；change 路由不编造版本/操作人。

### P1 - 评测用例仅 12/23；CI / 一键启停 / OnCall 联系人未闭环

- Evidence: `evals/oncall/cases.jsonl` 12 行；T0.13/T0.19/T3.4/T3.7 未完成。
- Impact: Day3 扩大与运维门禁不足。
- Recommendation: 补 K2/K3、N2/N4/N5、M2–M4、R1–R3/R5；固化 lint+pytest+frontend 本地脚本；指定值班升级路径。
- Verification: 23 case 可加载；启停文档可复现；CI 脚本 exit 0。

### P2 - 配置/注释漂移与 harness 能力边界

- Evidence: 模块差距清单已列：`HARNESS_ENABLED` 注释与固定 harness 入口漂移；`HARNESS_FORCE_EXPERT_DELEGATION` 实现可疑；verify 只前缀缺口不 re-evidence；checkpoint resume 幂等白名单窄等。
- Impact: 运维误配、恢复行为不可预期。
- Recommendation: 配置注释与代码对齐；ADR 记录双循环/force-delegation 取舍。
- Verification: 配置表与运行行为一致；回滚开关演练记录。

## Completion Matrix

| Item | Status | Notes |
| --- | --- | --- |
| 档 A — 评测资产主干 | Complete | `evals/oncall/cases.jsonl` 12 条 + README + results 占位 |
| 档 B — P0 门禁打通 | Missing | 工程/安全/数据面/上下文均未过；测试文件更回退 |
| 档 C — 评测出分 + 纪要 | Missing | 未跑分、无 go-nogo 文档 |
| 工程：pytest 全绿 | Missing | 收集失败；非 harness 子集全绿 |
| 工程：frontend test 全绿 | Partial | 51 pass / 1 fail |
| 工程：最小 CI | Missing | 无强制流水线 |
| 安全：真实登录 | Missing | 任意非空密码 |
| 安全：token TTL + 非默认 secret | Missing | 无 exp；默认 secret |
| 安全：写接口鉴权 | Partial | feedback/preferences 有；experiences/services 写多无 |
| 安全：CORS 白名单 | Missing | `*` |
| 安全：工具只读 | Complete | change 占位且声明未接入；无处置工具 |
| 数据面：方案 A/B 落地 | Missing | 配置倾向 A，MCP/进程未起 |
| 数据面：Prometheus 试点告警 | Partial | Ready，alerts 空 |
| 数据面：日志可查 | Missing | MCP/CLS 未起 |
| 数据面：MONITOR_TARGET_MODE=prometheus | Missing | 默认 self |
| 数据面：变更源策略 | Partial | 代码骨架已声明不可用；产品决策未签 |
| 数据面：aiops-docs 索引 | Unknown | Milvus 有 biz，未验 runbook 内容 |
| 上下文：两轮记忆 M1 | Missing | 单测未绿且文件损坏 |
| 上下文：checkpoint conservative | Partial | 默认 replay=false；resume/落盘仍有差距清单项 |
| 质量：最小 8 题 | Missing | 未执行 |
| 质量：23 题套件 | Partial | 用例 12/23，0 跑分 |
| 运维：一键启停可复现 | Missing | backend/frontend/MCP 本轮未起 |
| 运维：回滚演练 | Missing | 未做 |
| 运维：OnCall 联系人 | Missing | D5 待指定 |
| 前置决策 D1–D5 | Missing | 全部 `[!]` |
| 产品功能面（Harness/前端/记忆/RAG） | Partial | L0 演示可用级；L1 证据路径不足 |
| Day0 基线快照文档 | Complete | 已落盘 |
| 任务板文档 | Complete | 已创建并滚动；执行进度仍早期 |

## Test And Verification Notes

| 命令 | 结果 |
| --- | --- |
| `python -m pytest tests --tb=no --no-cov` | **ERROR** 收集 `test_harness_service.py`（IndentationError L4424） |
| `python -m pytest tests --ignore=tests/test_harness_service.py -q` | 其余用例 **全部通过**（无 failed） |
| `cd frontend && npm test -- --run` | **1 failed / 51 passed**；失败：`App.test.tsx` 期望文案「工具执行」 |
| Redis `PING` | `NOAUTH Authentication required` |
| Prometheus `/-/ready` | Ready |
| Prometheus `/api/v1/alerts` | `alerts: []` |
| Milvus list collections | `experience_memory`, `biz` |
| MCP 8003/8004 | 超时 |
| Backend 9900 / Frontend 5173 | 未启动 |
| `.env` 已设键 | `HARNESS_MCP_ENABLED` 等 harness 超时/委派相关 + `LLM_API_KEY` + RAG 权重；**无** `AUTH_TOKEN_SECRET` / `MONITOR_TARGET_MODE` / `LOG_PROVIDER` |

相对 Day0（242p/9f）的变化：**不是“修了几条失败”，而是 harness 主测文件损坏导致整文件不可跑** —— 完成度判定应标为 **回退**。

## Next Steps

1. **立刻修复** `tests/test_harness_service.py` 语法/缩进（必要时从 git 历史恢复），恢复可收集状态。  
2. **重跑** 全量 pytest，更新 Day0/任务板失败快照；按 T1.1→T1.8 修绿（优先两轮历史、verify、澄清）。  
3. **并行安全底线**：登录表、token TTL、`AUTH_TOKEN_SECRET`、CORS、memory 写鉴权。  
4. **签字 D1–D5**；按方案 A 拉起 MCP + backend + frontend；`MONITOR_TARGET_MODE=prometheus`；准备试点告警/日志。  
5. **修 frontend 失败用例**；手工最小 8 题打分；写 `docs/go-nogo-YYYYMMDD.md`。  
6. 仅当工程+安全+数据面+M1+最小质量线全勾后，再讨论 Conditional Go 或 L1 Go；当前只允许 **L0 演示**。

---

**一句话：** 评测资产（档 A）已就绪，但 P0 门禁未过且主链路测试文件损坏导致工程门禁回退——**L1 No-Go，下一步先修测试文件与安全/数据面，再谈出分。**
