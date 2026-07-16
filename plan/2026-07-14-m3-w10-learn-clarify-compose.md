# M3 W10 实施计划：半自动蒸馏 · 失败反模式 · 澄清升级 · Compose 草案

> **角色边界**：本文件描述「怎么演进 / 怎么做」。用户批准本计划并明确授权「按计划实现 / 开始做」后，再改 harness / 记忆写入 / 前端业务代码。  
> **日期**：2026-07-14  
> **状态**：已批准并实现主干（见 [progress](./2026-07-14-m3-w10-progress.md)）  
> **上级**：
> - [M3 总览](./2026-07-14-m3-overview.md)
> - [3 个月路线图 §4](./2026-07-13-complete-agent-system-3-month-roadmap.md)
> - [M2 蒸馏草案](./2026-07-14-m2-auto-distill-draft.md)
> - [W9 进度](./2026-07-14-m3-w9-progress.md)（selected 3/3 · full **23/23** Core **5/5**）
> - [当前交接](../docs/pilot/handoff-2026-07-14-m3-w9.md)
> - [CLAUDE.md](../CLAUDE.md)（先计划后编码）

**本轮目标一句话**：把「学习路径」从文档草案变成**可演示闭环**——成功 run 可出 draft 经验待确认、失败可记反模式并降权召回、澄清可结构化填槽、本机/compose 拉起有文档与骨架；**不**宣布 L3，**不**默认静默全量入库。

---

## Context（现状与问题）

### 从哪到哪

| 维度 | 现在（W9 收口后） | W10 出口切片 |
|---|---|---|
| 产品 | 只读 L2 Conditional 协作诊断副驾 | 同上 + **可演示学习/澄清/平台草案** |
| 评测 | full **23/23**；Core **5/5**；P50 **71s** / P95 **174s** | 不回退；N2/N3 仍绿；蒸馏/反模式有**单测 + 1 条 e2e/手工** |
| 成功蒸馏 | 仅 [D1 草案](./2026-07-14-m2-auto-distill-draft.md)；API 仅 feedback/manual | complete 可生成 **pending draft**；confirm 入库；`AUTO_DISTILL` 默认 false |
| 失败学习 | 无 anti_pattern / 无效序列记忆 | 失败/超时/空证据可记 **weak 反模式**；召回降权死路 |
| 澄清 | `MissingParameterClarifier` 文本问句；前端无快捷填槽 | 结构化缺参 + 前端快捷填槽（最小） |
| 平台 | `deploy-checklist` + `check_env_secrets`；无 compose | compose **草案** 或强化 start + 文档；密钥 check 已有则复用 |
| HITL | suggested_actions + confirm 审计 only（W9） | 可挂「采纳经验」确认（仍不执行变更） |
| H3 / L3 | 真人豁免；非 L3 | **不变** |

### 已有可复用资产

| 资产 | 路径 | W10 用法 |
|---|---|---|
| 经验卡 CRUD / 召回 | `app/services/experience_memory_service.py` | 扩展 draft/confirm/anti_pattern；**禁止**默认 auto 写 |
| PII 脱敏 | `app/services/memory_safety.py` | 蒸馏/反模式必走 `redact_memory_text` |
| 反馈 API | `app/api/memory.py` + 前端 `memoryApi.ts` | 旁路增加 confirm-distill；feedback 路径保留 |
| HITL 审计 | `app/api/hitl.py` | 模式对齐（audit only）；经验采纳可走 memory API |
| Clarifier | `app/agent/harness/clarifier.py` | 结构化 `missing_params` / `defaults` 已有 dataclass |
| complete 钩子 | `app/agent/harness/loop.py`（verify 后 / complete 前） | distill + anti_pattern 触发点 |
| 部署清单 | `docs/pilot/deploy-checklist.md` | W10 补 compose/一键章节 |
| 密钥检查 | `scripts/check_env_secrets.py` | start/compose 前调用 |
| 启动 bat | `start-all-windows.bat` | 文档注明 healthy 跳过重启坑；可选强化 |

### 关键缺口（代码事实）

1. **无** `LONG_TERM_MEMORY_AUTO_DISTILL` 配置与 complete 钩子写入（config 仅有 confidence / threshold）。  
2. `experience_memories` 表字段含 `enabled` / `source_type` / `confidence`，**无** `status=pending|active|rejected` 语义；`_create_memory` 默认 `enabled=1` 即入库可召回。  
3. 召回 `recall_experience` **不过滤**反模式、不降权「已知死路工具序列」。  
4. Clarifier 仅产出自然语言 `question`；complete/SSE 未稳定暴露结构化 `missing_params` 给前端快捷填。  
5. 仓库 **无** `docker-compose*.yml`；依赖本机 bat + 外部 Prom/Milvus/Redis。

---

## 设计决策（含默认开关）

| ID | 决策 | 选择 | 理由 |
|---|---|---|---|
| D-W10-1 | 自动蒸馏默认 | **`LONG_TERM_MEMORY_AUTO_DISTILL=false`** | 路线图 D-M3-1；防脏写；预生产不静默全量 |
| D-W10-2 | 半自动路径 | complete → **draft（pending，默认不进召回）** → 人确认后 `enabled=1` / status=active | 可演示学习且可控 |
| D-W10-3 | 蒸馏触发条件 | 见 §触发规则；**纯 knowledge how-to 无调查工具成功 → 不蒸馏** | 避免文档复读进经验库 |
| D-W10-4 | 失败反模式存储 | **复用** experience 表：`source_type=anti_pattern` + 低 confidence + 可选 `enabled`；**或** 旁路 SQLite 表 `anti_patterns`（实现时二选一，优先复用表减迁移） | 少新存储面；召回侧显式过滤/降权 |
| D-W10-5 | 反模式用途 | 召回时 **标注「勿重复此无效路径」**；可选 harness 提示抑制同名失败工具连打（轻量，可关） | 对齐 WP-D2「第二次更早 gap/换工具」 |
| D-W10-6 | 澄清 | **结构化缺参**进 SSE（`clarification` / decision_event）；前端快捷 chip 填槽；**不**大改 Chat 布局 | 控制前端半径 |
| D-W10-7 | Compose | **草案优先**：`deploy/compose/docker-compose.pilot.yml`（backend+redis 必选；prom/milvus/mcp **profile 或文档外置**）+ 更新 checklist；Windows 主路径仍 bat | 新机器可部分复现；不全盘容器化 MCP |
| D-W10-8 | 审计浅层 | distill confirm / anti_pattern 写 **带 owner_key/session 的日志或表字段**；跨用户不可 list 他人 pending（沿用 `require_session_owner`） | WP-H3 浅层，不做完整多租户 |
| D-W10-9 | 只读红线 | 蒸馏/反模式/澄清 **永不**触发重启/回滚/扩缩容；HITL 确认文案禁止「已执行」 | 产品红线 |
| D-W10-10 | 时延 | W10 **不**以压 P95 为主题；蒸馏钩子必须 **async 非阻塞 complete 关键路径**（失败只 log） | 避免 P50 回退 |
| D-W10-11 | 偏好迁移 WP-D3 | **W10 仅文档/可选最小**；默认不做自动抽偏好 | 防 scope creep；完整放 W11 |
| D-W10-12 | L2 书面改判 | **不在 W10 做**；可选附录 `215932` 另议 | 产品拍板项 |

### 蒸馏触发规则（成功 draft）

```text
complete
  AND not harness_degraded_fallback（timeout soft-close 有证据可放行，见实现注释）
  AND verification.confidence in {medium, high} 或映射分数 ≥ min
  AND ≥1 次成功非 context 调查工具（metric/log/change/diagnosis 工具；非纯 retrieve-only 可选策略见下）
  AND route not in {clarify}
  AND LONG_TERM_MEMORY_DISTILL_ENABLED=true（总开关，默认 true 以允许 draft；与 AUTO 分离）
  AND （AUTO_DISTILL=false → status=pending, enabled=0）
     或（AUTO_DISTILL=true AND DISTILL_REQUIRE_CONFIRM=false → 直接 active）
```

**禁止蒸馏**：

- 纯 knowledge 问答且无调查工具成功证据  
- N2/N3 类「拒绝写操作」成功话术不当作故障经验  
- prompt-inject / 无数据源缺口类（可选：可记 anti_pattern 而非 success）

### 失败反模式触发（草案）

```text
complete 或 soft-close / fallback
  AND （主调查工具失败序列 或 replan 后仍无成功证据 或 zero-evidence gap 合成）
  AND HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=true（默认 true）
→ 写入 source_type=anti_pattern 卡片：症状摘要 + 无效工具名序列 + 「勿重复」resolution
→ enabled 默认 true 但 recall 标记 is_anti_pattern；success 经验优先
```

---

## 范围与非目标

### W10 in-scope

1. **WP-W10-1** · 半自动蒸馏（D1 实现）：draft → confirm API → 可选 auto  
2. **WP-W10-2** · 失败反模式（D2）：捕获 + 召回降权/提示  
3. **WP-W10-3** · 澄清体验（E3）：结构化缺参事件 + 前端快捷填槽最小  
4. **WP-W10-4** · Compose / 一键草案（H2b）+ checklist 更新  
5. **WP-W10-5** · 审计浅层（H3 浅）：owner 贯穿 pending list/confirm  
6. **WP-W10-6** · 单测 + 文档 + progress；可选 1 条 live 蒸馏手工冒烟  

### 明确非目标（W10 不做）

1. 默认 `AUTO_DISTILL=true` 生产静默全量写  
2. 自动处置执行器 / HITL「确认即执行」  
3. 完整 OTEL / Langfuse（W11）  
4. Online 抽样评测（W11）  
5. 双路径 ContextBuilder 收敛（W11 WP-H4）  
6. 完整多区域多活 / SSO  
7. L3 出口宣布或无条件 L2 Go 改判  
8. 前端「经验管理中心」大盘（最多 1 个采纳按钮/列表最小）  
9. 训练数据导出离机  
10. 变更源恢复编造（option B 不变）

---

## 实施步骤（可验收）

### WP-W10-1 · 半自动蒸馏 D1（P0）

1. **Config**（`app/config.py` + `.env.example`）：
   ```text
   LONG_TERM_MEMORY_DISTILL_ENABLED=true
   LONG_TERM_MEMORY_AUTO_DISTILL=false
   LONG_TERM_MEMORY_AUTO_DISTILL_MIN_CONFIDENCE=medium
   LONG_TERM_MEMORY_DISTILL_REQUIRE_CONFIRM=true
   ```
2. **Schema**：`experience_memories` 增加可空列或复用约定：
   - 推荐：`status TEXT DEFAULT 'active'`（`pending|active|rejected`）+ `enabled` 语义：pending 时 `enabled=0` 不进召回  
   - 迁移：启动 `_ensure_schema` 兼容旧库 `ALTER TABLE` 忽略已存在  
3. **Service API**：
   - `create_draft_from_run(...)` → pending  
   - `confirm_draft(experience_id, owner)` → active + index upsert  
   - `reject_draft(experience_id)` → rejected / enabled=0  
   - 默认 `list(enabled=True)` **不含** pending  
4. **Harness hook**（`loop.py` complete 前，try/except 吞错）：
   - 评估触发规则 → draft  
   - complete payload 增加 `distill_draft: {experience_id, status}`（可关）  
5. **API**：
   - `POST /api/memory/distill/confirm` `{ExperienceId}`  
   - `POST /api/memory/distill/reject`  
   - `GET /api/memory/distill/pending?limit=`（owner 过滤）  
6. **前端最小**：complete 后若有 draft → 「采纳为经验」按钮 → confirm；失败 toast；**无**大改版  
7. **单测**：默认 false auto → 零 active 写入；confirm 后可 recall；PII 脱敏；knowledge-only 不产生 draft  

**文件**：`config.py`、`experience_memory_service.py`、`models/memory.py`、`api/memory.py`、`loop.py`、`.env.example`、`frontend/src/api/memoryApi.ts`、可选 Chat 组件、`tests/test_m3_w10_distill.py`

### WP-W10-2 · 失败反模式 D2（P0）

1. 开关：`HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=true`（默认 true）  
2. 从 timeline 抽取：失败工具名序列（有序去重）、route、症状摘要  
3. `create_anti_pattern(...)`：`source_type=anti_pattern`，confidence≤weak，resolution 固定模板「无效路径，勿重复；先换工具或声明缺口」  
4. **召回**：`recall` / `recall_experience` 工具输出：
   - anti_pattern 单独段落或 `is_anti_pattern=true`  
   - 成功经验优先排序；anti 仅作负向提示  
5. （可选轻量）loop 内：若最近 anti 命中同工具序列 → 日志/decision 提示；**不**硬禁工具以免误伤（默认关：`HARNESS_ANTI_PATTERN_TOOL_HINT=true`）  
6. **单测**：失败 run → 有 anti 记录；recall 文本含「勿重复」；开关 false 不写  

**文件**：`experience_memory_service.py`、`recall_experience.py`、`loop.py`、`config.py`、`tests/test_m3_w10_anti_pattern.py`

### WP-W10-3 · 澄清体验 E3（P1）

1. `_emit_clarification` 事件 payload 增加结构化字段：
   ```json
   {
     "type": "decision_event",
     "stage": "clarify",
     "missing_params": ["service", "time_window"],
     "defaults": {},
     "question": "..."
   }
   ```
2. Clarifier：补强常见 OnCall 槽位（service / env / time_window）识别，减少误澄清；单测 C1 不回归  
3. 前端：澄清气泡下快捷 chip（缺参名）→ 点击填入输入框或一键发送「服务=xxx」模板  
4. **不做**全自动槽位表单引擎  

**文件**：`clarifier.py`、`loop.py`、`frontend` Chat/AgentProcess、`tests/test_m3_w10_clarify.py`（或扩现有）

### WP-W10-4 · Compose / 一键草案 H2b（P1）

1. 新增 `deploy/compose/docker-compose.pilot.yml`（草案）：
   - 服务建议：`backend`（build 或 image 占位）、`redis`  
   - `prometheus` / `milvus` 用 profile `full` 或文档「外置依赖」  
   - MCP：优先 **文档说明本机 bat 起**（容器化 MCP 若成本高则非阻塞）  
2. `docs/pilot/deploy-checklist.md` 增补：
   - compose 启动命令  
   - Windows bat 与 compose 对照  
   - **强制重启 backend** 提醒（healthy 跳过坑）  
3. `scripts/check_env_secrets.py` 已存在则在 compose README 引用；可选 Makefile target `make pilot-up`  
4. **验收**：文档路径可复制；不要求 CI 真起全栈  

**文件**：`deploy/compose/*`、`docs/pilot/deploy-checklist.md`、可选 `Makefile`、`README` 片段

### WP-W10-5 · 审计浅层 H3（P2，可与 W10-1 合并）

1. distill confirm/reject 日志含 `owner_key` / `session_id` / `experience_id`  
2. pending list 必须 `require_session_owner` 或 project 范围 + 鉴权  
3. 单测：无 token/错 owner → 403/空  

### WP-W10-6 · 文档与评测收口

1. 写 `plan/2026-07-14-m3-w10-progress.md`（实现中勾选）  
2. 更新 `AGENTS.md` / `CLAUDE.md` / M3 overview / 交接指针  
3. 验证命令见下节  
4. **不**强制 full 23 重跑；若动 harness 关键路径则至少：
   - W10 单测全绿  
   - 既有 soft 回归子集（W9 相关 + close_the_loop 抽样）  
   - 手工：1 次成功诊断 → draft → confirm → recall 可见  

---

## 文件清单

| 文件 | 动作 |
|---|---|
| `app/config.py` / `.env.example` | 蒸馏/反模式开关 |
| `app/services/experience_memory_service.py` | draft/confirm/anti_pattern/schema |
| `app/services/memory_safety.py` | 复用；必要时补测试 |
| `app/models/memory.py` | DistillConfirm 等请求体 |
| `app/api/memory.py` | confirm/reject/pending |
| `app/agent/harness/loop.py` | complete 钩子；澄清事件字段 |
| `app/agent/harness/clarifier.py` | 槽位识别增强 |
| `app/tools/recall_experience.py` | 反模式展示 |
| `frontend/src/api/memoryApi.ts` | confirm/reject |
| `frontend/src/components/*` | 采纳按钮 + 澄清 chip（最小） |
| `deploy/compose/docker-compose.pilot.yml` | 新建草案 |
| `docs/pilot/deploy-checklist.md` | 更新 |
| `tests/test_m3_w10_distill.py` | 新建 |
| `tests/test_m3_w10_anti_pattern.py` | 新建 |
| `tests/test_m3_w10_clarify.py` | 新建或合并 |
| `plan/2026-07-14-m3-w10-learn-clarify-compose.md` | 本计划 |
| `plan/2026-07-14-m3-w10-progress.md` | 实现后 |
| `plan/2026-07-14-m3-overview.md` | 状态一句 |
| `AGENTS.md` / `CLAUDE.md` / handoff | 索引 |

---

## 开关一览

| 开关 | 默认 | 关后 / 说明 |
|---|---|---|
| `LONG_TERM_MEMORY_DISTILL_ENABLED` | **true** | 不产生 draft |
| `LONG_TERM_MEMORY_AUTO_DISTILL` | **false** | 仅 draft，不自动 active |
| `LONG_TERM_MEMORY_DISTILL_REQUIRE_CONFIRM` | **true** | 与 auto 联用；confirm 关且 auto 开才直写 |
| `LONG_TERM_MEMORY_AUTO_DISTILL_MIN_CONFIDENCE` | `medium` | 低于阈值不蒸馏 |
| `HARNESS_ANTI_PATTERN_CAPTURE_ENABLED` | **true** | 不记失败反模式 |
| `HARNESS_ANTI_PATTERN_TOOL_HINT` | **true** | 不在 loop 提示死路 |
| 既有 HITL / replan / parallel / soft-close | 保持 W9 | 回滚路径不变 |
| `CHANGE_SOURCE_POLICY` | `unavailable` | **禁止**改回编造 |

回滚：

```text
LONG_TERM_MEMORY_DISTILL_ENABLED=false
LONG_TERM_MEMORY_AUTO_DISTILL=false
HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=false
HARNESS_ANTI_PATTERN_TOOL_HINT=false
```

---

## 验证方式（命令 + 出口标准）

### 单测

```bash
python -m pytest \
  tests/test_m3_w10_distill.py \
  tests/test_m3_w10_anti_pattern.py \
  tests/test_m3_w10_clarify.py \
  tests/test_m3_w9_residuals.py \
  tests/test_m3_w9_hitl_metrics.py \
  tests/test_m1_close_the_loop.py \
  -q --tb=line --no-cov
```

### 密钥 / 健康

```bash
python scripts/check_env_secrets.py
curl -sS http://127.0.0.1:9900/health
```

### 手工冒烟（蒸馏闭环）

1. 登录 → 对可观测服务跑一条 CPU/指标诊断至 complete  
2. complete payload 或 UI 出现 **draft**（`AUTO_DISTILL=false`）  
3. 「采纳经验」→ `confirm` → `GET /api/memory/experiences` 可见 enabled  
4. 新会话 `recall_experience` 或知识路径能命中（允许相似度阈值导致未命中时用 list 断言）  
5. 构造工具失败/RE2 类 → 存在 anti_pattern 记录且召回含负向提示  
6. 澄清题（C1）→ 结构化 missing_params + chip 可点  
7. N2/N3：不得出现「已执行/已重启」；蒸馏文案不得宣称已变更系统  

### 可选 live

```bash
# 不强制 full；若 harness 大改再考虑
python scripts/evaluate_oncall_local.py --case C1-clarify-metric-subject --timeout-extra 90
python scripts/evaluate_oncall_local.py --suite minimal --timeout-extra 90
```

### W10 出口标准

| # | 标准 |
|---|---|
| 1 | 上述单测绿 |
| 2 | 默认 `AUTO_DISTILL=false` 时 complete **不**静默写入可召回 active 经验 |
| 3 | confirm 路径 e2e/手工 1 条成功 |
| 4 | 反模式可捕获 + 召回可区分 |
| 5 | 澄清结构化字段可演示；C1 不回归 |
| 6 | compose 草案或强化文档存在；checklist 已更新 |
| 7 | N2/N3 安全红线不回退 |
| 8 | **不**宣布 L3；H3 仍豁免除非产品另批 |

---

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 脏经验污染召回 | 默认 pending；confirm 门禁；PII redact；knowledge-only 不写 |
| draft 爆炸占库 | pending TTL 可选（W10 可只文档；实现 `limit` list）；reject API |
| complete 钩子拖慢 P50 | try/except + 同步 SQLite 须快；失败只 log；必要时 `asyncio.to_thread` |
| anti_pattern 误伤合法重试 | 召回仅提示；tool hint 可关；不默认 ban 工具 |
| schema 迁移搞坏旧 DB | ALTER 兼容；列缺省；单测临时 db_path |
| 前端范围膨胀 | 只允许 1 按钮 + chip；大盘延期 |
| compose 在 Windows 不可用 | bat 仍主路径；compose 标 Linux/Docker Desktop 草案 |

---

## 实现顺序（批准后）

1. 落盘本计划（已完成）→ 挂 `AGENTS.md` → 用户批「按计划实现」  
2. WP-W10-1 schema + service + API + 单测（先于 harness 钩子）  
3. loop complete 钩子 + complete 字段  
4. WP-W10-2 反模式 + recall  
5. WP-W10-3 澄清结构化 + 前端最小  
6. 前端采纳按钮  
7. WP-W10-4 compose 草案 + checklist  
8. 回归单测 + 手工冒烟 → progress → 更新交接指针  

**不在本批准范围内自动开工 W11–W12 代码。**

---

## 与路线图 WP 映射

| 路线图 | W10 动作 |
|---|---|
| WP-D1 半自动沉淀 | **实现**（非草案） |
| WP-D2 失败反模式 | **实现** 捕获+召回 |
| WP-D3 偏好迁移 | 非目标 / 文档一句 |
| WP-E3 澄清升级 | **最小实现** |
| WP-H2 部署 compose | **草案 + 文档** |
| WP-H3 审计浅层 | **随 confirm API** |
| WP-E1/E2/G2 | W9 已做；W10 不重复除非回归 |

---

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-14 | 初稿：W9 收口后下一棒；蒸馏 confirm + 反模式 + 澄清 + compose 草案；待用户批准后编码 |
| 2026-07-14 | 用户「开始」→ 主干实现；单测 16 passed；progress 落盘 |
