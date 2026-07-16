# M1 W2 实施计划：Context 刷新 · Checkpoint 可靠 · 证据细匹配 · CI 骨架

> **角色边界**：本文件描述「怎么做」。用户指示进入 W2 后，按本计划实现。  
> **日期**：2026-07-13  
> **状态**：**已实施主干**（见 [progress](./2026-07-13-m1-w2-progress.md)）  
> **上级**：
> - [3 个月路线图](./2026-07-13-complete-agent-system-3-month-roadmap.md) §2.2 / §10
> - [M1 W1 计划](./2026-07-13-m1-w1-close-the-loop-implementation.md)（已落地）
> - [状态化上下文计划](./2026-07-08-stateful-agent-context.md) 未勾选项
> - [CLAUDE.md](../CLAUDE.md)（先计划后编码）

---

## 0. 一页摘要

| 项 | 内容 |
|---|---|
| 主题 | 多轮白板不空心 + 超时可续跑 + 计划证据可核对 + 最小 CI |
| 范围 WP | **WP-CTX1** · **WP-I1** · **WP-A2** · **WP-F2** ·（可选轻量 **WP-L1a**） |
| 明确不做 | replan（WP-A3）、并行委派、共享内核、自动蒸馏、全量 18 题补齐 |
| 出口 | 单测绿 + `make ci-smoke` 可跑 + 多轮/恢复/证据匹配可演示 |

---

## 1. Context（现状与问题）

### 1.1 代码事实（2026-07-13 核对）

| 点 | 现状 | 差距 |
|---|---|---|
| `stamp_recent_turns` | 仅在 `_rebuild_context_state_from_legacy`（cold miss）调用 | 正常 happy path **每轮结束不刷新** recent_turns |
| `last_answer_summary` | final 时 framework_patch 有写 | 但 **recent_turns 无本轮 Q/A** |
| Checkpoint timeout | 外层 `TimeoutError` 只走 fallback，**不** `save_step` | 超时后 resume 可能无盘 |
| Checkpoint ref | `save_step` 已写 `context_version/ref` | resume **未 rehydrate** ContextState（仅靠 store 正常 get） |
| 幂等白名单 | 默认几乎只有 `delegate_to_expert` | 只读 query 类 resume 易 close-only |
| stateful 路径 | `persist_messages=False` | resume 依赖 messages 时可能空；需保证 context store 可用 |
| Verifier | 仅「有/无成功 tool」；`required_evidence` 只在 **零证据** 时整表 gaps | 不按证据类型细匹配工具 |
| CI | 有 `make test`，**无**轻量 `ci-smoke` / GitHub Actions | 门禁弱 |

### 1.2 问题 → WP

| ID | 问题 | WP |
|---|---|---|
| P1 | 多轮会话白板 `recent_turns` 空心 | WP-CTX1 |
| P2 | 总超时不落 checkpoint | WP-I1-a |
| P3 | resume 不显式 rehydrate / 幂等过窄 | WP-I1-b/c |
| P4 | 计划证据类型与工具不对齐 | WP-A2 |
| P5 | 无最小 CI 命令 | WP-F2 |

### 1.3 非目标（W2 不做）

1. WP-A3 mid-loop replan  
2. 并行委派 / aux 执行（M2）  
3. 专家共享内核（M2）  
4. 全量补到 18/23 题（可顺带 +1～2，不阻塞）  
5. 真 LLM 时延优化大改（仅可选配置文档 **WP-L1a**）  
6. 自动处置 / HITL 执行器  

---

## 2. 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| D-W2-1 何时 stamp turns | **每轮 complete 前** 追加本轮 user + assistant 摘要；非 cold-only | 解决空心 |
| D-W2-2 stamp 内容 | `[{role,content}…]`，assistant 截断 600 字（对齐 last_answer_summary） | 控 token |
| D-W2-3 timeout 落盘 | 外层 timeout **best-effort** `save_step`（step≥1 或强制 step=max(1,state.step)） | 可 resume |
| D-W2-4 rehydrate | resume 时若有 `context_snapshot_ref`，**ensure** 走 `build_stateful_context`（已有 store 路径）；并在 resume 事件 payload 带 ref/version | 不另起存储 |
| D-W2-5 幂等白名单 | 扩大只读工具：`query_prometheus_alerts`、`retrieve_knowledge`、`recall_experience`、`lookup_service_knowledge`、`check_redis_health`、`get_current_time`、`query_recent_changes`、`context_read`、`context_note`、`read_attachment`、`delegate_to_expert` | 保守 replay 更有用 |
| D-W2-6 证据细匹配 | 规则表：证据类型关键词 → 工具名集合；缺类则 gap；**默认观测模式可严格** | 默认 `strict=false` 时：有成功工具但缺类 → degraded+gap；strict=true 同行为（W2 先落地匹配逻辑，开关预留） |
| D-W2-7 CI | `make ci-smoke`：pytest 选中子集 + 前端 test（若 node 可用则跑，否则 skip 注明） | 无强依赖 GH Actions 也可本地门禁 |

---

## 3. 目标行为

### 3.1 WP-CTX1 多轮刷新

```text
stream 成功路径 complete 之前：
  if stateful_ctx:
    stamp_recent_turns(
      turns = 原 recent_turns（截断保留尾部 N）
             + {role:user, content: message}
             + {role:assistant, content: final_answer[:600]}
      last_turn_index += 1
    )
    framework_patch last_answer_summary（已有则保留）
    persist_stateful_context(persist_snapshot=True)
```

下一轮 `build_stateful_context` Redis hit 时 view 中应能看到上一轮 Q/A。

### 3.2 WP-I1 Checkpoint

```text
stream() except TimeoutError:
  best-effort:
    _schedule_checkpoint_save(
      state, messages=last_known_messages or [],
      step_index=max(1, state.step),
      tool_calls=[],
      context_state=...
    )
  then existing fallback...

resume 事件 payload 增加:
  context_version, context_snapshot_ref（若有）

_DEFAULT_TOOLS 扩大只读集合（config 可覆盖可选，W2 先改默认元组）
```

**难点**：外层 `stream()` 当前拿不到 inner 的 `state/messages`。  
**做法**：在 `HarnessService.stream` 用可变 holder（dict）由 `_stream_inner` 持续写入 `state`/`messages`/`context_state`，timeout 时读取 holder 落盘。

### 3.3 WP-A2 证据细匹配

在 `verifier.py`：

```text
EVIDENCE_TOOL_MAP = {
  "指标": {"query_prometheus_alerts", "check_redis_health", ...},
  "告警": {"query_prometheus_alerts", ...},
  "日志": {工具名含 log, analyze 等},
  "变更": {"query_recent_changes"},
  "知识": {"retrieve_knowledge", "recall_experience", "lookup_service_knowledge"},
  ...
}
对 plan.required_evidence 每条：
  解析匹配工具集合
  若成功 tool_event 的 tool 名与集合无交集 → gaps.append("缺少证据类型：…")
有成功工具但仍有类型缺口 → degraded / confidence 不高于 medium
```

开关：`HARNESS_EVIDENCE_MATCH_ENABLED` 默认 `true`。

### 3.4 WP-F2 CI

```makefile
ci-smoke:
  python -m pytest tests/test_m1_close_the_loop.py tests/test_m1_w2_*.py \
    tests/test_harness_verifier.py tests/test_context_integration.py \
    tests/test_harness_checkpoint.py -q --no-cov
  # optional frontend if npm present
```

可选：`.github/workflows/ci-smoke.yml`（windows/ubuntu 其一）。

---

## 4. 实施步骤

| Step | WP | 文件 | 验收 |
|---|---|---|---|
| 0 | 计划 | 本文件 + 索引 | 可独立阅读 |
| 1 | WP-A2 | `verifier.py`、`config.py`、单测 | 细匹配 gaps 单测 |
| 2 | WP-CTX1 | `loop.py`、`integration` 可复用 stamp | 多轮 stamp 单测 / harness 级 |
| 3 | WP-I1 | `loop.py` holder + timeout save；`harness_checkpoint.py` 白名单 | timeout 落盘单测；白名单单测 |
| 4 | WP-F2 | `Makefile`、可选 workflow | `make ci-smoke` 绿 |
| 5 | 进度 | `plan/*-progress.md`、AGENTS | 状态回填 |

顺序建议：**A2（小）→ CTX1 → I1（中）→ F2 → 文档**。

---

## 5. 开关

| 开关 | 默认 | 说明 |
|---|---|---|
| `HARNESS_EVIDENCE_MATCH_ENABLED` | `true` | 计划证据类型细匹配 |
| （既有）checkpoint / stateful / re_evidence | 不变 | W1 保持 |

幂等白名单 W2 以代码默认元组扩展为主；如需配置化可加 `HARNESS_CHECKPOINT_IDEMPOTENT_TOOLS`（逗号分隔，可选，非必须）。

---

## 6. 文件清单

| 动作 | 路径 |
|---|---|
| 改 | `app/agent/harness/loop.py` |
| 改 | `app/agent/harness/verifier.py` |
| 改 | `app/services/harness_checkpoint.py` |
| 改 | `app/config.py` / `.env.example` |
| 改 | `Makefile` |
| 增 | `tests/test_m1_w2_context_checkpoint.py`（或拆分） |
| 增 | `tests/test_m1_w2_evidence_match.py` |
| 可选 | `.github/workflows/ci-smoke.yml` |
| 增 | 本计划 + progress |

---

## 7. 验证

```bash
python -m pytest tests/test_m1_w2_evidence_match.py tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_close_the_loop.py tests/test_harness_verifier.py \
  tests/test_harness_checkpoint.py tests/test_context_integration.py -q --no-cov

make ci-smoke
```

**出口标准**

- [ ] 每轮 complete 后 stateful `recent_turns` 含本轮 user/assistant
- [ ] 外层 timeout 会 best-effort checkpoint（可单测 mock store）
- [ ] 只读工具在幂等白名单内
- [ ] required_evidence 缺类产生明确 gap
- [ ] `make ci-smoke` 本地可跑绿
- [ ] W1 回归不破

---

## 8. 风险

| 风险 | 缓解 |
|---|---|
| timeout holder 竞态 | holder 只写引用；timeout 只读 |
| recent_turns 膨胀 | 保留尾部最多 12 条 turn dict |
| 证据映射过严误伤 | 映射宁宽；未知类型不强制 gap |
| 扩大幂等白名单误 replay | 仍默认 `checkpoint_replay=false`；白名单只影响「可否 replay」判断 |
| persist_messages=False + redis 丢 context | rehydrate 依赖 context store；timeout 仍尽量带 version/ref |

---

## 9. 与 W1 / 路线图关系

- W1：re-evidence / force_delegation / 变更 gap — **保持**  
- W2：补「状态与恢复可信」+「证据语义」+「门禁」  
- W3–W4：replan、时延对照、评测 ≥18  

---

## 10. 一句话

> **W2 让多轮白板有记忆、超时跑得可续、计划证据对得上工具，并用 `make ci-smoke` 锁住回归。**
