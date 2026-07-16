# 状态化 Agent 上下文实施计划

> 状态: 修订版计划
> 创建: 2026-07-08
> 修订目标: 去掉与状态化上下文冲突的旧缓存/旧拼接路径, 明确 Redis + DB snapshot 的职责边界, 不再让 memory cache 参与当前上下文
> 涉及模块: `app/agent/harness/*`、`app/agent/context/*`、`app/services/harness_checkpoint.py`、`app/services/conversation_service.py`、`app/services/redis_client.py`、`app/config.py`

## 1. 核心结论

本项目后续的多轮上下文主路径应从:

```text
每轮请求 -> 从 conversation DB 读取 turns -> rolling_summary/token window -> 拼 system prompt
```

调整为:

```text
每轮请求 -> 读取 ContextState 白板 -> 渲染上下文视图 -> LLM 决策
```

因此以下机制需要重新分层:

| 机制 | 新定位 | 是否继续作为主路径 |
|---|---|---|
| `ContextState` | 当前会话白板, 承载 current_goal、known_facts、open_questions、working_plan、tool evidence | 是 |
| Redis | ContextState 热存储, 每轮读写的主来源 | 是 |
| DB ContextState snapshot | Redis 丢失后的冷备份, 每轮结束/关键证据/最终答案时写入 | 是, 但不是每次 prompt 构建来源 |
| conversation DB turns | 原始审计日志, 最后兜底重建来源 | 否 |
| rolling summary | 仅用于冷启动/Redis miss/DB snapshot miss 时辅助重建 ContextState | 否 |
| token-aware history window | 仅用于冷启动重建和最终渲染视图预算控制 | 否 |
| memory cache | 本计划不再使用; 不读、不写、不作为上下文兜底 | 否 |
| checkpoint | 单次 Harness run 的中断恢复点, 只保存执行进度与 ContextState 版本引用 | 是, 但不保存上下文主体 |

最重要的原则:

```text
ContextState 是当前会话唯一的上下文白板。
memory cache 不再缓存当前会话上下文。
memory cache 不参与 ContextState 的任何读写链路。
rolling summary/token window 不再作为正常多轮对话的主上下文构建路径。
```

## 2. 当前冲突点与处理方式

### 2.1 rolling summary / token window 与状态化上下文冲突

现状:

- `ContextBuilder.abuild()` 每轮从 `conversation_service.get_turns(owner_key, session_id)` 拉历史。
- `_load_or_update_rolling_summary()` 会用旧 turns 生成摘要。
- `_select_recent_turns()` 和 token window 会裁剪最近历史。
- 最后 `_build_system_prompt()` 拼接出 prompt。

这套逻辑仍然以“历史文本”为上下文主来源, 与“白板状态对象”为主来源冲突。

处理:

```text
保留 ContextBuilder, 但降级为 fallback/rebuild 工具。
正常路径不再每轮调用 ContextBuilder.abuild()。
只有 ContextState 缺失、损坏、schema 不兼容时才回 DB turns 重建。
```

### 2.2 memory cache 与 ContextState 冲突

如果 memory cache 保存的是当前会话状态、最近工具结果、当前计划、上下文摘要, 它与 ContextState 重叠。

本计划按用户当前目标收敛为:

```text
只要信息属于当前会话上下文, 就只进入 ContextState。
memory cache 不再作为上下文组件存在。
```

处理:

```text
删除或停用“当前会话上下文缓存”用途。
ContextState 不写入 memory cache。
ContextState 不从 memory cache 读取。
本计划不设计 memory promotion; 如果未来需要长期经验, 另起独立设计, 不复用当前会话白板。
```

新的边界:

| 数据 | 存放位置 |
|---|---|
| 当前目标、当前计划、已知事实、待确认问题 | Redis ContextState + DB snapshot |
| 当前 run 的 step/timeline 引用/恢复策略 | checkpoint |
| 原始用户消息、助手回答、审计日志 | conversation DB |
| 跨会话可复用经验 | 本计划不处理; 另起长期经验设计 |

### 2.3 只用 Redis 存状态存在丢失风险

Redis 适合做热状态, 但不能作为唯一真实来源。

风险:

- Redis 重启。
- key TTL 过期。
- maxmemory 淘汰。
- 运维误删/flush。
- 网络抖动导致写失败。
- 多 worker 写入冲突。

处理:

```text
Redis 是热读写主路径。
DB ContextState snapshot 是冷备份。
conversation turns 是最后重建来源。
```

读取优先级:

```text
1. Redis ContextState
2. DB ContextState snapshot
3. conversation turns + rolling summary/token window 重建
```

写入策略:

```text
1. 每次 patch 先写 Redis。
2. 每轮结束、关键工具证据产生、final answer 时写 DB snapshot。
3. checkpoint save_step 只记录 context_version/context_snapshot_ref, 不保存上下文主体。
4. DB snapshot 写失败不阻断主链路, 但要进入 timeline/warning 日志。
```

### 2.4 checkpoint 与 ContextState 的边界冲突

当前 checkpoint 如果继续保存 `messages`、拼接后的历史上下文或完整 `context_snapshot`, 就会形成第二套上下文模式。

处理:

```text
checkpoint 不再保存上下文主体。
checkpoint 只保存恢复当前 run 所需的执行元数据。
resume 时先恢复 ContextState, 再读取 checkpoint 的 next_step/step_refs 继续控制流。
```

checkpoint 保留:

```text
run_id / trace_id
next_step
route / route_reason
completed_step_refs
pending_tool_call_refs
idempotent_tools
resume_policy
context_version
context_snapshot_ref
```

checkpoint 剔除:

```text
完整 messages 快照
拼接后的 system prompt
历史 turns 副本
完整 context_snapshot 主体
raw tool result 大段内容
```

如果恢复时发现 `context_version` 与 Redis/DB snapshot 不一致:

```text
以 ContextStateStore.get_or_rebuild() 返回的状态为准。
checkpoint 只用于决定是否继续 step、是否禁止 replay 非幂等工具。
```

## 2.5 旧上下文模式剔除清单

为了保证系统只剩一套上下文模式, 以下旧模式必须退出主链路。

| 旧模式 | 当前问题 | 处理 |
|---|---|---|
| `ContextBuilder.abuild()` 每轮 DB 重读 | 每轮重新拼上下文, 与 ContextState 主路径重复 | 只保留为 Redis/DB snapshot miss 后的 rebuild 工具 |
| `history_messages` 作为主上下文 | 把历史 turns 再次作为 prompt 主体 | 改为 ContextState 的 `conversation` 视图区块, 不从 DB 每轮生成 |
| `rolling_summary` 正常参与 prompt | 摘要仍是历史文本路径 | 只在冷启动重建时生成 `conversation.cold_start_summary` |
| token-aware history window 正常裁剪历史 | 说明主链路仍依赖历史 turns | 只用于冷启动重建和视图渲染预算 |
| memory cache 当前会话缓存 | 与 ContextState 重叠, 容易双写不一致 | 完全移出上下文链路 |
| checkpoint 保存完整 messages/context_snapshot | 形成第二套上下文快照 | checkpoint 只保存恢复元数据和 ContextState 引用 |
| 工具结果完整文本进入上下文 | 白板膨胀成文本仓库 | ContextState 只保存结构化摘要和 raw_ref |

执行约束:

```text
1. 开启 harness_stateful_context_enabled 后, prompt 构建不得调用 conversation_service.get_turns。
2. ContextStateStore.get_or_rebuild 只有 miss/rebuild 时可以调用 ContextBuilder。
3. Harness 上下文链路不得调用 memory_cache。
4. HarnessCheckpointStore 新写入不得包含完整 messages/context_snapshot。
5. 所有上下文更新必须通过 ContextStore patch API。
```

## 3. 目标架构

### 3.1 分层

```text
HarnessState
  单次请求运行态: step / usage / timeline / answer_parts

Checkpoint
  单次 run 中断恢复: next_step / step_refs / resume_policy / context_version

ContextState
  会话级临时白板: goal / facts / plan / gaps / tool summaries

Conversation DB
  原始历史与审计: user message / assistant answer / raw turn metadata

Memory Cache
  本计划不接入: 不读、不写、不作为 ContextState fallback
```

### 3.2 ContextState 区块

第一版保留 7 个区块, 避免过度设计:

| 区块 | 写入者 | 说明 |
|---|---|---|
| `identity` | framework only | owner_key / session_id / schema_version |
| `intent` | framework + limited LLM | current_question / current_goal / user_corrections |
| `working` | framework + limited LLM | plan / completed_steps / pending_steps / blocker |
| `evidence` | framework only | observed facts, tool summaries, evidence gaps |
| `conversation` | framework only | recent turns snapshot, cold-start summary, last_turn_index |
| `tool` | framework only | recent tool calls, do_not_repeat, latency/status |
| `output` | framework + limited LLM | answer contract, required evidence, low-confidence rule |

移除原计划里的 `runtime` 区块:

```text
runtime 属于 HarnessState/checkpoint, 不放入会话白板, 避免 LLM 误改控制流。
```

### 3.3 证据写入权限

`evidence` 必须拆分事实和模型笔记:

```python
@dataclass(slots=True)
class EvidenceState:
    observed_facts: list[EvidenceItem] = field(default_factory=list)
    tool_summaries: list[ToolSummary] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    cannot_conclude_reasons: list[str] = field(default_factory=list)
    model_notes: list[str] = field(default_factory=list)
```

规则:

```text
framework 可以写 observed_facts/tool_summaries。
LLM 不允许写 observed_facts。
LLM 只能写 model_notes、hypotheses、working plan、open questions。
verifier 只信 observed_facts/tool_summaries, 不把 model_notes 当事实。
```

## 4. 新增文件与职责

```text
app/agent/context/
├── __init__.py
├── state.py
├── operations.py
├── store.py
├── views.py
├── tools.py
└── persistence.py

tests/
├── test_context_state.py
├── test_context_operations.py
├── test_context_store.py
├── test_context_views.py
├── test_context_tools.py
└── test_harness_stateful_context.py
```

职责:

| 文件 | 职责 |
|---|---|
| `state.py` | 定义 ContextState 和各区块 dataclass |
| `operations.py` | 提供受控 patch: set/append/merge/delete |
| `store.py` | Redis-first ContextStateStore, 带 DB snapshot fallback |
| `views.py` | 把 ContextState 渲染成给 LLM 的短视图 |
| `tools.py` | 第一版只暴露 `context_read`, `context_note` 可选; 不暴露 rollback |
| `persistence.py` | to_dict/from_dict/schema migration/snapshot 压缩 |

## 5. Store 设计

### 5.1 主存储策略

修正原计划:

```text
不要以进程内 Map 为主。
Redis 是主存储。
进程内 Map 只能做短 TTL LRU 读缓存, 可以第一版不做。
```

Key:

```text
{redis_namespace}:context:{owner_key}:{session_id}
```

Value:

```json
{
  "schema_version": 1,
  "version": 18,
  "owner_key": "...",
  "session_id": "...",
  "state": {},
  "patch_tail": [],
  "updated_at": "2026-07-08T..."
}
```

### 5.2 DB snapshot

需要新增一个持久化入口, 可以第一版放在 `conversation_service` 旁边:

```text
save_context_snapshot(owner_key, session_id, snapshot)
get_latest_context_snapshot(owner_key, session_id)
```

如果暂时不改数据库 schema, 第一版可以利用现有 conversation/session metadata 字段; 如果没有合适字段, 新增 `context_snapshots` 表或本地 JSON snapshot 存储。

Snapshot 只保存 compact 版本:

```text
保存 state 主体。
保存 version/schema_version/updated_at。
不保存完整 raw tool result。
不保存超过 patch_history_limit 的 patch。
```

### 5.3 Redis 丢失恢复

```python
async def get_or_rebuild(owner_key: str, session_id: str) -> AgentContextState:
    state = await redis_get(owner_key, session_id)
    if state and state.schema_version == CURRENT_SCHEMA:
        return state

    snapshot = await db_snapshot_get(owner_key, session_id)
    if snapshot and snapshot.schema_version == CURRENT_SCHEMA:
        await redis_set(snapshot)
        return snapshot

    rebuilt = await rebuild_from_conversation_turns(owner_key, session_id)
    await redis_set(rebuilt)
    await db_snapshot_save(rebuilt)
    return rebuilt
```

## 6. ContextBuilder 降级方案

`ContextBuilder` 不删除, 但不再是正常路径。

新定位:

```text
1. Redis miss 时从 conversation turns 构造初始 conversation 区块。
2. DB snapshot miss 时生成 cold-start summary。
3. 作为 harness_stateful_context_enabled=False 时的完整旧路径。
```

需要新增配置:

```python
harness_stateful_context_enabled: bool = False
harness_context_rebuild_from_turns_enabled: bool = True
harness_context_view_token_budget: int = 4000
harness_context_redis_ttl_seconds: int = 86400
harness_context_db_snapshot_enabled: bool = True
harness_context_patch_history_limit: int = 200
harness_context_tools_enabled: bool = True
harness_context_llm_patch_enabled: bool = False
```

旧配置保留但降级:

```text
harness_rolling_summary_enabled
harness_history_token_window_enabled
harness_history_token_budget
harness_history_max_turns
```

这些配置只在 fallback/rebuild 时生效。

## 7. 工具暴露策略

修正原计划:

```text
第一版不要暴露 context_patch/context_rollback 给 LLM。
```

第一版工具:

| 工具 | 是否暴露给 LLM | 说明 |
|---|---|---|
| `context_read` | 是 | 读取白板摘要或指定区块 |
| `context_note` | 可选 | 只允许写 `working.model_notes` 或 `intent.pending_hypotheses` |
| `context_patch` | 否 | framework/internal only |
| `context_rollback` | 否 | 调试 API, 不注册为 RuntimeTool |

原因:

```text
事实证据不能让 LLM 自己写。
rollback 是运维/调试能力, 不应该暴露给模型。
```

## 8. Harness 接入点

### 8.1 `_stream_inner` context 阶段

旧路径:

```text
ContextBuilder.abuild(...) -> context.system_prompt + history_messages
```

新路径:

```text
context_state = context_store.get_or_rebuild(owner_key, session_id)
framework_patch current_question/current_goal
system_prompt = render_context_view(context_state, tools)
recent_message_view = render_recent_message_view(context_state)
```

要求:

```text
所有状态更新必须通过 ContextStore.framework_patch。
禁止直接 state_ctx.xxx = value。
不再把 DB turns 渲染为独立 history_messages 列表作为主上下文。
recent_message_view 只是 ContextState 的一个视图区块。
```

### 8.2 `_execute_tools` 后写 evidence

工具结果不能直接把大段文本塞进 ContextState。

应写结构化摘要:

```json
{
  "tool": "check_redis_health",
  "status": "success",
  "latency_ms": 2032,
  "facts": ["Redis PING 成功"],
  "gaps": [],
  "raw_ref": "timeline:event-id"
}
```

原始完整结果仍在:

```text
timeline_events
conversation/audit log
```

checkpoint 只保存 `raw_ref` / `step_ref`, 不保存完整工具结果。

### 8.3 final answer 后写 snapshot

请求结束时:

```text
1. 更新 output.last_answer_summary。
2. 更新 conversation.last_turn_index。
3. 保存 Redis。
4. 保存 DB snapshot。
5. 原始对话照常写 conversation DB。
6. 不写 memory cache; 当前会话状态只保存在 Redis ContextState 与 DB snapshot。
```

## 9. checkpoint 瘦身与衔接

目标:

```text
checkpoint 不再成为第二套上下文。
checkpoint 只负责 run 恢复, 不负责上下文保存。
```

正确接入:

```text
save_step 时保存 context_version/context_snapshot_ref。
mark_completed 时只标记 run 完成, 不写上下文主体。
try_resume 时先通过 ContextStore 恢复上下文, 再使用 checkpoint 的 next_step/step_refs 恢复执行进度。
```

`CheckpointResume` 增加:

```python
context_version: int | None = None
context_snapshot_ref: str | None = None
```

`HarnessCheckpointStore.save_step(...)` 增加可选参数:

```python
context_version: int | None = None
context_snapshot_ref: str | None = None
```

兼容性:

```text
旧 checkpoint 里的 messages/context_snapshot 只作为迁移期兼容字段读取。
新 checkpoint 不再写 messages/context_snapshot 主体。
schema_version 不匹配时忽略 checkpoint 中的上下文残留, 只走 ContextStore.get_or_rebuild。
```

## 10. memory cache 去冲突规则

新增明确规则:

```text
ContextState 永远不写入 memory cache。
memory cache 永远不作为当前会话上下文读取来源。
Harness 上下文构建链路不调用 memory_cache。
本计划不设计 memory promotion。
长期经验如果后续确实需要, 必须另起独立计划, 不复用当前会话白板。
```

## 11. 实施步骤

### Task 1: 定义 ContextState

文件:

- 新增 `app/agent/context/state.py`
- 新增 `tests/test_context_state.py`

验收:

```text
AgentContextState 可以创建默认状态。
schema_version/version/updated_at 存在。
identity/intent/working/evidence/conversation/tool/output 区块存在。
```

### Task 2: 实现受控 patch

文件:

- 新增 `app/agent/context/operations.py`
- 新增 `tests/test_context_operations.py`

验收:

```text
framework_patch 可以写所有 framework-owned 字段。
llm_note 只能写 model_notes/pending_hypotheses/working plan。
LLM 写 observed_facts/identity 时被拒绝。
每次 patch version +1。
patch_tail 超过限制会丢弃最旧项。
```

### Task 3: 实现 Redis-first ContextStore

文件:

- 新增 `app/agent/context/store.py`
- 修改 `app/services/redis_client.py` 仅在需要时复用现有 async client, 不新增连接池
- 新增 `tests/test_context_store.py`

验收:

```text
Redis 命中时不读 conversation DB。
Redis miss 时尝试 DB snapshot。
DB snapshot miss 时才 rebuild from turns。
Redis 写失败时返回 warning, 不阻断主链路。
```

### Task 4: 增加 DB snapshot 入口

文件:

- 修改 `app/services/conversation_service.py` 或新增 `app/services/context_snapshot_service.py`
- 新增对应测试

验收:

```text
save_context_snapshot/get_latest_context_snapshot 可保存 compact snapshot。
snapshot 不包含 raw tool result。
schema_version 不匹配时返回不可用状态。
```

### Task 5: 实现视图渲染

文件:

- 新增 `app/agent/context/views.py`
- 新增 `tests/test_context_views.py`

验收:

```text
render_context_view 输出包含 current_goal、observed_facts、evidence_gaps、pending_steps、tool summaries。
6k token 输入下输出不超过 harness_context_view_token_budget。
model_notes 明确标注为 "模型笔记/未验证"。
```

### Task 6: 降级 ContextBuilder

文件:

- 修改 `app/agent/harness/context.py`
- 修改 `app/agent/harness/loop.py`
- 新增/修改 `tests/test_harness_stateful_context.py`

验收:

```text
harness_stateful_context_enabled=False 时旧行为不变。
harness_stateful_context_enabled=True 且 Redis 命中时不调用 conversation_service.get_turns。
Redis miss 时只重建一次, 后续同 session 命中 Redis。
```

### Task 7: 工具结果写入 ContextState

文件:

- 修改 `app/agent/harness/loop.py`
- 新增测试

验收:

```text
工具成功结果写入 evidence.tool_summaries。
工具失败结果写入 evidence_gaps 或 tool status。
不会把完整 result.content 大段写入 ContextState。
timeline_event_id/raw_ref 可追溯到原始结果。
```

### Task 8: 注册安全上下文工具

文件:

- 新增 `app/agent/context/tools.py`
- 修改 `app/agent/harness/registry.py`
- 新增 `tests/test_context_tools.py`

验收:

```text
context_read 可注册可调用。
context_note 如果开启, 只能写允许字段。
context_patch/context_rollback 不出现在模型可用工具列表。
```

### Task 9: checkpoint 瘦身为恢复元数据

文件:

- 修改 `app/services/harness_checkpoint.py`
- 修改 `app/agent/harness/loop.py`
- 新增/修改 checkpoint 测试

验收:

```text
save_step 写入 context_version/context_snapshot_ref。
try_resume 返回 context_version/context_snapshot_ref。
新 checkpoint 不再写完整 messages/context_snapshot。
旧 checkpoint 中的 messages/context_snapshot 仅迁移期读取, 不再作为上下文主来源。
schema mismatch 忽略 checkpoint 上下文残留, 转 ContextStore.get_or_rebuild。
```

### Task 10: 剔除 memory cache 上下文链路

文件:

- 检查 `app/services/memory_cache.py`
- 检查 `app/services/experience_memory_service.py`
- 检查调用链中是否把当前 session context 写入或读自 memory cache
- 新增测试或断言

验收:

```text
当前会话 ContextState 不写 memory cache。
memory cache 不参与 ContextState get_or_rebuild 的读取优先级。
Harness 上下文构建链路不调用 memory_cache。
本计划不实现 memory promotion。
```

## 12. 验收标准

- [ ] Redis 命中时, 单次请求不调用 `conversation_service.get_turns` 构建上下文。
- [ ] Redis miss + DB snapshot 命中时, 不走 rolling summary。
- [ ] Redis miss + DB snapshot miss 时, 才使用 conversation turns + rolling summary/token window 重建。
- [ ] `ContextState` 中没有 raw 大段工具结果。
- [ ] `observed_facts` 只能由 framework 写入。
- [ ] LLM 工具列表不包含 `context_rollback`。
- [ ] memory cache 不保存当前会话上下文。
- [ ] checkpoint `save_step` 只保存 `context_version/context_snapshot_ref`, 不保存上下文主体。
- [ ] 关闭 `harness_stateful_context_enabled` 后旧路径行为不变。
- [ ] 端到端 P50 不高于旧路径, 超时率不升高。

## 13. 回退方案

一级回退:

```text
harness_stateful_context_enabled=False
```

效果:

```text
恢复旧 ContextBuilder.abuild 主路径。
ContextStateStore 不参与 prompt 构建。
checkpoint 的 context_version/context_snapshot_ref 读取忽略。
```

二级回退:

```text
harness_context_db_snapshot_enabled=False
```

效果:

```text
只使用 Redis ContextState。
Redis miss 时从 conversation turns 重建。
```

三级回退:

```text
harness_context_tools_enabled=False
```

效果:

```text
LLM 不可调用 context_read/context_note。
framework 仍可维护 ContextState。
```

## 14. 本计划刻意不做

第一版不做以下能力:

- 不允许 LLM 任意 `context_patch`。
- 不把 `context_rollback` 暴露给 LLM。
- 不把当前会话 ContextState 写入 memory cache。
- 不把 rolling summary/token window 作为正常主路径。
- 不以进程内 Map 作为 ContextState 主存储。
- 不把 raw tool result 存入 ContextState。

这些限制是为了保证证据链可信、状态单一来源清晰、Redis 丢失可恢复。
