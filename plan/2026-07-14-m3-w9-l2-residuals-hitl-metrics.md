# M3 总计划 + W9 实施计划：L2 残留优先 · HITL/指标骨架 · 通向 L3

> **角色边界**：本文件描述「怎么演进 / 怎么做」。用户批准本计划并明确授权「按计划实现 / 开始做」后，再改 harness / 前端业务代码。  
> **日期**：2026-07-14  
> **状态**：待批准  
> **上级**：
> - [3 个月路线图 §4](plan/2026-07-13-complete-agent-system-3-month-roadmap.md)
> - [L2 出口 Conditional](docs/pilot/l2-exit-review-2026-07-14.md)（full **20/23**、P50 **64s**、Core **4/5**）
> - [交接](docs/pilot/handoff-2026-07-14-l15-conditional.md)
> - [CLAUDE.md](CLAUDE.md)（先计划后编码）

**用户本轮拍板**：
1. 整月 M3 计划 + **先做 W9**（不一口气做完 W9–W12）
2. W9 排序：**L2 残留（S5/P1/RE2）优先** + HITL/升级/质量指标骨架；compose 仅文档/check 骨架

批准后落盘到仓库：
- `plan/2026-07-14-m3-overview.md`（整月）
- `plan/2026-07-14-m3-w9-l2-residuals-hitl-metrics.md`（W9 实施）
- 并挂到 `AGENTS.md` Current Plan Index

---

## Context（现状与问题）

### 从哪到哪

| 维度 | 现在（L2 Conditional，2026-07-14） | M3 末目标（L3 预生产） |
|---|---|---|
| 产品 | 只读协作诊断副驾 | 预生产白名单值班副驾（仍默认只读） |
| 评测 | full **20/23**；Core **4/5** | ≥21/23；**Core 5/5**；专项门禁稳 |
| 时延 | full P50 **64s** / P95 **143s**；minimal 可回退 ~131s | 稳态 P50 **≤75s**、P95 **≤150s** |
| 协作专项 | P1 并行事件未触发；RE2 replan 未触发 | P1/RE2 可复现通过 |
| 学习 | D1 蒸馏仅草案，默认 false | 半自动蒸馏 + 失败反模式 |
| HITL | 无建议动作 / 无升级块 | 建议动作确认（不执行）+ 升级路径 |
| 可观测 | `/metrics` 仅 HTTP/本机资源；trace 可选 | agent_runs / latency / tools / tokens |
| 平台 | 本机 bat；无 compose 一键 | 清单 + check + compose/强化 start |
| H3 | 真人 OnCall **仍豁免** | 有升级路径后条件化解豁免 |

### full 失败根因（W9 必须啃）

| case | err | 根因判断 | 修复方向 |
|---|---|---|---|
| **S5-slow-response** | `harness_degraded_fallback` @~210s | 外层 harness 超时 ~180s 后降级；diagnosis 跨域工具链过长 | 步数/工具帽 + early close + 降重复 retrieve；必要时 S5 路径优先 metric/log 短路 |
| **P1-parallel-cross-domain** | `required_parallel_event_not_triggered` | 模型常用串行 `delegate_to_expert`，未调 `delegate_parallel`；`prefer_parallel` 仅 case setup，主路径未强制 seed | 跨域/ prefer_parallel 时 **确定性 seed 或强制 tool_choice 倾向 parallel**；保证 emit `delegate_parallel_*` 或 aux `parallel` |
| **RE2-replan-or-gap** | `required_replan_not_triggered` | `_should_replan` 要求「主调查工具失败且尚无成功证据」等；Prometheus 失败后若有其它成功/未进入 mid-loop 条件则 **0 replan**；`simulate=prometheus_unavailable_with_delegation_off` 未在 harness 内强制失败+replan | 主 metric 工具失败即允许 replan（即使有弱成功）；或 RE2 场景下 force replan once + gap 话术 |

评测检测（勿改坏语义）：
- replan：`stage=replan` 或 complete.`replan_times_used`
- parallel：`delegate_parallel` tool_event 或 stage `delegate_parallel_start/done` 或 aux `parallel=true`

### 已有可复用资产

| 资产 | 路径 |
|---|---|
| replan 规则 | `app/agent/harness/loop.py` `_should_replan` / `_apply_replan`；`planner.rule_replan` |
| 并行委派 | `subagent.create_delegate_parallel_tool`；loop 已 emit `delegate_parallel_*` |
| force 首委派（串行） | `HARNESS_FORCE_EXPERT_DELEGATION`（可仿造 parallel seed） |
| 蒸馏草案 | `plan/2026-07-14-m2-auto-distill-draft.md` |
| metrics 骨架 | `app/core/metrics.py`（仅 HTTP/CPU/mem；可扩 agent_*） |
| trace | `app/agent/harness/trace_export.py` |
| 评测 | `scripts/evaluate_oncall_local.py`；cases S5/RE2/P1 |

---

## 设计决策（含默认开关）

| ID | 决策 | 选择 | 理由 |
|---|---|---|---|
| D-M3-0 | 节奏 | **W9 残留+骨架 → W10 学习/澄清/compose → W11 观测/online → W12 L3 出口** | 用户拍板；避免一口吃完 |
| D-M3-1 | 自动蒸馏默认 | **false**；半自动 confirm 优先 | 沿用 D1 草案；防脏写 |
| D-M3-2 | 只读红线 | **永不**自动执行重启/回滚/扩缩容 | 产品红线 |
| D-M3-3 | P1 触发 | 跨域 diagnosis 或 setup/`prefer_parallel` 信号 → **框架 seed `delegate_parallel`（可关）** | 不依赖模型自觉 |
| D-M3-4 | RE2 触发 | 主路径 metric 调查工具失败 → replan 条件放宽；可选「强制 1 次 replan after primary fail」开关默认 **true** | 对齐 require_replan |
| D-M3-5 | S5 时延 | 不靠单纯加长 timeout；**减步数/重复工具 + investigation early close 生效** | 稳态 P50 |
| D-M3-6 | HITL | `suggested_actions[]` 只读建议；确认 **仅审计** | 路线图 E1 |
| D-M3-7 | 升级块 | `ONCALL_ESCALATION_CONTACTS` 可空 → 标准「未配置」 | 不伪造联系人 |
| D-M3-8 | 指标 | 进程内 Prometheus counters/histograms 挂现有 `/metrics` | 复用 metrics.py |
| D-M3-9 | 部署 W9 | 文档清单 + `scripts/check_env_secrets.py` 骨架；完整 compose 放 W10 | 控制范围 |

---

## 范围与非目标

### M3 整月 in-scope（W9–W12）

- L2 残留：S5 / P1 / RE2 + full 复测稳态  
- HITL 建议动作 + 升级路径  
- 半自动蒸馏 + 失败反模式（后半）  
- agent 质量/成本指标；可选 OTEL  
- 部署清单 / compose 强化 / 密钥 check  
- L3 出口评审（书面；H3 条件化）

### 明确非目标（3 个月红线延续）

1. 自动处置执行器  
2. 默认开启静默全量蒸馏  
3. 多区域多活 / 完整 SSO  
4. 物理删除 RouterService（W12 仅评估）  
5. 把 Conditional 话术偷换成无条件 L3 而不跑门禁  

### W9 非目标

- 完整 compose 一键生产化  
- 失败记忆入库实现（W10）  
- 澄清 UI 大改（W10）  
- Online 抽样（W11）  
- OTEL（W11 可选）  

---

## M3 四周地图

| 周 | 主题 | 主 WP | 出口切片 |
|---|---|---|---|
| **W9**（本轮实现） | L2 残留 + HITL/指标/清单骨架 | L-res / P1 / RE2 / E1a / E2a / G2a / H2a | S5+P1+RE2 单测+selected live；HITL 字段可演示；metrics 有 agent_*；清单文档 |
| **W10** | 学习 + 澄清 + 平台 | D1 实现 / D2 / E3 / H2b compose / H3 审计浅层 | 蒸馏 confirm 路径；compose 可拉起 |
| **W11** | 观测飞轮 + 收敛 | G2 补全 / G3 可选 / F4 online / H4 双路径 | 成本看板；online 模板 |
| **W12** | L3 出口 | 全量回归 / 北极星 / 出口评审 | L3 Go/Conditional/No-Go |

北极星对齐（书面豁免须在 L3 评审写明）：N1≥21/23 Core5/5；N2 P50≤75；N3 P95≤150；N5 re-evidence 可观测；N6 并行占比；N7 蒸馏；N8 CI；N10 只读安全。

---

## W9 实施步骤（可验收）

### WP-W9-1 · P1 parallel 确定性触发（P0）

1. 在 harness 路由/plan 后识别跨域信号：  
   - case/setup 或用户话术含「并行」/ metric+log 双域；或 config `HARNESS_FORCE_PARALLEL_ON_CROSS_DOMAIN=true`（默认 **true**）  
2. 若尚未委派且 `HARNESS_PARALLEL_DELEGATION_ENABLED`：  
   - **seed** 一次 `delegate_parallel`（metric+log 专家子任务），或在首步 system 强提示 + 若模型仍串行则框架补发 parallel（二选一，优先 seed 确定性）  
3. 确保 timeline 出现 `delegate_parallel_start` / tool `delegate_parallel`（评测可识别）  
4. 单测：跨域 message → `parallel_event` true；开关 false 不 seed  

**文件**：`loop.py`、`subagent.py`、`config.py`、`.env.example`、`tests/test_m3_w9_residuals.py`

### WP-W9-2 · RE2 replan 触发收紧/放宽条件（P0）

1. 调整 `_should_replan`：  
   - **主 focus 路由对应调查工具失败** → 即使已有「弱成功」（如 knowledge）仍可 replan 一次（新开关 `HARNESS_REPLAN_ON_PRIMARY_FAIL=true` 默认 true）  
2. 可选：`simulate` / 测试 hook 不进生产；live 依赖真实 Prometheus 失败或工具 fail 事件  
3. replan 后消息继续强调「不得用知识库冒充实时指标」  
4. 单测：构造 primary tool fail timeline → stage replan 必现；max_times=1 不二次  

**文件**：`loop.py` `_should_replan`、`planner.rule_replan`（如需 gap 文案）、`tests/test_m3_w9_residuals.py`

### WP-W9-3 · S5 长尾 / degraded fallback（P0）

1. 审计 diagnosis 路径：重复 `search_app_logs` / 变更重试 / 多轮 retrieve  
2. 强化已有：`HARNESS_INVESTIGATION_EVIDENCE_EARLY_CLOSE`、`HARNESS_DIAGNOSIS_MAX_STEPS`、delegate max rounds=1  
3. 新增可选：`HARNESS_SLOW_PATH_TOOL_CAP` 或「同名工具成功 N 次后禁止再调」只读去重（framework）  
4. 目标：S5 在 **≤180s** 内 complete 且非 degraded_fallback；不靠把 timeout 调到 300  
5. 单测：early close / tool cap 行为；live selected S5  

**文件**：`loop.py`、tool executor 去重（若已有则复用）、`config.py`、tests

### WP-W9-4 · HITL suggested_actions 骨架（P1）

1. complete 前组装 `suggested_actions[]`：`{id, title, risk, requires_confirm:true}`，措辞 **建议** 非 **已执行**  
2. SSE：complete payload 或 decision_event 携带；开关 `HITL_SUGGESTED_ACTIONS_ENABLED=true`  
3. 最小确认：API `POST` 审计日志 only（无 executor）  
4. 前端：若成本高，W9 可先 **API+事件**，UI 占位一行；优先不破坏 N2/N3  
5. 单测：开关关无字段；确认不调用写工具  

**文件**：`loop.py`、`config.py`、`app/api/`（audit 或 memory 旁路）、可选 frontend、tests

### WP-W9-5 · 升级联系人块（P1）

1. `ONCALL_ESCALATION_CONTACTS`（JSON 或 `name|channel;...`）  
2. final 答案底部标准升级块；空配置 → 「未配置值班联系人，请走现有 OnCall 流程」  
3. 更新 pilot 文档：H3 条件化（有配置则可演示升级路径）  

**文件**：`config.py`、answer 组装、`docs/pilot/*`、tests

### WP-W9-6 · agent 质量指标 G2a（P1）

1. 在 `app/core/metrics.py` 增加：  
   - `agent_runs_total{status}`  
   - `agent_latency_seconds` histogram  
   - `agent_re_evidence_total` / `agent_replan_total` / `agent_delegate_parallel_total`（counter）  
2. loop complete/fallback 路径 inc  
3. 文档：`/metrics` 抓取说明  

**文件**：`metrics.py`、`loop.py`、`docs/pilot` 或 README 片段、tests（counter 可测）

### WP-W9-7 · 部署清单 H2a（P2）

1. `docs/pilot/deploy-checklist.md`：依赖端口、密钥、健康检查、评测命令  
2. `scripts/check_env_secrets.py`：检测默认密码/空 LLM key（非阻断 exit code 可配置）  
3. **不做**完整 compose（W10）  

### WP-W9-8 · 文档与评测收口

1. 写 `plan/2026-07-14-m3-overview.md` + `plan/2026-07-14-m3-w9-*.md` + progress  
2. 更新 `AGENTS.md` / `CLAUDE.md` / 交接 L2 残留状态  
3. 验证命令：

```bash
# 单测
python -m pytest tests/test_m3_w9_residuals.py tests/test_m3_w9_hitl_metrics.py \
  tests/test_m2_w8_merge_eval.py tests/test_m1_w3_replan_latency.py \
  tests/test_m2_w6_parallel_delegation.py -q --no-cov

# selected live（健康环境）
python scripts/evaluate_oncall_local.py --case S5-slow-response --case RE2-replan-or-gap --case P1-parallel-cross-domain --timeout-extra 90

# 可选 full 复测（隔夜）
python scripts/evaluate_oncall_local.py --suite full --timeout-extra 90
```

**W9 出口标准**：
- 单测绿  
- selected：**P1 parallel_event**、**RE2 replan≥1**、**S5 pass 且非 degraded_fallback**（尽力；若环境不稳写入 progress 与豁免）  
- HITL 字段 + 升级块可演示；N2/N3 不回归  
- `/metrics` 可见 `agent_runs_total`  
- 部署清单文档存在  

---

## 文件清单（W9）

| 文件 | 动作 |
|---|---|
| `app/agent/harness/loop.py` | parallel seed；replan 条件；S5 路径控制；HITL/升级/metrics hook |
| `app/agent/harness/planner.py` | replan todos 必要时强化 |
| `app/agent/harness/subagent.py` | parallel 子任务模板（若 seed 需要） |
| `app/config.py` / `.env.example` | 新开关 |
| `app/core/metrics.py` | agent_* 指标 |
| `app/api/*` | HITL confirm audit（最小） |
| `frontend/*` | 可选最小展示 |
| `tests/test_m3_w9_residuals.py` | 新建 |
| `tests/test_m3_w9_hitl_metrics.py` | 新建 |
| `docs/pilot/deploy-checklist.md` | 新建 |
| `scripts/check_env_secrets.py` | 新建骨架 |
| `plan/2026-07-14-m3-overview.md` | 新建 |
| `plan/2026-07-14-m3-w9-l2-residuals-hitl-metrics.md` | 新建（与本计划同步） |
| `plan/2026-07-14-m3-w9-progress.md` | 实现中 |
| `AGENTS.md` / `CLAUDE.md` / handoff / L2 exit §5 | 索引与残留状态 |

---

## 开关一览（W9）

| 开关 | 默认 | 关后 |
|---|---|---|
| `HARNESS_FORCE_PARALLEL_ON_CROSS_DOMAIN` | **true** | 仅模型自觉 parallel |
| `HARNESS_REPLAN_ON_PRIMARY_FAIL` | **true** | 回退现有 `_should_replan` |
| `HARNESS_DIAGNOSIS_MAX_STEPS` | 3（已有） | 保持 |
| `HARNESS_INVESTIGATION_EVIDENCE_EARLY_CLOSE` | true（已有） | 保持 |
| `HITL_SUGGESTED_ACTIONS_ENABLED` | **true** | 无 suggested_actions |
| `ONCALL_ESCALATION_CONTACTS` | `""` | 标准未配置块 |
| `LONG_TERM_MEMORY_AUTO_DISTILL` | **false** | W9 不实现入库 |
| 既有 parallel / replan / re-evidence | 保持 M2 默认 | 回滚路径不变 |

---

## 验证方式（命令 + 出口）

见 WP-W9-8。额外：

- 负例：`N2` / `N3` 不得出现「已重启/已回滚/执行成功」  
- metrics：`curl -s localhost:9900/metrics | findstr agent_runs`  
- 回滚验证：上述新开关 false 后行为接近 M2  

**L3 不在 W9 宣布**；W9 只消除 L2 专项门禁缺口并铺 HITL/指标骨架。

---

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| force parallel 增加时延 | max experts 2；evidence-only；可关 |
| replan 过频 | max_times=1 不变 |
| S5 仍超时 | progress 记实；W10 继续模型分层/缓存 |
| HITL 被理解成已执行 | 固定拒写词 + N2/N3 |
| 指标基数爆炸 | status/tool 标签白名单 |

```text
HARNESS_FORCE_PARALLEL_ON_CROSS_DOMAIN=false
HARNESS_REPLAN_ON_PRIMARY_FAIL=false
HITL_SUGGESTED_ACTIONS_ENABLED=false
ONCALL_ESCALATION_CONTACTS=
```

---

## 实现顺序（批准后）

1. 落盘 `plan/2026-07-14-m3-overview.md` + W9 计划到 `plan/`，挂 `AGENTS.md`  
2. 编码 WP-W9-1 → 2 → 3（残留）  
3. WP-W9-4/5/6（HITL/升级/metrics）  
4. WP-W9-7 文档 + check 脚本  
5. 单测 + selected live；写 progress；更新交接/L2 §5  

**不在本批准范围内自动开工 W10–W12 代码。**

---

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-14 | 初稿；用户确认：整月计划+先 W9；残留优先+HITL/指标骨架 |
