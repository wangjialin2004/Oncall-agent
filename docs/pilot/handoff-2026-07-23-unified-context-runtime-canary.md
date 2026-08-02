# OnCall Agent 交接文档 — 统一上下文 live canary 与运行时收口

> **交接日期**：2026-07-23  
> **工作树**：`codex/publish-current-worktree`  
> **当前 HEAD**：`c4aabef`（已提交 rollout 文档）；后续代码/测试改动仍在 dirty worktree  
> **本棒主题**：live schema v3 + compact projection apply + `.env` unified canary；动态 plan；白板工具去冗余；端到端验证与 AGENTS 规则补强  
> **产品状态**：L3 Conditional 不变；上下文数据面已 live canary，**代码默认仍 false**  
> **目标读者**：接手默认 true 决策、P50/P95、stateful 旧路径清理、续聊/工具体验的工程同学  
> **前序交接**：[统一上下文加载与持久化（2026-07-19）](./handoff-2026-07-19-unified-context-repository.md)  
> **计划 / 进度**：[统一上下文仓储](../../plan/2026-07-19-unified-context-repository.md) · [进度](../../plan/2026-07-19-unified-context-repository-progress.md)

---

## 0. 30 秒结论

| 项 | 状态 |
|---|---|
| Live schema | ✅ **v3**（`context_projection_*` + `commit_id`） |
| Compact projection apply | ✅ 62 会话处理完；JSON 约 **-76%**；recent/patch 重复清零 |
| Unified canary | ✅ `.env` `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=true` |
| 代码 / example 默认 | 🔒 仍为 **false**（一键回滚） |
| Redis | ✅ 可达；旧 v1 cache miss 回源；**未删 key** |
| 动态 plan | ✅ `.env` `HARNESS_LLM_PLANNING_ENABLED=true`，模型 `gpt-5.6-luna` |
| 白板工具 | ✅ 与 stateful 存储解耦；**白板已注入时默认不挂 `context_read`** |
| Stateful 开关 | ⚠️ **未删除**；unified 下只选 structured renderer |
| 自动化 E2E | ✅ API→commit→第二轮回载；focused / soft-path 通过 |
| Live E2E | ✅ 两轮 complete + compact + 续聊主题正确；部分工具（prometheus 等）仍可能降级 |
| 工作树 | ⚠️ dirty；含本棒未提交代码/测试；禁止 `reset --hard` / 整树 `git add -A` |

**接手人一句话**：统一上下文已在 live canary 跑通（schema v3 + compact + 原子 commit）；动态 plan 与模型已切到 `gpt-5.6-luna`；`context_read` 冗余调用已默认关掉。下一步是默认 true 决策、性能对照，以及旧 dual-path 存储代码的退役计划——**不要整包删除 stateful**。

---

## 1. 必读文档

| 优先级 | 文档 | 用途 |
|---|---|---|
| **P0** | 本文 | 当前 live 状态、开关、E2E 证据、红线、下一棒 |
| **P0** | [2026-07-19 交接](./handoff-2026-07-19-unified-context-repository.md) | 架构心智、迁移策略、原子 commit 细节 |
| **P0** | [进度与证据](../../plan/2026-07-19-unified-context-repository-progress.md) | 命令与 live 数字 |
| **P0** | [运行架构](./context-dual-path.md) | single repository / dual renderer |
| **P0** | [AGENTS.md](../../AGENTS.md) | **含新增：功能完成必须做端到端测试** |
| P1 | [状态化上下文原计划](../../plan/2026-07-08-stateful-agent-context.md) | 旧 stateful 设计；勿再当主存储路径扩展 |

---

## 2. 本棒完成了什么

### 2.1 Live 数据面四道门（已执行）

| Gate | 动作 | 结果 |
|---|---|---|
| 0 | 只读 audit / dry-run | 基线 62 会话 / 57 turns |
| 1 | `migrate_database.py up` → schema v3 | current=3，row counts 不变 |
| 2 | `migrate_context_projection.py apply` | compact 22 / rebuild 10 / clear 30 |
| 3 | Redis 只读聚合 | 8 context keys，schema v1，未删除 |
| 4 | `.env` canary true | unified 运行时生效 |

备份目录：`volumes/backups/context-v3/`（含 schema-v3-pre-apply 时间戳副本）。

### 2.2 运行时配置变更（`.env`，勿提交）

```text
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=true
HARNESS_STATEFUL_CONTEXT_ENABLED=true
HARNESS_LLM_PLANNING_ENABLED=true
LLM_MODEL=gpt-5.6-luna
LLM_PLANNER_MODEL=gpt-5.6-luna
LLM_REASONER_MODEL=gpt-5.6-luna
# 默认行为（代码默认）：
# HARNESS_CONTEXT_TOOLS_ENABLED=true
# HARNESS_CONTEXT_READ_WHEN_VIEW_INJECTED=false
```

代码 / `.env.example` 中 **unified 默认仍为 false**。

### 2.3 白板工具分离（防冗余 `context_read`）

**问题**：unified structured renderer 已把「当前会话白板」+ turn history 注入 prompt，但模型仍被注册 `context_read`，续聊时会再调一次「读取上下文」，过程栏出现 low/需关注噪声。

**处理**：

- `context_read` / `context_note` / `read_attachment` 绑定**当前白板对象**，不再等同于 “stateful 存储路径专属工具”。
- 当 whiteboard 已注入 prompt 时，默认 **不注册 `context_read`**。
- 仍注册：`context_note`、`read_attachment`。
- 调试可开：`HARNESS_CONTEXT_READ_WHEN_VIEW_INJECTED=true`。

关键文件：

- `app/agent/context/tools.py`
- `app/agent/harness/registry.py`
- `app/agent/harness/stream_inner.py`
- `app/config.py`（`harness_context_read_when_view_injected`，默认 false）

### 2.4 Stateful 是否删除？

**没有删除。**

| 层 | 状态 |
|---|---|
| 旧 dual-path 加载/双写 | unified=true 时主路径不再使用；代码保留作回滚 |
| `HARNESS_STATEFUL_CONTEXT_ENABLED` | 仍 true；unified 下只选 structured renderer |
| `AgentContextState` 白板结构 | 仍是 runtime / projection 模型 |
| `context_note` | 保留，写模型笔记到白板 |
| 整包删 stateful 模块 | **禁止**；等默认 true 稳定后再做退役计划 |

### 2.5 动态 plan

- 默认规则模板仍作兜底。
- `HARNESS_LLM_PLANNING_ENABLED=true` 时 `LightweightPlanner.acreate` 用 LLM 精炼 todos / required_evidence。
- 失败回退规则 plan，不打断主循环。
- 中途 replan 仍主要是规则修订。

### 2.6 AGENTS 规则补强

`AGENTS.md` 已要求：完成功能或改变运行时/API/harness/上下文/路由/工具/前端/开关/schema 的项目变更，**完成前必须做端到端测试**（自动化路径 e2e + 适用时的 live/续聊 e2e + 证据记录）。

---

## 3. 当前架构心智（canary 运行时）

```text
Authenticated request
  -> ContextRepository.load_envelope()          # 唯一加载
       SQLite compact projection + turn window
       Redis committed cache (watermark 全匹配才命中)
  -> RenderPolicy
       stateful=true  -> Structured（# 当前会话白板 + history）
       stateful=false -> Legacy summary/window
  -> Tools
       domain tools + context_note + read_attachment
       context_read 默认不挂（白板已注入）
  -> Planner
       rule base -> optional LLM refine (flag true)
  -> complete 前
       同一 SQLite 事务：insert turn + update compact projection + watermark
       再 best-effort Redis committed / 清 inflight
```

---

## 4. 验证证据（2026-07-23）

### 4.1 自动化

```bash
# 进程级 canary 勿污染脚本化 FakeLLM：测试内强制 planning/unified 隔离
HARNESS_LLM_PLANNING_ENABLED=false \
LONG_TERM_MEMORY_DISTILL_ENABLED=false \
HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=false \
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_context_unified_wiring.py \
  tests/test_context_repository.py \
  tests/test_context_projection.py \
  tests/test_harness_stateful_context.py \
  tests/test_context_tools.py \
  -q --no-cov
# 近期：39 / 23 等 focused 批通过

PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_harness_service.py \
  -k 'two_turn or history or attachment' -q --no-cov
# 16 passed, 51 deselected
```

核心 E2E 用例：

`tests/test_context_unified_wiring.py::test_assistant_two_turn_unified_e2e_persists_compact_projection`

覆盖 API → harness → 原子 commit → 第二轮 history 回载 → compact projection。

### 4.2 Live E2E（真实后端）

| 项 | 结果 |
|---|---|
| readiness | ready |
| 示例 session | `e2e-full-246cd48fe0` 等 |
| 两轮 complete | ✅ |
| planning 阶段 | ✅ 动态 todos 可见 |
| 续聊主题 | ✅（如「Redis 内存过高」） |
| turns + commit_id | ✅ 2 轮均有 |
| projection | ready / compact v2 / recent=0 / patch_tail=0 |
| 工具健康 | ⚠️ 部分 prometheus/monitor 工具可能失败 → medium 证据缺口提示 |

LLM 网关曾对旧模型 `gpt-5.4` 返回 503；已切 `gpt-5.6-luna` 后探测与 live 对话恢复可用。

### 4.3 Compact 前后量级

| 指标 | apply 前 | apply 后 |
|---|---:|---:|
| projection JSON 总字符 | ~486k | ~118k（约 -76%） |
| recent_turn entries | >0 | 0 |
| patch_tail entries | >0 | 0 |

---

## 5. 关键文件地图

| 区域 | 路径 | 接手关注 |
|---|---|---|
| Compact projection | `app/agent/context/projection.py` | v2 序列化；无 recent/patch/identity scope |
| Repository | `app/services/context_repository.py` | load_envelope / atomic commit / cache |
| Unified glue | `app/agent/context/unified.py` | prepare / committer / inflight |
| Renderers | `app/agent/context/renderers.py` | structured vs legacy，只消费 envelope |
| 白板工具 | `app/agent/context/tools.py` | note/attachment；read 条件注册 |
| Harness 挂载 | `app/agent/harness/stream_inner.py`、`registry.py` | unified load；`whiteboard_injected=True` |
| Planner | `app/agent/harness/planner.py` | rule + optional LLM |
| Schema v3 | `app/services/database_migration_service.py` | additive only |
| Operator | `scripts/migrate_database.py`、`migrate_context_projection.py`、`audit_context_storage.py` | apply 需 backup |
| E2E 测试 | `tests/test_context_unified_wiring.py` 等 | canary 下需隔离 planning/unified |

---

## 6. 红线

### 禁止（未单独授权）

- 把代码 / `.env.example` 默认 unified 改为 true  
- Redis `FLUSHDB` / 无清单删 context key  
- destructive schema down / 在线猜测逐行回写 projection  
- 根据旧 snapshot 伪造 `conversation_turns`  
- 整包删除 stateful / ContextStateStore / AgentContextState  
- 把 `.env`、DB、backup、token 提交进 git  
- `git reset --hard` / 整树 `git add -A`（工作树混有用户与多棒改动）

### 行为回滚

```text
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false
# 仅需关动态 plan：
HARNESS_LLM_PLANNING_ENABLED=false
# 仅需恢复冗余 context_read：
HARNESS_CONTEXT_READ_WHEN_VIEW_INJECTED=true
```

数据回滚：停写 → 恢复 `volumes/backups/context-v3/` 中 verified backup；**不删 canonical turns**。

---

## 7. 下一棒 SOP

1. **默认 true 决策包**  
   - two-turn / 写放大 / commit P50-P95 对照  
   - Redis 旧 v1 key 是等 TTL 还是定向清理（禁止 FLUSHDB）  
   - 独立批准后才改 `app/config.py` 与 `.env.example`

2. **Stateful 退役（文档+代码，勿混 canary）**  
   - 明确：删的是 dual-path 存储，不是白板 renderer  
   - 可考虑重命名开关 → structured renderer  
   - 旧 store 主路径标 deprecated，有稳定 canary 后再删

3. **工具/证据体验**  
   - 续聊 plan 避免把「上一轮原文」写成硬 required_evidence  
   - verifier 对已注入 history 的缺口文案可再收紧

4. **测试卫生**  
   - 进程 `.env` canary 会污染 FakeLLM；测试必须 monkeypatch  
     `harness_unified_context_repository_enabled` / `harness_llm_planning_enabled`  
   - 未提交的 router `previous_route` 兼容与测试隔离改动应随功能提交

5. **工作树提交策略**  
   - 按主题拆 commit；排除 `.env`、`volumes/`、`*.db`、`server.log`、pid  
   - 已提交：`c4aabef docs: record live unified context rollout canary`

---

## 8. 交接检查清单

- [x] Live schema v3 up + verify  
- [x] Compact projection apply + 精确水位对账  
- [x] Redis 只读聚合（未删）  
- [x] `.env` unified canary + 后端重启 ready  
- [x] 动态 plan 开启并实测 todos 与规则模板不同  
- [x] 模型切换 `gpt-5.6-luna` 且 LLM probe 通过  
- [x] 自动化 E2E + soft-path  
- [x] Live 两轮 E2E（complete / compact / 续聊）  
- [x] `context_read` 在白板已注入时默认不注册  
- [x] AGENTS 增加功能完成必须 E2E 的规则  
- [x] 本交接文档  
- [ ] 默认 true 独立批准  
- [ ] P50/P95 / 写放大正式对照  
- [ ] 旧 dual-path 存储退役计划  
- [ ] dirty worktree 按主题提交并推送（按需）  
- [ ] 历史 test-session experience 数据治理（独立授权）

---

## 9. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-19 | 统一仓储代码与本地门禁；flag 默认 false |
| 2026-07-23 | live schema v3 + compact apply + Redis 审计 + `.env` canary |
| 2026-07-23 | 模型 `gpt-5.6-luna`；动态 plan 开启 |
| 2026-07-23 | 自动化 + live E2E；AGENTS E2E 规则 |
| 2026-07-23 | 白板工具与 stateful 存储解耦；注入后默认无 `context_read` |
| 2026-07-23 | 形成本文作为当前上下文/运行时交接入口 |
