# 上下文加载与持久化合并实施计划

> 日期：2026-07-19
> 状态：**代码修复与本地门禁已完成（默认 flag=false）；具备申请 live schema v3 条件；projection apply / canary / 默认 true 仍待单独授权** · 进度见 [progress](./2026-07-19-unified-context-repository-progress.md) · [最新复审](../docs/reviews/completion-review-2026-07-19-unified-context-repository-2.md) · [交接](../docs/pilot/handoff-2026-07-19-unified-context-repository.md)
> 关联：`plan/2026-07-08-stateful-agent-context.md`、
> `plan/2026-07-19-context-dedup-reduction.md`、
> `docs/pilot/context-dual-path.md`

## 1. 问题

当前系统把“上下文模式”同时实现成了两套加载路径和两套持久化生命周期：

```text
请求开始
  |
  +-- stateful=true  -> Redis ContextState
  |                    -> SQLite context_state_json
  |                    -> Conversation turns 重建
  |
  +-- stateful=false -> ContextBuilder
                       -> Conversation turns + rolling summary

运行中
  -> 工具 / aux / re-evidence / replan 后多次写 Redis + SQLite snapshot
  -> checkpoint 另写 Redis 恢复元数据

请求完成
  -> Harness 先保存 ContextState snapshot
  -> API 收到 complete 后再用独立事务 append conversation turn
```

这不是简单的“两份完全相同文本”，而是以下三类职责没有被明确隔开：

1. `conversation_turns` 是完整、不可变的会话审计记录。
2. `context_state_json` 是供模型使用的结构化、可重建投影。
3. Redis checkpoint 是未完成运行的短期恢复数据。

当前实现的问题是：

- Stateful 与 legacy 各自加载历史，模式切换意味着更换数据源，而不是只更换渲染方式。
- Structured snapshot 又持久化 `recent_turns` 文本和 patch audit，复制了部分会话信息。
- Harness snapshot 和 API turn 分属两个 SQLite 事务，没有共同 commit id 或可靠水位。
- 工具阶段会反复序列化并提交整份 snapshot；最终完成时还会再次提交。
- Redis 同一个 context key 同时承担热状态与未完成运行恢复，已提交和未提交状态边界不清楚。
- `conversation.last_turn_index` 的现有含义实际更接近“下一轮序号”，字段名却像“最后已应用轮次”，容易产生 off-by-one。
- `AgentContextState` 顶层已有 owner/session，`identity` 内又重复一份；当前数据中后者全部为空。

本计划的目标是让两种上下文模式共享**一个加载器、一个 envelope、一个最终提交边界**，并减少高频整行 snapshot 重写；不是删除审计记录，也不是把所有恢复数据塞进同一条永久记录。

## 2. 当前数据只读验证

### 2.1 方法与边界

- 数据库：`volumes/long_term_memory.db`
- SQLite 数据审计使用 URI `mode=ro`；未执行 `INSERT`、`UPDATE`、`ALTER`、`VACUUM` 或 migration `up`。
- `scripts/migrate_database.py status/verify` 只读取 schema/migration 状态。
- Redis 只尝试 `PING`；未执行写、删、过期调整或 key 内容输出。
- 结果只保留聚合数，不输出 owner、session、附件、消息或 Redis key。

### 2.2 SQLite 健康与物理状态

| 指标 | 结果 |
|---|---:|
| 文件大小 | 11,042,816 bytes |
| `PRAGMA quick_check` | `ok` |
| page size / page count | 4,096 / 2,696 |
| freelist pages | 2,106（约 8.63 MB，78.1% pages） |
| WAL / SHM | 0 / 32,768 bytes |
| migration | version 2，pending `[]`，schema/checksum 均通过 |

结论：11 MB 文件大小不能直接归因于上下文双份存储；大量页面已经进入 freelist，可被 SQLite 后续复用，但文件不会自动缩小。本计划减少未来写放大，**不自动执行 `VACUUM`**。

### 2.3 行数与逻辑载荷

| 数据 | 结果 |
|---|---:|
| conversations | 60 |
| conversation turns | 52 |
| structured snapshots | 53 |
| rolling summaries | 0 |
| snapshot JSON 总字符 | 457,019 |
| snapshot 单行字符 min / avg / median / max | 1,043 / 8,623 / 8,257 / 24,374 |
| user message 总字符 | 2,228 |
| assistant answer 总字符 | 55,870 |
| turn events JSON 总字符 | 661,203 |
| user context 总字符 | 0 |
| attachment refs JSON 总字符 | 104（当前 52 行均为空列表量级） |

Snapshot 各 block 聚合字符数（用 JSON 编码后的近似逻辑量统计）：

| block | 字符数 | 说明 |
|---|---:|---|
| `patch_tail` | 229,704 | 最大项，约占 snapshot 总字符一半 |
| `evidence` | 83,782 | 结构化事实与工具摘要 |
| `tool` | 46,680 | 工具状态 |
| `conversation` | 46,431 | 含 recent turn 文本 |
| `output` | 19,923 | 最近输出摘要 |
| `intent` | 9,280 | 当前问题/目标 |
| `identity` | 3,710 | 与顶层 scope 重复且当前均为空 scope |

`patch_tail` 共 1,574 条，全部是 writer/section/field/op/version/time/count 元数据，**0 条携带 value**。因此它适合留在运行期/调试观测，不适合占用每次 durable projection 的一半空间。

### 2.4 重复、生命周期与水位

| 检查 | 结果 |
|---|---:|
| snapshot recent-turn entries / chars | 296 / 29,805 |
| 与 turn DB role+content 完全相同 | 23 entries / 1,625 chars |
| conversation `updated_at` 晚于 snapshot | 24 |
| snapshot 有、turn 无的会话 | 29 |
| turn 有、snapshot 无的会话 | 7 |

完全相同统计只是重复量的可验证下界；截断、清洗或摘要后的派生文本不会被 exact match 计入。

当前 `last_turn_index` 按代码是下一轮序号。将它与 DB 的 `MAX(turn_index)+1` 比较：

| 关系 | 会话数 | 差值分布 |
|---|---:|---|
| 对齐 | 38 | `0: 38` |
| snapshot 超前 | 12 | `2:1, 9:2, 10:1, 12:4, 13:1, 14:2, 16:1` |
| snapshot 落后 | 3 | `-1:3` |

这直接证明 snapshot 与 turn 的提交生命周期独立。超前记录不能在迁移时伪造成已提交 turn；落后记录需要从审计 turn 补投影。

上述关系与“有无 turn”存在交叉。用于 migration 对账的互斥分类是：

| 互斥分类 | 会话数 |
|---|---:|
| snapshot + turn，水位对齐 | 21 |
| snapshot + turn，snapshot 落后 | 3 |
| snapshot-only，零水位对齐 | 17 |
| snapshot-only，snapshot 超前 | 12 |
| turn-only | 7 |
| 合计 | 60 |

因此 12 个 ahead 全部属于 29 个 snapshot-only，不能与 29 相加后当成独立异常总数。

### 2.5 完整性与租户范围

| 检查 | 结果 |
|---|---:|
| snapshot JSON valid / corrupt | 53 / 0 |
| snapshot schema versions | `1: 53` |
| 重复 `(owner, session, turn_index)` | 0 |
| orphan turn sessions | 0 |
| 空 owner/session turn 或 snapshot | 0 |
| invalid events / attachment refs JSON | 0 / 0 |
| snapshot column vs JSON schema mismatch | 0 |
| snapshot 顶层 owner/session 与 DB row mismatch | 0 |
| snapshot `identity.owner_key/session_id` mismatch | 53 |

53 个 identity mismatch 均来自 identity 内 owner/session 为空，而顶层 scope 正确。它当前不是跨租户泄漏，但说明重复身份字段没有形成可靠契约；目标模型只保留 envelope 顶层 scope 作为权威值。

### 2.6 Redis 验证缺口

当前配置为：

- `REDIS_ENABLED=true`
- namespace `super_biz_agent`
- context TTL 86,400 seconds
- DB snapshot enabled
- stateful context enabled

只读 `PING` 在 8 秒内返回 `TimeoutError`，pilot compose 当前也没有活动服务，因此无法验证 context key 数、TTL 分布、payload 体积、JSON/schema 完整性。此项是实施前数据门，不得写成“通过”。待 Redis 健康后必须运行脱敏聚合审计，且不得输出完整 key 或 owner/session。

### 2.7 静态写放大证据

- `persist_stateful_context(...)` 在 harness 三个模块中有 10 个实际调用点。
- 其中 9 个请求 SQLite snapshot，1 个仅写 Redis。
- 主工具循环内的调用会随完成的 tool step 重复，不是“每轮固定一次”。
- 每次 `ContextStateStore.save(..., persist_snapshot=True)` 都序列化整份 state、写 Redis，并用独立 SQLite 连接更新 snapshot。
- API 在收到最终 `type=complete` 后，再通过 `conversation_service.append_turn` 开启另一事务。
- Stateful checkpoint 每个完成 step 另写 Redis meta/step/messages 三个 key；它是恢复数据，不应与永久会话投影混为一份。

现有数据库只保留最新 snapshot，无法从静态结果还原历史 snapshot 写入总次数。因此计划把“实际写次数/字节”列为上线观测指标，不从当前 53 行反推未经证实的历史写量。

## 3. 目标架构

```text
                         +-----------------------------+
request ---------------->| ContextRepository.load()    |
                         |  SQLite audit + projection  |
                         |  Redis committed cache      |
                         +--------------+--------------+
                                        |
                               ContextEnvelope
                                        |
                   +--------------------+--------------------+
                   |                                         |
          StructuredRenderPolicy                    LegacyRenderPolicy
          (whiteboard view)                         (summary/window view)
                   |                                         |
                   +--------------------+--------------------+
                                        |
                                  Harness runtime
                                        |
                   in-flight recovery -> Redis + checkpoint
                                        |
                         complete -> one SQLite transaction
                           insert immutable turn
                           update compact projection
                           advance explicit watermark
                                        |
                           post-commit Redis cache refresh
```

### 3.1 数据职责

| 数据 | 权威位置 | 是否可重建 | 是否含完整 turn 文本 |
|---|---|---:|---:|
| user/assistant/events/attachments | `conversation_turns` | 否，审计源 | 是 |
| structured working/evidence/tool/output | compact projection | 是 | 否 |
| recent history window | `ContextEnvelope` 运行时字段 | 是，从 turns 读取 | 仅内存 |
| committed projection cache | Redis committed key | 是，从 SQLite 读取 | 否 |
| 未完成运行恢复 | Redis inflight key + checkpoint refs | 可过期 | 仅必要恢复状态 |

## 4. 设计决策与默认值

### D1. 一个 repository，一个 envelope

新增 `ContextRepository.load_envelope(owner_key, session_id)`，返回：

- 经过 scope 校验的 owner/session；
- compact structured projection；
- 从 `conversation_turns` 读取、按既有 token/turn budget 截断的 runtime turn window；
- active attachment index；
- rolling summary（若存在）；
- `latest_turn_id/index`、`last_applied_turn_id/index`、`projection_version`；
- cache/source/repair 状态和脱敏 warning。

这里的 repository `owner_key` 在兼容期必须接收经过认证的
`RequestContext.storage_owner_key`；本计划不顺带把 legacy SQLite scope 切换为 stable `RequestContext.owner_key`。两者的显式数据迁移仍由租户治理计划负责，禁止在同一查询或 cache key 中混用。

`HARNESS_STATEFUL_CONTEXT_ENABLED` 在 unified 模式下只选择 render policy：

- `true`：structured whiteboard renderer；
- `false`：legacy summary/window renderer。

两者不得自行访问 `conversation_service`、`ContextSnapshotService` 或 Redis。`ContextBuilder` 保留为兼容 adapter，但其加载职责移入 repository。

### D2. Conversation turn 是唯一完整审计源

- Projection serializer 不再持久化 `conversation.recent_turns` 文本。
- Runtime turn window 每次由 repository 从 turn 表构造；需要缓存时只缓存 compact projection，不缓存第二份完整 turn。
- Projection 不再重复持久化 identity owner/session；scope 只来自 envelope/repository 参数和 DB 主键。
- `active_attachment_refs` 只保留带 source turn id 的轻量派生索引，原始 refs 仍以 turn 行为准。
- `patch_tail` 默认不进入 durable projection；运行期可保留 bounded tail，完整过程仍由 turn `events_json` 和 checkpoint 承担。

### D3. 完成轮次与 projection 同一事务

统一完成回调在最终 `type=complete` 发给 API 客户端前调用 repository。默认
`HARNESS_CONTEXT_DB_SNAPSHOT_ENABLED=true` 时：

1. 在事务外完成 payload/schema/scope 校验和 compact serialization。
2. SQLite `BEGIN IMMEDIATE`。
3. 以 request trace id 作为内部 `commit_id`，先检查幂等记录。
4. 在同一 owner/session 锁定窗口内计算并插入下一 `turn_index`。
5. 取得稳定的 `conversation_turns.id`。
6. 更新 compact projection、`projection_version` 和实际已插入 turn 的 id/index 水位。
7. 同一事务更新 conversation 时间戳并 commit。
8. DB commit 后 best-effort 刷新 Redis committed cache。

任何 DB 步骤失败必须整体 rollback，不能因异常出现“有 turn 无 projection”或“projection 宣称应用了未提交 turn”。为保持当前可用性契约，默认仍允许回答完成，但必须记录无高基数标签的 degraded metric/log；不得吞掉部分提交。

为保留 2026-07-08 已批准的独立降级层，
`HARNESS_CONTEXT_DB_SNAPSHOT_ENABLED=false` 是唯一允许的显式 turn-only 模式：同一事务仍提交 canonical turn，并把 projection 标记为 `disabled/stale`，但不更新 projection JSON 或 `last_applied` 水位。后续 load 使用 Redis committed cache（若可用）或从 turns 重建 runtime projection。该状态必须可观测，不能伪装成原子 projection commit 成功。

API stub/旧 harness 不支持完成回调时继续走旧 `_persist_turn`，仅作为 unified flag 关闭时的兼容路径，不能与 unified commit 同时执行。

### D4. 明确四种 version/watermark

| 字段 | 含义 |
|---|---|
| `schema_version` | JSON 结构版本 |
| `state.version` | 单次运行中的 patch 版本，仅用于运行期排序 |
| `projection_version` | 每次成功 durable commit 单调 +1，用于 cache CAS/失效 |
| `last_applied_turn_id/index` | projection 真正包含的最后一条已提交 turn；空会话为 `NULL/-1` |

弃用含义模糊的 persisted `conversation.last_turn_index`。旧值只作为 migration audit 输入，不直接复制为新水位。

### D5. Redis 分开 committed 与 inflight

- committed cache：只缓存已由 SQLite commit 的 projection，key 带 projection version，TTL 沿用 86,400 秒。
- inflight recovery：按 run/commit id 隔离，TTL 沿用 checkpoint 1,800 秒；普通 context load 不读取它。
- 只有显式 conservative checkpoint resume 才能读取 inflight，并校验 owner/session、base projection version、context version 和允许重放的工具。
- 每个逻辑 stage 只在 state version 前进时 flush 一次；同 version 重复调用不写。
- DB commit 后才更新 committed cache；Redis 失败不改变 SQLite 权威结果。
- run 完成后 best-effort 删除 inflight key；删除失败依赖 TTL，不触发自动生产修复。

### D6. Schema 与数据迁移采用 additive + 显式 apply

Schema migration version 3 只做可回滚的增量修改：

- `conversation_turns.commit_id TEXT`；
- scoped partial unique index，保证非空 commit id 幂等；
- `conversations.context_projection_version`；
- `context_last_applied_turn_id`；
- `context_last_applied_turn_index`；
- `context_projection_status`。

继续复用 `context_state_json` 物理列，避免第三份长期 snapshot；在代码和文档中将其语义收敛为 compact projection。Schema `up` 不自动改写 JSON 数据。

新增 operator 脚本提供严格分离的子命令：

- `audit`：只读聚合；
- `dry-run`：只读生成分类和预计变化；
- `apply`：必须经过现有 backup/free-space gate，显式授权后才写；
- 不提供 destructive `down` 或自动 `VACUUM`。

推荐迁移规则（本计划的审批门，按互斥分类）：

- snapshot + turn / aligned 21：保留非 conversation structured blocks，去掉 recent text/identity duplicate/patch tail，水位取 DB 实际 turn id/index。
- snapshot + turn / behind 3：从缺失的 committed turn/events 补投影后再写新水位。
- snapshot-only / zero-watermark 17：没有 turn 审计的数据不直接升级为可信 evidence；保留备份和分类报告，目标 projection 为空。
- snapshot-only / ahead 12：**审计 turn 优先**，不得伪造缺失 turn；旧 snapshot 只进入 backup/audit 报告，目标 projection 为空。
- turn-only 7：从 turns 构造 projection。

由于该规则会改变数据源优先级，且可能舍弃没有 turn 审计支撑的旧结构化状态，必须由用户明确审批后才能执行 `apply`。

### D7. Rollout 默认不改变当前行为

| Env | 初始默认 | 作用 |
|---|---:|---|
| `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED` | `false` | 总开关；false 保留当前双路径，true 启用统一 repository/commit |
| `HARNESS_STATEFUL_CONTEXT_ENABLED` | 现值 `true` | unified 下仅选 structured/legacy renderer |
| `REDIS_ENABLED` | 现值 `true` | false 时 repository 仅使用 SQLite，功能可降级 |
| `HARNESS_CHECKPOINT_ENABLED` | 现值 `false` | 控制 inflight checkpoint；不影响 committed projection |
| `HARNESS_CONTEXT_DB_SNAPSHOT_ENABLED` | 现值 `true` | false 时仍提交 turn，但 projection 明确标记 disabled/stale，并从 Redis/turns 重建 |
| `HARNESS_CONTEXT_TOOLS_ENABLED` | 现值 `true` | false 时不向 LLM 暴露 context tools，不影响 repository scope/commit |

不在本计划内把 unified 总开关改为默认 true。默认切换需要完成 migration dry-run、Redis 验证、canary 和前后数据对账后的独立批准。

### D8. 安全与契约保持

- 所有 repository SQL 与 Redis key 必须同时带验证后的 storage owner key + session_id；本计划不切换 owner-key 数据策略。
- 不改变 SSE `type` 语义；允许在既有 `agent_event` payload 增加脱敏 commit 状态，但不暴露 snapshot/key。
- 不修改 auth、CORS、owner authorization 或 RAG tenant scope。
- 不增加自动 restart/rollback/scaling/production remediation。
- Checkpoint replay 保持 conservative 默认；context merge 不扩大可重放工具名单。
- `app/agent/harness/*.py` 均保持不超过 1,000 行；新逻辑放入 focused repository/render/commit 模块，不堆到 `loop.py` facade。

## 5. 范围与非目标

### 范围

1. 统一 context envelope、repository、structured/legacy render policy。
2. 完成 turn + projection 的 SQLite 原子提交。
3. Redis committed/inflight 语义分离与 stage coalescing。
4. Context projection schema v2 与 SQLite migration v3。
5. 只读 audit/dry-run、显式 apply、观测与 rollout runbook。
6. 旧 service/ContextBuilder 的兼容 adapter 和一键行为降级路径。

### 非目标

- 不删除 conversation turn/events 审计数据。
- 不把 checkpoint 合并进永久 conversation row。
- 不修改 LLM prompt 去重规则；该项已由 2026-07-19 dedup 计划实现。
- 不改变附件可信度、工具权限、SSE 类型或前端协议。
- 不做 Redis 集群迁移、SQLite 自动 `VACUUM` 或生产数据自动修复。
- 不删除 legacy 代码；物理删除需后续 traffic/rollback ADR。
- 不在本计划中修改默认数据源；只实现 gated 能力并完成验证。

## 6. 受影响文件

| 区域 | 文件 |
|---|---|
| Domain contract | 新增 `app/agent/context/envelope.py`、调整 `state.py` / `persistence.py` |
| Repository | 新增 `app/services/context_repository.py` |
| Render policies | 新增 `app/agent/context/renderers.py`，收敛 `views.py` / `harness/context.py` |
| Legacy adapters | `app/agent/context/store.py`、`integration.py`、`app/services/context_snapshot_service.py`、`conversation_service.py` |
| Harness wiring | `app/agent/harness/loop.py`（仅依赖注入/完成事件拦截）、`stream_inner.py`、`tools_runtime.py`、`close_path.py`、`checkpoint_ops.py` |
| API commit | `app/api/assistant.py` |
| Redis/checkpoint | `app/services/redis_client.py`、`harness_checkpoint.py` |
| Schema/CLI | `app/services/database_migration_service.py`、`scripts/migrate_database.py`、新增 `scripts/audit_context_storage.py` / `scripts/migrate_context_projection.py` |
| Config/docs | `app/config.py`、`.env.example`、`docs/pilot/context-dual-path.md`、本计划、进度文档、`AGENTS.md` |
| Tests | 新增 `tests/test_context_repository.py`、`test_context_projection_migration.py`、调整 context/harness/API/checkpoint/migration focused tests |

## 7. 可执行实施顺序

### Task 0：审批并冻结基线

1. 审批 D1/D3 的 canonical source 与 atomic commit。
2. 审批 D6 对 ahead/snapshot-only 数据的“审计优先”迁移策略。
3. Redis 健康后补只读 key/TTL/bytes/schema 聚合。
4. 保存本节 SQLite audit JSON 和 migration status 作为 before 基线。

退出：审批结论写入本计划；未审批不得进入 schema/code/data write。

### Task 1：先交付只读 auditor

1. 实现 `audit_context_storage.py --db ... --read-only`，强制 SQLite URI `mode=ro`。
2. Redis 子命令只允许 `PING/SCAN/PTTL/STRLEN/GET`，输出聚合且屏蔽 key/scope。
3. 覆盖 corrupt JSON、scope mismatch、watermark relation、payload block size、duplicate index。
4. 为 auditor 增加 fixture 单测，断言 read-only 模式不改变文件 hash/mtime/row counts。

退出：在当前 DB 上复现本计划 totals；Redis 不可达时明确 non-zero/blocked 状态，不伪造空结果。

### Task 2：定义 envelope、projection v2 与 render policy

1. 新增不可变 scope/watermark metadata 和 runtime-only turn window。
2. Projection v2 serializer 排除 recent turn text、duplicate identity scope 和 durable patch tail。
3. v1/v2 reader 均可读；v1 只做内存兼容，不在 load 时写回。
4. Structured 与 legacy renderer 只接收 envelope，不直接访问服务。

退出：同一 fixture envelope 可由两种 renderer 输出；静态测试禁止 renderer 导入 conversation/snapshot/Redis service。

### Task 3：实现 SQLite migration v3

1. 添加 commit/watermark/projection metadata columns 与 partial unique index。
2. 更新 required schema/checksum/status/verify。
3. 覆盖 empty/current/v2 legacy fixture、失败 rollback、幂等 `up`。
4. Schema `up` 不改 `context_state_json` 内容。

退出：fixture `up` 可重入；注入失败后 schema/data 不出现半迁移；live 只先跑 status/dry-run。

### Task 4：实现 ContextRepository

1. `load_envelope` 用单一 scope 和统一 read transaction 获取最新 turn metadata/window + projection。
2. committed Redis cache 只有在 projection version/watermark 与 SQLite 一致时命中。
3. cache miss/corrupt/stale 回 SQLite；projection behind 只通过 reducer 补 committed turns。
4. `commit_completed_turn` 实现 D3 事务、commit id 幂等和并发序号保护。
5. Redis post-commit 更新 best-effort；任何错误不得改变 DB transaction 结果。

退出：原子性、幂等、并发、cache stale/corrupt、Redis down、scope isolation tests 全通过；DB projection disabled 时 turn 提交且水位不虚假前移。

### Task 5：接入两种 render mode

1. Harness 只调用 repository 一次加载 envelope。
2. `HARNESS_STATEFUL_CONTEXT_ENABLED` 只选择 renderer。
3. `ContextBuilder.abuild` 变成兼容 adapter；移除它自己的 turn/summary DB read。
4. Stateful rebuild 不再回调 legacy builder 形成第二次加载。
5. 保持附件 summary/index/full 和 intent/evidence dedup 现有行为。

退出：AST/调用计数测试证明 harness 业务路径只有 repository 可加载 conversation/projection；两种 policy 的 turn window 来源一致。

### Task 6：接入统一完成提交

1. Harness 外层 `stream` 拦截最终 `type=complete`，调用 optional completion committer。
2. API 注入捕获 raw question、persistent attachment refs/context、request trace id 的 committer。
3. Normal、clarify、soft-timeout、fallback complete 都走同一提交接口；无 structured state 时 reducer 构造最小 projection。
4. Unified 回调成功后禁止 API 再 `_persist_turn`。
5. 完成事件在 commit 尝试结束后发出，event shape 不变。

退出：每个 terminal path 恰好一个 turn；重复 complete/commit id 不重复插入；故障注入无半提交。

### Task 7：收敛运行中持久化

1. 工具/aux/re-evidence/replan 只修改内存 projection 并标 dirty。
2. 每个完成 stage 按 state version 至多 flush 一次 inflight Redis。
3. 删除运行中 durable SQLite snapshot 调用；最终 DB commit 是正常轮次唯一永久 context write。
4. Checkpoint 继续只存恢复元数据/ref，不恢复完整 messages 写入。
5. timeout/error 保存 inflight + conservative checkpoint；普通新请求不读取 inflight。

退出：N 个 tool step 的正常完成只有 1 个 SQLite transaction（projection enabled 时 turn+projection；disabled 时 turn+stale marker）；Redis 写次数不超过逻辑 stage 数且同 version 去重。

### Task 8：数据 migration dry-run 与 canary

1. 对 SQLite backup/copy 运行 schema `up` 和 projection `dry-run`。
2. 输出第 2.4 节五个互斥 migration 分类，必须合计 60 并与 before totals 对账。
3. 经再次授权后才对 live 执行 schema `up`；projection `apply` 仍分开授权。
4. 先仅新 session/canary owner 启用 unified flag；禁止全量自动切换。
5. 对账 turn count、commit uniqueness、watermark equality、projection size/write count 和两轮连续性。

退出：无 scope mismatch/duplicate/orphan/corrupt；canary rollback 演练通过；Redis 审计补齐。

### Task 9：验证、进度与 rollout

1. 跑 focused、ci-smoke、soft-path、静态检查和 selected two-turn live eval。
2. 记录 before/after P50/P95、SQLite transactions/turn、Redis writes/stage、projection bytes。
3. 更新 `docs/pilot/context-dual-path.md` 为“single repository, dual render policy”。
4. 新建 progress 文档记录命令、结果、偏差和未完成 live 门。
5. 只有所有 exit criteria 满足并再次审批，才提议把 unified 默认改为 true。

## 8. 验证命令

计划实施后的 focused tests：

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_context_repository.py \
  tests/test_context_projection_migration.py \
  tests/test_context_store.py \
  tests/test_context_integration.py \
  tests/test_context_snapshot_service.py \
  tests/test_context_views.py \
  tests/test_context_dedup.py \
  tests/test_harness_stateful_context.py \
  tests/test_harness_stateful_checkpoint_resume.py \
  tests/test_harness_checkpoint.py \
  tests/test_database_migrations.py \
  -q --no-cov
```

Existing soft path 与静态门：

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_harness_service.py \
  -k 'attachment or two_turn or history or context_builder or stateful or fallback or clarify' \
  -q --no-cov

PYTHONPATH=. .venv/bin/python -m pytest \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_harness_verifier.py \
  tests/test_harness_checkpoint.py \
  tests/test_context_integration.py \
  -q --tb=line --no-cov

make check-harness-size
PYTHONPATH=. .venv/bin/python -m compileall -q app scripts
PYTHONPATH=. .venv/bin/ruff check --select E9,F63,F7,F82 app tests scripts
git diff --check
```

Schema/data audit（先只读）：

```bash
PYTHONPATH=. .venv/bin/python scripts/migrate_database.py status
PYTHONPATH=. .venv/bin/python scripts/migrate_database.py verify
PYTHONPATH=. .venv/bin/python scripts/audit_context_storage.py \
  --db volumes/long_term_memory.db --read-only --format json
PYTHONPATH=. .venv/bin/python scripts/migrate_context_projection.py \
  dry-run --db volumes/long_term_memory.db --format json
```

Live selected eval（服务与授权环境健康后）：

```bash
PYTHONPATH=. .venv/bin/python scripts/evaluate_oncall_local.py \
  --case M1-two-turn --inter-case-sleep 0
```

需分别在 unified off/on 下运行并记录同一类场景，不在命令或文档中写真实密码/token。

## 9. 验收标准

- [x] Harness 中 structured/legacy 模式共享唯一 `ContextRepository.load_envelope`。
- [x] `conversation_turns` 是唯一持久化完整 user/assistant/events 的位置。
- [x] Durable projection JSON 不含 recent turn text、重复 identity scope 或 patch tail。
- [x] 每个正常 complete 只有一个 SQLite transaction；projection enabled 时同时提交 turn 与 projection。
- [x] DB projection=false 时 turn 仍提交，projection 标记 disabled/stale，`last_applied` 不虚假前移。
- [x] commit id 重放不新增 turn；并发提交没有重复 scoped turn index。
- [x] 成功提交后 `last_applied_turn_id/index` 精确等于实际插入 turn。
- [x] Redis unavailable/corrupt/stale 时 SQLite 主路径可用，且不把 inflight 当 committed。
- [x] N 个工具 step 不产生 N 个 durable SQLite snapshot 更新。
- [x] Unified flag=false 的兼容路径通过；紧急 legacy renderer 回退已演练。
- [x] 旧 checkpoint 仍只读兼容；conservative replay 语义未放宽。
- [x] 当前数据 dry-run 分类总数对齐，ahead/snapshot-only 没有被静默升级为可信 evidence。
- [ ] Redis key/TTL/bytes/schema 脱敏聚合验证补齐。
- [x] storage owner/session scope、auth、SSE type、附件安全与 RAG tenant contract 无回归；未混用 stable owner key。
- [x] `enabled=false / db_snapshot=false / tools=false` 三个降级开关分别有独立生效断言。
- [x] focused tests、existing soft path、ci-smoke、harness size、compile/ruff/diff 全通过。
- [ ] unified on 相比 off：projection bytes 和 SQLite context writes/turn 明显下降；two-turn 连续性不下降。
- [x] 验证证据和 material deviations 写入 progress 文档，AGENTS 索引状态同步。

未勾选项均为 live rollout 门禁，不影响“代码与本地项目流程完成”的结论，也不得据此启用 canary 或修改默认值。

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| ahead/snapshot-only 含有未审计但仍有价值的状态 | apply 前完整 backup + 聚合清单；默认审计优先；必须单独审批 |
| SQLite transaction 增加 complete 前延迟 | serialization 在事务外；事务内只 insert/update；记录 commit P50/P95 |
| 同 session 并发导致 turn_index 冲突 | `BEGIN IMMEDIATE` + scoped unique index + commit id 幂等测试 |
| Redis cache 覆盖 DB 新状态 | cache 必须匹配 DB projection version/watermark；DB 永远可回源 |
| timeout 的未提交工具状态污染下一请求 | committed/inflight 分 key；普通 load 禁读 inflight |
| legacy renderer 行为漂移 | 复用同一 envelope，但保留原 window/summary 预算和 focused parity tests |
| 删除 persisted recent text 后 stateful history 为空 | turn window 作为 envelope runtime 字段，每次从 canonical turns 生成 |
| patch_tail 不落 DB 后调试信息减少 | runtime bounded tail + checkpoint/timeline events + 聚合 metrics |
| 文件大小不立即下降 | freelist 可复用；不承诺物理缩容；VACUUM 另行评估/授权 |
| 回调失败导致回答完成但未持久化 | DB 全有或全无；degraded metric/log；不伪造成功 watermark |

## 11. 回滚路径

### 代码/行为回滚

```bash
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false
HARNESS_STATEFUL_CONTEXT_ENABLED=false
```

第一项恢复现有路径；第二项在 unified 期间旧 stateful snapshot 可能变旧时，强制从 conversation turns 走 legacy renderer。Schema v3 为 additive，旧代码可忽略新增列。

### 数据回滚

- Schema 不提供 destructive down。
- Projection `apply` 前必须使用现有 SQLite backup gate 生成并校验备份。
- 若 projection 数据迁移需撤回，停止服务写入、恢复已验证备份并重新执行只读 audit；不得在线逐行猜测性回写。
- Redis committed/inflight 均为可丢缓存；回滚时使用 namespace 定向失效或等待 TTL，禁止 `FLUSHDB`。
- 不回滚或删除 canonical conversation turns。

## 12. 审批门

进入实施前需要明确批准以下两项：

1. **Canonical source / commit**：conversation turns 为完整审计源；compact projection 为可重建派生状态；Redis 为 cache/recovery；DB projection 开启时，完成 turn 与 projection 同一 SQLite 事务。
2. **Migration policy**：对 29 个 snapshot-only 会话（其中 12 个 snapshot-ahead），不伪造 turn、不直接提升为可信 evidence；以 backup 保留原始值；另对 3 个 behind 会话从 committed turns 补投影。

### 12.1 审批结论（2026-07-19）

| 门 | 结论 | 说明 |
|---|---|---|
| D1/D3 Canonical source / atomic commit | **批准** | turns=审计源；compact projection=派生；Redis=cache/recovery；`HARNESS_CONTEXT_DB_SNAPSHOT_ENABLED=true` 时 turn+projection 同事务 |
| D6 Migration policy（审计优先） | **批准** | snapshot-only/ahead 不伪造 turn、不直接升为可信 evidence；behind 从 committed turns 补投影；apply 前 backup |
| Schema `up` / projection `apply` / unified 默认 true | **未批准** | 代码可实现 gated 能力；live 写库与默认切换仍需单独授权 |
| Redis 实例聚合审计 | **未完成** | 上次 PING 超时；Task 1 auditor 必须可报告 blocked，不得伪造空通过 |

因此允许从 Task 1 起实现代码与 fixture 测试；默认 `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false`。对 live `volumes/long_term_memory.db` 的 schema `up`、projection `apply`、Redis mutation 仍禁止，直至再次明确授权。

## 13. 进度记录

- 2026-07-19：完成现有架构、加载路径、10 个持久化调用点和 API 独立 turn commit 的只读核对。
- 2026-07-19：完成 SQLite `mode=ro` 聚合审计；数据库 quick check、migration version 2、JSON/scope/index 完整性结果已记录于第 2 节。
- 2026-07-19：Redis `PING` 超时，实例级 key/TTL/bytes 验证明确保持未完成。
- 2026-07-19：计划已按 AGENTS.md SOP 补齐问题、决策/default、范围、文件、flags、任务、验证、exit criteria、风险、回滚与审批门；未实施运行时代码或数据迁移。
- 2026-07-19：用户批准审批门 1+2；进入 Task 1 实施。
- 2026-07-19：Task 1–7 代码落地（auditor、envelope/projection v2、migration v3、ContextRepository、harness/API gated wiring、migration script、docs）；focused 55 passed；live 只读 audit 分类合计 60 与计划一致；**未**对 live 执行 schema up / projection apply；统一 flag 默认 false。详见 progress 文档。
- 2026-07-19：首次 completion review 的 6 P1 / 2 P2 已全部完成代码修复；focused 115、soft-path 23、ci-smoke 49 均 exit 0；Make/Redis auditor/scoped Ruff 门禁通过。最新复审结论为“implementation ready，rollout 仍受 live schema/Redis/canary 显式门阻塞”。
