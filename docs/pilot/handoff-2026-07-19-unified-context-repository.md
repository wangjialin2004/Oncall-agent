# OnCall Agent 交接文档 — 统一上下文加载与持久化

> **交接日期**：2026-07-19  
> **工作树**：`codex/publish-current-worktree`  
> **当前 HEAD**：`634851f`；本棒实现仍在 dirty worktree，尚未提交  
> **本棒主题**：合并 structured / legacy 上下文加载与保存生命周期，建立 single repository、dual renderer 和原子 turn/projection commit  
> **产品状态**：L3 Conditional 不变；本棒只完成 gated 数据面能力，尚未完成 live rollout  
> **目标读者**：接手 live schema v3、projection migration、Redis 审计和 canary 的工程同学  
> **本棒计划**：[上下文加载与持久化合并](../../plan/2026-07-19-unified-context-repository.md)  
> **实施进度**：[进度与验证证据](../../plan/2026-07-19-unified-context-repository-progress.md)  
> **最新复审**：[统一上下文仓储修复复审](../reviews/completion-review-2026-07-19-unified-context-repository-2.md)

---

## 0. 30 秒结论

| 项 | 状态 |
|---|---|
| 两套加载模式 | ✅ unified 开关开启时共用一次 `ContextRepository.load_envelope`，只切换 renderer |
| 两套长期持久化生命周期 | ✅ 代码已收敛为一个 terminal commit owner；turn + projection 同一 SQLite 事务 |
| 完整会话审计源 | ✅ 只保留在 `conversation_turns`；projection 不再复制 recent-turn 全文 |
| Compact projection v2 | ✅ 删除重复 identity scope、recent-turn 文本和 durable `patch_tail` |
| Redis committed / inflight | ✅ 代码语义已分离；inflight 仅显式 checkpoint resume 可读 |
| 首次 completion review | ✅ 6 个 P1、2 个 P2 与 scoped Ruff 问题均关闭 |
| 本地门禁 | ✅ 115 focused、23 soft-path、49 ci-smoke；compile/lint/size/diff 全通过 |
| Live SQLite | 🟡 current schema v2、pending `[3]`；只读 audit/dry-run 已通过 |
| Redis 实例审计 | ⬜ 当前实例不可达；只验证 fail-fast blocked 行为 |
| Canary / 性能收益 | ⬜ 未执行；必须在 schema/apply/Redis 门后单独批准 |
| 统一开关默认值 | 🔒 `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false`，必须保持 |
| 工作树 | ⚠️ 包含本棒和用户/前序未提交改动；禁止 reset、checkout 或整树 `git add -A` |

**接手人一句话**：代码和本地项目流程已经完成，但 live 数据面仍在 schema v2；先保持统一开关关闭，依次通过 schema v3、projection apply、Redis 聚合和新会话 canary 四道门，任何一道都不能合并授权。

## 1. 必读文档

| 优先级 | 文档 | 用途 |
|---|---|---|
| **P0** | 本文 | 当前状态、架构、红线和下一棒 SOP |
| **P0** | [完整实施计划](../../plan/2026-07-19-unified-context-repository.md) | 决策、迁移策略、验收标准和回滚 |
| **P0** | [进度与证据](../../plan/2026-07-19-unified-context-repository-progress.md) | 实际命令、测试结果、live 只读数据 |
| **P0** | [最新 completion review](../reviews/completion-review-2026-07-19-unified-context-repository-2.md) | 已关闭问题和剩余 blocker |
| **P0** | [运行架构](./context-dual-path.md) | single repository / dual render policy 运维说明 |
| **P0** | [AGENTS.md](../../AGENTS.md) | 计划、授权、数据安全和测试要求 |
| P1 | [状态化上下文原计划](../../plan/2026-07-08-stateful-agent-context.md) | 原始 Redis/DB snapshot 设计与审批约束 |
| P1 | [上下文去重与缩减](../../plan/2026-07-19-context-dedup-reduction.md) | prompt/view 去重，与本棒存储合并相邻但独立 |

## 2. 为什么要合并

原系统不是简单保存了两份相同字符串，而是三类数据职责和两套运行生命周期交叉：

1. `conversation_turns` 保存完整 user/assistant/events/attachments，是不可替代的审计记录。
2. `context_state_json` 保存供模型使用的结构化工作状态，但旧版还复制了 recent-turn 文本和大量 patch 元数据。
3. Redis 同时承担热状态和未完成运行恢复，committed 与 inflight 边界不清楚。

同时：

- Stateful 模式从 Redis/SQLite snapshot/turns 加载。
- Legacy 模式通过 `ContextBuilder` 再读 turns/summary。
- Harness 在工具阶段反复保存整份 snapshot。
- API 在最终 complete 后另开事务追加 turn。

结果是模式切换会更换数据源，turn 与 snapshot 可能相互超前或落后，失败时还可能出现统一提交之后再走 legacy append 的双写风险。

本棒批准并实现的方向是：**完整 turn 作为唯一审计源，projection 作为可重建派生状态，Redis 只做 cache/recovery；两种上下文体验共用 repository，只保留 renderer 差异。**

## 3. 当前架构心智模型

```text
Authenticated request
  -> RequestContext.storage_owner_key + session_id
  -> ContextRepository.load_envelope()             # 唯一加载入口
       |-> SQLite conversation/projection metadata # 权威数据
       |-> Redis committed cache                    # 必须匹配 DB 水位
       |-> conversation_turns LIMIT window          # 运行时历史，不进 projection
       |-> shared reducer                            # 仅补 committed turns
  -> ContextEnvelope
       |-> StructuredRenderPolicy  (stateful=true)
       `-> LegacyRenderPolicy      (stateful=false)
  -> Harness runtime state
       |-> memory-only patches
       `-> Redis inflight           # 仅 checkpoint/timeout，按 version 合并
  -> terminal complete, before SSE yield
  -> completion committer
  -> one SQLite transaction
       |-> insert immutable conversation turn
       |-> update compact projection/status
       `-> advance exact turn id/index + projection version
  -> commit
  -> best-effort Redis committed refresh + inflight delete
  -> yield existing SSE complete event
```

### 3.1 加载流程

1. API 使用认证后的 `RequestContext.storage_owner_key` 和 `session_id`，不得从请求体接受 scope。
2. Unified 模式下 Harness 只调用一次 `ContextRepository.load_envelope`。
3. Repository 从 SQLite 读取 conversation metadata、compact projection 和受 SQL `LIMIT` 约束的 recent turn window。
4. Redis committed cache 只有在 scope、projection version、last-applied id/index 和 status 全部匹配 SQLite 时才能命中。
5. Projection 落后时，shared reducer 只应用真正 committed 的 turns；ahead/corrupt 不得虚构水位。
6. `HARNESS_STATEFUL_CONTEXT_ENABLED` 只选择 structured 或 legacy renderer，不再触发第二次 DB/Redis load。

### 3.2 运行中与 checkpoint

- 工具、aux、re-evidence、replan 只修改内存 state。
- 同一个 state version 的重复 flush 会被合并，不重复写 inflight。
- 普通新请求不读取 inflight。
- 只有显式 conservative checkpoint resume 才读取 inflight，并校验 owner/session、base projection version、state version 和 run id。
- 完成后 best-effort 删除 inflight；删除失败依赖 TTL，不执行自动生产清理。

### 3.3 完成与保存流程

1. Harness 在 `type=complete` 事件发给客户端前调用 completion committer。
2. Repository 在同一 SQLite `BEGIN IMMEDIATE` 事务中插入 immutable turn，并更新 projection、projection version 和精确水位。
3. `commit_id` 提供幂等；同 session 并发由事务和 scoped unique index 保护。
4. 事务任一步失败都整体 rollback。
5. Unified commit 已尝试但失败时，API **不得**再调用 legacy `_persist_turn`，避免重复或部分提交。
6. SQLite commit 成功后才 best-effort 刷 Redis committed cache，并清理对应 inflight。

### 3.4 开关矩阵

| 开关 | 值 | 行为 |
|---|---|---|
| `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED` | `false` | 当前默认；完整保留旧双路径，作为一键回滚 |
| 同上 | `true` | 启用 single repository + terminal atomic commit；仅 canary 授权后可用 |
| `HARNESS_STATEFUL_CONTEXT_ENABLED` | `true` | structured whiteboard renderer |
| 同上 | `false` | legacy summary/window renderer；unified 下仍用同一 envelope |
| `HARNESS_CONTEXT_DB_SNAPSHOT_ENABLED` | `false` | 仍提交 canonical turn；projection 标记 disabled/stale，水位不虚进 |
| `REDIS_ENABLED` | `false` | committed/inflight cache 关闭，SQLite 权威路径继续工作 |
| `HARNESS_CONTEXT_TOOLS_ENABLED` | `false` | 不向 LLM 暴露 context tools，不改变 repository scope/commit |

## 4. 关键实现地图

| 区域 | 文件 | 接手关注点 |
|---|---|---|
| Envelope contract | `app/agent/context/envelope.py` | scope、水位、runtime history 和 warning/source |
| Compact projection | `app/agent/context/projection.py` | v1/v2 读取；v2 排除 recent text/identity duplicate/patch tail |
| Deterministic reducer | `app/agent/context/reducer.py` | runtime repair、terminal `projection=None`、migration 共用 |
| Render policy | `app/agent/context/renderers.py` | structured/legacy 只消费 envelope，不自行加载服务 |
| Unified glue | `app/agent/context/unified.py` | Harness load、explicit resume、completion commit adapter |
| Repository | `app/services/context_repository.py` | SQLite 权威读写、cache validation、inflight、atomic/idempotent commit |
| Harness wiring | `app/agent/harness/loop.py`、`stream_inner.py`、`checkpoint_ops.py` | 单次加载、terminal commit、后台 checkpoint task 收口 |
| Terminal/API owner | `app/agent/harness/events_emit.py`、`app/api/assistant.py` | complete 前提交；失败后禁止 legacy 二次 append |
| Schema v3 | `app/services/database_migration_service.py` | additive columns/index；runtime 不允许隐式 DDL |
| Operator scripts | `scripts/audit_context_storage.py`、`scripts/migrate_context_projection.py` | audit/dry-run 默认只读；apply 显式授权和 backup gate |
| Focused tests | `tests/test_context_repository.py`、`test_context_projection_migration.py`、`test_context_unified_wiring.py` | 原子性、幂等、并发、cache/inflight、真实 Harness 接线 |

Harness 文件仍满足单文件不超过 1000 行；当前最大 `stream_inner.py` 为 962 行。新逻辑不得重新堆回 `loop.py` facade。

## 5. 首次评审问题的关闭状态

| 问题 | 状态 | 修复结果 |
|---|---|---|
| Unified loader 被第二个 builder/store 绕过 | ✅ | structured/legacy 都只 load repository 一次 |
| `projection=None` 清空旧状态 | ✅ | 事务内保留可信 projection，并 reduce committed turns |
| Unified commit 失败后 legacy 再 append | ✅ | attempted/committed 标记阻止二次持久化 |
| Behind migration 水位虚进 | ✅ | runtime/commit/migration 共用 reducer，水位取实际 turn |
| Runtime 隐式 DDL / backup 门过弱 | ✅ | 强制 schema v3；backup 校验 quick-check、新鲜度、表和行数、空间 |
| Inflight lifecycle 不完整 | ✅ | save/load/delete、coalescing、显式 resume、complete cleanup |
| Cache/history SQL 语义不严 | ✅ | 全水位校验；history=0 为空；SQL LIMIT；正常 load 不读 events JSON |
| pytest/Make/Redis auditor/旧 auth 测试不稳定 | ✅ | 生命周期、Make target、超时与 principal fixture 均修复 |

## 6. 验证证据

### 6.1 最终本地门禁

```text
Unified focused: 115 passed, exit 0
Existing soft path: 23 passed / 44 deselected, exit 0
make ci-smoke: 49 tests, exit 0
make check-harness-size: max 962, exit 0
compileall: exit 0
critical Ruff app/tests/scripts: exit 0
scoped full Ruff: exit 0
git diff --check: exit 0
config assertion: unified default=false
```

完整 focused 命令见[进度文档](../../plan/2026-07-19-unified-context-repository-progress.md)。接手后的最小复核：

```bash
LONG_TERM_MEMORY_DISTILL_ENABLED=false \
HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=false \
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_context_repository.py \
  tests/test_context_projection_migration.py \
  tests/test_context_unified_wiring.py \
  tests/test_audit_context_storage.py \
  -q --no-cov

make ci-smoke
make check-harness-size
PYTHONPATH=. .venv/bin/python -m compileall -q app scripts
PYTHONPATH=. .venv/bin/ruff check --select E9,F63,F7,F82 app tests scripts
git diff --check
```

Make 测试目标已默认关闭经验蒸馏和反模式写入，防止测试继续污染 live memory。新增测试也必须显式关闭可能写长期记忆的 hook。

### 6.2 当前 live 只读基线

| 指标 | 结果 |
|---|---:|
| SQLite schema | current 2 / latest 3 / pending `[3]` |
| `migrate_database.py verify` | exit 1，当前为预期 gate，不是测试失败 |
| conversations / turns / snapshots | 60 / 52 / 53 |
| snapshot+turn aligned / behind | 21 / 3 |
| snapshot-only zero / ahead | 17 / 12 |
| turn-only | 7 |
| 分类合计 | 60 |
| corrupt snapshot / duplicate turn index / orphan | 0 / 0 / 0 |
| top-level scope mismatch / legacy nested identity mismatch | 0 / 53；后者均为空 identity scope，是已知迁移输入 |
| SQLite `quick_check` | `ok` |
| 只读 audit / dry-run | exit 0，`mutated=false` |
| DB 文件 hash | `5ed75bb788654787761dbbd29d58ff0b321a99aaa22c9e26512a2856939493ef`，audit 前后相同 |
| Redis | 不可达；auditor 约 0.2 秒 fail-fast `status=blocked`, exit 2 |

Dry-run 的批准策略：

- 21 aligned：压缩旧 projection。
- 3 behind：从 committed turns 重建缺失部分。
- 17 snapshot-only-zero + 12 snapshot-only-ahead：不伪造 turn，不提升为可信 evidence；原值只由 verified backup 保留。
- 7 turn-only：从 canonical turns 重建 projection。

## 7. 当前红线与已知风险

### 7.1 未获授权，禁止执行

- live `scripts/migrate_database.py up`；
- live projection `apply`；
- Redis 写入、删除或 namespace 清理；
- unified canary；
- 把 `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED` 默认值改为 `true`；
- 清理历史 test-session experience 数据；
- SQLite `VACUUM`、destructive down migration 或猜测性逐行回写。

当前必须保持：

```text
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false
```

### 7.2 数据卫生

复审期间发现历史测试曾向 live `experience_memories` 写入多批 test-session 记录，且 WAL 非零。后续测试写入入口已经关闭，但既有记录没有删除；清理属于新的 live 数据治理任务，必须先建立只读清单、备份和单独授权。

### 7.3 工作树

- 当前 HEAD 为 `634851f`，本棒文件尚未提交。
- 工作树包含用户和前序实现的差异，不能假定全部属于本棒。
- 禁止 `git reset --hard`、`git checkout --` 或整树 `git add -A`。
- 提交前按计划的受影响文件和 `git diff -- <path>` 逐项审查；数据库、WAL、日志、`.env`、token 和 pilot marker 不得进入提交。
- 全仓 Ruff/format 仍有历史基线；本棒只要求 critical Ruff 和 scoped full Ruff，不要顺带格式化约 100 个无关文件。

## 8. 下一棒 SOP

四道 live 门必须依次、独立批准。上一道通过不自动授权下一道。

### Gate 0：只读接手复核

```bash
git status --short --branch
PYTHONPATH=. .venv/bin/python scripts/migrate_database.py status
PYTHONPATH=. .venv/bin/python scripts/audit_context_storage.py \
  --db volumes/long_term_memory.db --read-only --format json
PYTHONPATH=. .venv/bin/python scripts/migrate_context_projection.py \
  dry-run --db volumes/long_term_memory.db --format json
```

退出条件：current 仍为 2、pending `[3]`；audit 与 dry-run `mutated=false`；分类合计 60；top-level scope mismatch、corrupt、orphan、duplicate 仍为 0；已知 legacy nested identity mismatch 仍为 53。若数据已变化，先更新 before 基线，不沿用旧授权。

### Gate 1：live schema v3

仅在用户明确批准 schema `up` 后：

```bash
PYTHONPATH=. .venv/bin/python scripts/migrate_database.py up \
  --db volumes/long_term_memory.db \
  --backup-dir volumes/backups/context-v3

PYTHONPATH=. .venv/bin/python scripts/migrate_database.py status \
  --db volumes/long_term_memory.db
PYTHONPATH=. .venv/bin/python scripts/migrate_database.py verify \
  --db volumes/long_term_memory.db
```

退出条件：current=3、pending=[]、schema/checksum/quick-check 全通过；canonical row counts 不变；backup 可打开且新鲜。失败时确认事务 rollback，保持 unified flag=false，不执行 projection apply。

### Gate 2：projection apply

1. 先用最新 verified backup/copy 演练 schema v3 + projection apply。
2. 对账 60 个会话分类、projection JSON、水位、scope、turn counts 和 backup。
3. 再单独申请 live apply 授权。

获批后的 operator 入口：

```bash
PYTHONPATH=. .venv/bin/python scripts/migrate_context_projection.py \
  dry-run --db volumes/long_term_memory.db --format json

PYTHONPATH=. .venv/bin/python scripts/migrate_context_projection.py apply \
  --db volumes/long_term_memory.db \
  --backup-dir volumes/backups/context-v3 \
  --format json
```

退出条件：apply 分类与 dry-run 一致；无伪造 turn；`last_applied` 精确指向实际 committed turn；再次 audit/quick-check 通过。若需撤回，停止写入并恢复 verified backup，禁止在线逐行猜测回写。

### Gate 3：Redis 实例只读聚合

Redis 健康后执行：

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_context_storage.py \
  --db volumes/long_term_memory.db \
  --read-only --include-redis --format json
```

退出条件：获得 committed/inflight key count、TTL、bytes、schema/corrupt 聚合；不得输出完整 key、owner、session 或消息正文。此门只授权只读命令，不授权删除旧 key。

### Gate 4：新会话 canary

仅在 Gate 1–3 全通过并获得 canary 授权后：

```text
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=true
HARNESS_STATEFUL_CONTEXT_ENABLED=true
```

只对新 session/canary owner 开启，至少记录：

- selected two-turn 连续性；
- 每个 complete 的 SQLite transaction 数；
- Redis writes/stage 与同 version coalescing；
- projection bytes before/after；
- commit P50/P95 与整体 P50/P95；
- commit failure、Redis down、renderer fallback 和 unified=false 回滚演练。

Canary 通过后仍需单独审批默认 true。任何异常先将 unified flag 设回 false；不删除 schema v3 新列，不回滚 canonical turns，不执行 `FLUSHDB`。

## 9. 回滚路径

### 行为回滚

```text
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false
HARNESS_STATEFUL_CONTEXT_ENABLED=false  # 仅需强制 legacy renderer 时使用
```

- 第一项恢复当前旧双路径。
- Schema v3 是 additive，旧代码可忽略新列。
- Redis committed/inflight 都是可丢缓存；按 namespace 定向处理或等待 TTL，禁止 `FLUSHDB`。

### 数据回滚

- Schema migration 不提供 destructive down。
- Projection apply 前必须有新鲜、可打开、row-count 对账的 backup。
- Projection 回滚时停止服务写入并恢复完整 verified backup。
- Canonical `conversation_turns` 不删除、不回写、不根据旧 snapshot 伪造。
- 不自动 `VACUUM`；当前 freelist 可供 SQLite 后续复用。

## 10. 交接检查清单

- [x] 统一 repository、dual renderer 和 atomic commit 代码完成。
- [x] 首次评审 6 P1、2 P2 已关闭并复审。
- [x] 115 focused、23 soft-path、49 ci-smoke 与静态门通过。
- [x] 计划、进度、completion review、运行架构和 AGENTS 索引已同步。
- [x] Live SQLite audit/dry-run 只读完成，分类合计 60、`mutated=false`。
- [x] 默认统一开关保持 false；未执行 live schema/apply/Redis mutation/canary。
- [ ] 获得并执行 live schema v3 `up` 授权。
- [ ] 在 fresh verified backup/copy 演练后，获得并执行 live projection apply 授权。
- [ ] Redis 健康后完成脱敏实例聚合。
- [ ] 新 session canary 和 P50/P95、写放大、two-turn 对照通过。
- [ ] 默认 true 获得独立批准。
- [ ] 历史测试 experience 数据另立治理计划；不得在本棒顺手删除。

## 11. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-19 | 完成双路径现状审计、SOP 计划和 canonical source / audit-first migration 决策审批 |
| 2026-07-19 | 实现 envelope、projection v2、shared reducer、repository、schema v3、auditor/migration CLI 和 Harness/API gated wiring |
| 2026-07-19 | 首次 completion review 发现 6 P1/2 P2；完成修复并以第二轮复审关闭 |
| 2026-07-19 | 最终复核 115 focused、23 soft-path、49 ci-smoke；live 只读分类合计 60，schema 仍为 v2；形成本文作为下一棒入口 |
