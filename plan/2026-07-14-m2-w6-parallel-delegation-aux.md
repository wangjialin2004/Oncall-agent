# M2 W6 实施计划：并行委派 · Aux 真执行 · 协作评测

> **角色边界**：本文件描述「怎么做」。**用户明确授权「按 W6 计划实现 / 开始做」后**，再改 harness 业务代码。  
> **日期**：2026-07-14  
> **状态**：**主干已实施**（见 [progress](./2026-07-14-m2-w6-parallel-delegation-progress.md)）  
> **交接入口**：[docs/pilot/handoff-2026-07-14-l15-conditional.md](../docs/pilot/handoff-2026-07-14-l15-conditional.md)  
> **上级**：
> - [3 个月路线图](./2026-07-13-complete-agent-system-3-month-roadmap.md) §3 Month 2（WP-B1 / WP-B3 / WP-G1 / WP-F3 / WP-L2）  
> - [M2 W5 计划/进度](./2026-07-13-m2-w5-latency-eval-hardening-progress.md)（时延与评测硬化已合入）  
> - [CLAUDE.md](../CLAUDE.md)（**先计划后编码**；无计划不改 harness）

---

## 0. 一页摘要

| 项 | 内容 |
|---|---|
| 主题 | 跨域从「串行软提示」升级为 **可并行、可执行、可观测** 的多专家协作 |
| 范围 WP | **WP-B1** 并行委派 · **WP-B3** aux 真执行 · **WP-F3a** 评测补题 · **WP-G1a** 轨迹导出骨架 · **WP-L2a** 并行时延对照 |
| 明确不做 | 共享内核合入（W7 WP-B2）、变更源真接入（W7 决策）、自动蒸馏、HITL 执行器、自动处置、前端大改 |
| 入口证据 | W5：S1/RE1/RE2 真触发；minimal 在 MCP 故障下 6/10（非出口）；P50≈110s；**串行** `delegate_to_expert` only |
| 出口 | 单测绿 + 并行 wall-clock 证明 + aux 时间线可演示 + cases ≥22 + progress 对照报告 |
| 默认开关 | 并行委派 **开**；aux 默认 **parallel** max 2；全部可一键降级 |

---

## 1. Context（现状与问题）

### 1.1 M2 累计事实（2026-07-14）

| 点 | 现状 | 差距 |
|---|---|---|
| L1.5 | **Conditional Go**（P50 110s ≤120；未达 Go 100s） | 时延 backlog 仍在 |
| H3 | 真人 OnCall **仍豁免** | 不可无人值守主路径 |
| M1 W1–W4 | 闭环 / 白板 / ckpt / replan / early-close / 评测 20 题 | ✅ |
| M2 W5 | 诊断步数帽、委派 1 轮 + evidence-only、investigation early close、评测硬化、路由 override | ✅ 代码 + 部分 live |
| 委派 | 仅 `delegate_to_expert` **串行**；`subagent.py` 单专家 drain | **无 fan-out** |
| aux_routes | Router 可返回；planner 写 todo；replan 作跨域 hint | **从不自动执行** |
| 专家循环 | `ToolCallingExpert` 与 harness 双循环；W5 已共享 `GuardedToolExecutor` | 完整共享内核 → W7 |
| 评测 | `cases.jsonl` **20** 题；目标 23 | 缺 K3/N2/M2 等 |
| 并行 tool | 同一步多 tool_call 已 `asyncio.gather`（M1 W3） | 专家级并行仍无 |

### 1.2 问题 → WP

| ID | 问题 | WP |
|---|---|---|
| P1 | 跨域 diagnosis 串行委派 metric→log，wall-clock 近似相加，吃光 180s 外层预算 | **WP-B1** |
| P2 | `aux_routes=["metric","log"]` 只进 plan 文案，不产生真实工具/专家证据 | **WP-B3** |
| P3 | 评测无「必须并行」门禁；跨域 case 无法统计 parallel path 占比 | **WP-F3a** |
| P4 | complete 轨迹难离线回放，并行调试成本高 | **WP-G1a** |
| P5 | P50 仍 ~110s，M2 出口目标 ≤85s 依赖并行收益 | **WP-L2a**（观测+对照，不承诺本周冲到 85） |

### 1.3 非目标（W6 不做）

1. **共享 harness 子循环合入**（`HARNESS_SHARED_KERNEL_DELEGATION`）— 仅产出 **设计评审纪要**，实现放 W7  
2. 变更源生产接入 / 永久降权二选一落地（W7 周一决策）  
3. 自动经验蒸馏 / HITL 建议动作卡片  
4. 自动重启/回滚/扩缩容  
5. 物理删除 `RouterService` / 旧 `ToolCallingExpert`  
6. 前端过程面板大改（可选：若 SSE 已有并行事件则前端零改也可验收）  
7. 把 L1.5 Conditional 强行改 Go（需稳定 LLM + 全套 minimal 重跑；本周不挡 W6 合入）

---

## 2. 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| D-W6-1 并行 API 形态 | **新增工具** `delegate_parallel`（experts[] + subtasks[]），保留串行 `delegate_to_expert` | 契约清晰；LLM 可选其一；关并行开关时只注销并行工具 |
| D-W6-2 执行原语 | `asyncio.gather` + **每专家独立** `harness_delegate_timeout_seconds`；单专家失败不拖垮整批 | 对齐路线图 WP-B1；与 W5 timeout 一致 |
| D-W6-3 结果合并 | 返回结构化 `{results:[{expert,status,answer,events}], merged_summary, wall_ms, parallel:true}`；父 harness 继续 verify/re-evidence | 父级仍是唯一 final 作者（W5 evidence-only 哲学） |
| D-W6-4 预算继承 | 并行每专家仍受 `HARNESS_DELEGATE_MAX_TOOL_ROUNDS` + `HARNESS_DELEGATE_EVIDENCE_ONLY` 约束 | 不因并行放大子循环成本 |
| D-W6-5 人数上限 | `HARNESS_PARALLEL_MAX_EXPERTS=3`；入参截断并在 payload 记 `truncated` | 防模型一次扇出过多 |
| D-W6-6 去重 | 同一 `expert` 只保留第一条 subtask；空 subtask 丢弃 | 简单可测 |
| D-W6-7 aux 执行 | 新开关 `ROUTER_AUX_EXECUTION_MODE=off\|serial\|parallel`；默认 **`parallel`**；max aux = min(2, 配置) | 路线图 WP-B3；off 完全回退现状 |
| D-W6-8 aux 时机 | 主循环 **step0 后、首轮 model 决策前** 或与 force-seed 同级：若 aux 非空且 mode≠off → 自动 probe，emit `stage=aux_probe` | 不依赖 LLM 记得去委派 |
| D-W6-9 aux 与 force_delegation | force seed 主路由专家 **之后** 再 aux（避免重复主路由）；aux 不含 primary | 已有 `_normalize_aux_routes` 剔除 primary |
| D-W6-10 aux subtask 生成 | **规则模板**（确定性）：`f"只读补充排查：结合用户问题，从 {aux} 视角收集证据。问题：{message[:400]}"`；不调 LLM 生成 subtask | 时延可控、可测 |
| D-W6-11 事件契约 | 新增 stage 允许：`delegate_parallel_start` / `delegate_parallel_done` / `aux_probe`；`type` 仍用 `agent_event` | 兼容 SSE；评测可 grep |
| D-W6-12 评测 | 新增/改造 ≥1 跨域 case 要求 `require_parallel_delegation` 或时间线含 `delegate_parallel_*`；补 K3 或 N2/M2 逼近 22–23 | 北极星 N6 占比可后置统计 |
| D-W6-13 共享内核 | W6 只写 `plan/2026-07-14-m2-shared-kernel-design.md` 评审纪要（接口草图 + 风险），**不改** `experts/base.py` 主路径 | 降 W6 风险半径 |
| D-W6-14 轨迹导出 | `HARNESS_TRACE_EXPORT_ENABLED` 默认 **false**（本机可开）；complete 时写 `volumes/traces/{session_id}.json`（timeline 摘要，无密钥） | WP-G1 骨架；默认不占盘 |
| D-W6-15 默认开关哲学 | 新能力 **可关**；并行/aux 默认开以便兑现时延；出问题一键 serial/off | 与 M1/M2 一致 |

### 2.1 需用户拍板（建议默认）

| # | 问题 | 建议默认 |
|---|---|---|
| Q1 | 并行工具是独立 `delegate_parallel` 还是扩展 `delegate_to_expert(mode=parallel)`？ | **独立工具**（D-W6-1） |
| Q2 | aux 默认 `parallel` 还是先 `serial` 观察？ | **`parallel`**（max 2）；可用 env 改 serial |
| Q3 | 轨迹导出默认开还是关？ | **关**（false）；progress 本机手动开验证 |
| Q4 | W6 是否包含 shared-kernel **代码**？ | **否**，仅设计评审 |
| Q5 | 是否本周强制 minimal 冲 L1.5 Go？ | **否**；记录对照即可，出口仍 Conditional |

> 若用户未单独回复 Q1–Q5，**按上表建议实施**。有异议请在授权前改本计划决策表。

---

## 3. 范围与非目标

### 3.1 范围内

1. `delegate_parallel` 工具 + registry 注册 + 开关  
2. 并行执行器（gather、per-expert timeout、合并结果）  
3. aux_routes 自动 probe（off/serial/parallel）  
4. 相关单测（独立文件 `tests/test_m2_w6_parallel_delegation.py`）  
5. 评测 case 增量（目标 **≥22**，冲 23）  
6. 可选 trace 导出骨架  
7. progress + AGENTS 索引 + 交接一小节更新  

### 3.2 范围外

见 §1.3。

---

## 4. 实施步骤（可验收）

### Task 0 — 基线确认（只读）

- [ ] 跑 M1/M2 回归子集确认 W5 未脏：

```bash
python -m pytest \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_m2_latency_eval_hardening.py \
  tests/test_harness_verifier.py \
  tests/test_harness_checkpoint.py \
  tests/test_context_integration.py \
  -q --tb=line --no-cov
```

期望：与 W5 一致全绿（约 69+）。

### Task 1 — 配置与文档契约

**Files:** `app/config.py` · `.env.example`

- [ ] 新增：

```text
HARNESS_PARALLEL_DELEGATION_ENABLED=true
HARNESS_PARALLEL_MAX_EXPERTS=3
ROUTER_AUX_EXECUTION_MODE=parallel   # off | serial | parallel
ROUTER_AUX_MAX_PROBES=2
HARNESS_TRACE_EXPORT_ENABLED=false
HARNESS_TRACE_EXPORT_DIR=volumes/traces
```

- [ ] `.env.example` 注释写明回滚：`PARALLEL=false` / `AUX=off` / `TRACE=false`  
- [ ] 非法 `ROUTER_AUX_EXECUTION_MODE` → 回退 `off` 并 log warning  

### Task 2 — 并行委派内核（WP-B1）

**Files:** `app/agent/harness/subagent.py` · `app/agent/harness/registry.py`

- [ ] 抽取共享 `_run_one_delegate(...)`（现有 serial handler 复用），避免复制 timeout/evidence-only 逻辑  
- [ ] 新增 `create_delegate_parallel_tool(...)`：

```python
# 伪代码
async def handler(arguments):
    experts = arguments["experts"]
    subtasks = arguments["subtasks"]
    # zip + 校验长度；截断 max_experts；expert 去重
    if not config.harness_parallel_delegation_enabled:
        return {"status": "failed", "error": "parallel_delegation_disabled"}
    started = perf_counter()
    coros = [_run_one_delegate(expert, subtask, ...) for ...]
    results = await asyncio.gather(*coros, return_exceptions=True)
    # 规范化 exception → status=failed
    return {
        "status": "completed" if any(ok) else "failed",
        "parallel": True,
        "wall_ms": ...,
        "results": [...],
        "truncated": bool,
    }
```

- [ ] Registry：当 `harness_delegation_enabled and harness_parallel_delegation_enabled` 时追加该工具  
- [ ] 工具 description 明确：**只读**；跨域同时查 metric+log 时优先本工具  

### Task 3 — Aux 真执行（WP-B3）

**Files:** `app/agent/harness/loop.py` · 可选 `planner.py`（todo 文案可提 `delegate_parallel`）

- [ ] 在 force-seed 之后、主 for-step 之前调用 `_maybe_run_aux_probes(...)`  
- [ ] 行为矩阵：

| mode | 行为 |
|---|---|
| `off` | 与今日一致（仅 plan/replan hint） |
| `serial` | 按 aux 顺序逐个 `_run_one_delegate` |
| `parallel` | 对 aux[:max] 走与 `delegate_parallel` 相同 gather |

- [ ] emit `agent_event` `stage=aux_probe`（start + done，payload 含 mode/experts/wall_ms）  
- [ ] 将各专家 answer/events **折叠进 messages**（user 或 tool 角色摘要），供后续 model 使用  
- [ ] 预算：若 `state.over_budget` 或 resume_close_only → 跳过 aux  
- [ ] 与 investigation early-close 兼容：aux 成功证据应计入「已有成功工具证据」  

### Task 4 — 可选轨迹导出骨架（WP-G1a）

**Files:** 新建 `app/services/harness_trace_export.py` 或 `app/agent/harness/trace_export.py` · `loop.py` complete 路径

- [ ] 开关 false 时零 I/O  
- [ ] true 时 best-effort 写 JSON：`session_id, trace_id, route, plan, verify, re_evidence_rounds, replan_times, timeline_events(截断), usage, parallel_stats`  
- [ ] 失败只 log，不影响 SSE complete  
- [ ] **禁止**写入 token/密码/完整 `.env`  

### Task 5 — 单测（独立文件）

**File:** `tests/test_m2_w6_parallel_delegation.py`

最低用例：

1. `test_parallel_tool_runs_two_experts_concurrently` — fake experts `asyncio.sleep(0.05)`；wall < 0.09（证明并行非串行）  
2. `test_parallel_disabled_removes_or_rejects_tool`  
3. `test_parallel_respects_max_experts_truncation`  
4. `test_parallel_one_expert_failure_does_not_drop_sibling_success`  
5. `test_aux_mode_off_does_not_invoke_experts`  
6. `test_aux_mode_parallel_emits_aux_probe_and_invokes_aux_only`  
7. `test_aux_mode_serial_order`（recording 时间戳或 call order）  
8. `test_shared_delegate_round_cap_still_forwarded`（W5 回归）  
9. （若做 G1a）`test_trace_export_noop_when_disabled`  

- [ ] `python -m pytest tests/test_m2_w6_parallel_delegation.py -q --no-cov` 全绿  
- [ ] 再跑 Task 0 子集 + 本文件，无回归  

### Task 6 — 评测扩面（WP-F3a）

**Files:** `evals/oncall/cases.jsonl` · `evals/oncall/README.md` · `scripts/evaluate_oncall_local.py`（若需新 expected 键）

- [ ] 补题优先序：  
  1. **P-parallel-cross-domain**（或改造 S4/S5 notes）：跨域问法，`expected.require_parallel_event` 或 `must_use_tools_any_of` 含 `delegate_parallel`  
  2. **K3** 或 **N2** 或 **M2** 三选二，使总数 **≥22**（冲 23）  
- [ ] eval 脚本：若 `require_parallel_event`，时间线需出现 `delegate_parallel_*` 或 `aux_probe` 且 `parallel=true`  
- [ ] README 覆盖表更新  

### Task 7 — 共享内核设计评审（文档 only）

**File:** `plan/2026-07-14-m2-shared-kernel-design.md`

- [ ] 写清：子 agent = harness 配置（system/tools/max_steps/timeout）草图  
- [ ] 与现 `ToolCallingExpert` 对照表、迁移开关 `HARNESS_SHARED_KERNEL_DELEGATION`、风险（事件形状、timeout 嵌套、W5 evidence-only）  
- [ ] **不写业务代码**  

### Task 8 — Live 验证与 progress

- [ ] `/health` 200；MCP cls/monitor 可用  
- [ ] 单题：构造 metric+log 跨域问法，确认时间线有并行事件且 wall 合理  
- [ ] （可选）`python scripts/evaluate_oncall_local.py --suite minimal --timeout-extra 90`  
- [ ] 写 `plan/2026-07-14-m2-w6-parallel-delegation-progress.md`：命令、pass 数、live 文件名、P50 对照、已知限制  
- [ ] 更新 `docs/pilot/handoff-2026-07-14-l15-conditional.md` 一节「M2 W6」或追加 §  
- [ ] 更新 `AGENTS.md` Current Plan Index 状态  

---

## 5. 文件清单

| 文件 | 动作 |
|---|---|
| `app/config.py` | 新开关 |
| `.env.example` | 文档化 |
| `app/agent/harness/subagent.py` | 抽取共用 + `delegate_parallel` |
| `app/agent/harness/registry.py` | 注册并行工具 |
| `app/agent/harness/loop.py` | aux probe 钩子；可选 trace |
| `app/agent/harness/planner.py` | 可选：aux todo 提示 parallel |
| `app/services/harness_trace_export.py`（或 harness 内） | 可选导出 |
| `tests/test_m2_w6_parallel_delegation.py` | **新建** |
| `evals/oncall/cases.jsonl` | +1~3 case |
| `evals/oncall/README.md` | 覆盖表 |
| `scripts/evaluate_oncall_local.py` | 可选 parallel 断言 |
| `plan/2026-07-14-m2-shared-kernel-design.md` | 设计评审 |
| `plan/2026-07-14-m2-w6-parallel-delegation-progress.md` | 实施后 |
| `docs/pilot/handoff-2026-07-14-l15-conditional.md` | 收尾更新 |
| `AGENTS.md` / `CLAUDE.md` | 索引与焦点一句 |

---

## 6. 开关一览

| 开关 | 默认 | 回滚/说明 |
|---|---|---|
| `HARNESS_PARALLEL_DELEGATION_ENABLED` | `true` | `false` → 不注册/拒绝并行工具 |
| `HARNESS_PARALLEL_MAX_EXPERTS` | `3` | 降到 `1` 近似禁用有效并行 |
| `ROUTER_AUX_EXECUTION_MODE` | `parallel` | `off` 完全回到 W5 行为 |
| `ROUTER_AUX_MAX_PROBES` | `2` | `0` 等同 off |
| `HARNESS_TRACE_EXPORT_ENABLED` | `false` | `true` 仅本机/调试 |
| `HARNESS_TRACE_EXPORT_DIR` | `volumes/traces` | 路径可配 |
| （继承 W5）`HARNESS_DELEGATE_MAX_TOOL_ROUNDS` | `1` | 并行子专家同样生效 |
| （继承 W5）`HARNESS_DELEGATE_EVIDENCE_ONLY` | `true` | 同上 |
| （继承）`HARNESS_DELEGATION_ENABLED` | `true` | 总闸；false 无任何委派工具 |

---

## 7. 验证方式

### 7.1 单测

```bash
python -m pytest tests/test_m2_w6_parallel_delegation.py -q --no-cov
# + Task 0 全量 M1/M2 子集
```

**出口**：新文件全绿；旧子集无新增失败。

### 7.2 行为验收

| 场景 | 期望 |
|---|---|
| 并行工具 2 专家 sleep | wall < 串行之和的 70% |
| `PARALLEL=false` | 无并行工具或明确 failed |
| aux=off | 无 `aux_probe` 执行 |
| aux=parallel + router 返回 aux | 时间线 `aux_probe` + 子专家事件 |
| 只读红线 | 答案不得「已重启/已回滚」 |
| 变更 | 仍声明 missing_change_datasource |

### 7.3 Live（有服务时）

```bash
# 健康
# GET http://127.0.0.1:9900/health

python scripts/evaluate_oncall_local.py --case S4-service-down --timeout-extra 90
# 或新建 parallel case

python scripts/evaluate_oncall_local.py --suite minimal --timeout-extra 90
```

**出口（W6）**：不强制 L1.5 Go；要求 progress 记录 pass/total、P50、是否出现并行事件。  
**加分**：P50 相对 W5 对照下降。

---

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 并行放大 LLM/MCP 并发打满 | max_experts=3；per-expert timeout；evidence-only 1 轮 |
| aux 自动执行增加固定成本 | mode=off/serial；max_probes=2；over_budget 跳过 |
| 与 investigation early-close 竞态 | aux 在主 loop 前完成；证据计入 early-close 判定 |
| 模型不用 `delegate_parallel` | aux 自动 probe 兜底；评测可 require 事件 |
| SSE 前端不识新 stage | stage 新增允许；type 不变；前端可忽略 |
| 轨迹文件含敏感信息 | 默认关；payload 脱敏；不写密钥 |
| 范围 creeping 到 shared kernel | Task 7 文档 only；代码评审拒收 base.py 大改 |

**紧急回滚**：

```text
HARNESS_PARALLEL_DELEGATION_ENABLED=false
ROUTER_AUX_EXECUTION_MODE=off
HARNESS_TRACE_EXPORT_ENABLED=false
```

行为应回到 W5 串行委派 + aux 仅 hint。

---

## 9. 与路线图 / 后续周对齐

| 周 | 本计划关系 |
|---|---|
| W5 | 已完成时延/评测硬化；本计划 **承接** 原路线图 W5 未做的 B1 骨架 |
| **W6（本计划）** | B1 可用 + B3 真执行 + F3 补题 + G1 骨架 + 共享内核设计 |
| W7 | WP-B2 共享内核实现、变更源 A/B 决策、WP-D1 草案 |
| W8 | 合并去重、全量 ≥19/23、P50≤85、**L2 出口评审** |

---

## 10. 授权口令

实现前需要用户明确其一：

- 「**按 W6 计划实现**」  
- 「**开始做 M2 W6**」  
- 「**授权改代码**」（且指向本计划）

未授权前：**只更新本计划/索引，不改 `app/agent/harness/*` 业务逻辑**。

---

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-14 | 初稿：基于 L1.5 Conditional 交接 + W5 进度 + 路线图 §3；待用户批准 |
