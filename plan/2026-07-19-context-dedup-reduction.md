# Context Dedup / Reduction Implementation Plan

## Problem

Stateful 主路径落地后，发给模型的上下文仍有多层结构性重复与过宽注入：

1. **当前问题双写**：白板 `intent.current_question/current_goal` 进入 system view，同一 `composed_message` 又作为最后一条 user message。
2. **附件三通道重叠**：API 可能把附件全文塞进 user message；白板/legacy 再放 `active_attachment_refs` / active index；模型还可通过 `read_attachment` 再读。
3. **历史 stamp 放大**：`_stamp_current_turn` 把本轮 user 文本（含附件全文）写进 `conversation.recent_turns`，后续轮 history 继续携带大段附件。
4. **偏好多处注入**：router、harness system、fallback 都可调用 `format_for_prompt`；同轮不同 LLM 调用各自带一份偏好文本。
5. **白板 view 偏宽**：`render_context_view` 默认预算 4000 token，且与 history/tool results 并存；`cold_start_summary` 字段几乎未用，但 schema 仍占位。
6. **双路径骨架耦合**：stateful 仍调用 `ContextBuilder._build_system_prompt` 拼 base prompt，legacy 配置（rolling summary 等）语义易混淆。

目标：在**不破坏多轮连续性、附件可引用、只读红线、SSE 契约**的前提下，降低 token 浪费与重复语义，并保留一键降级开关。

## Decisions and defaults

### D1. 单一真相源（Single Source of Truth）

| 信息 | 唯一主注入位 | 白板/其他侧 |
|---|---|---|
| 本轮用户问题原文 | 最后一条 `user` message | intent 只存**短目标**，默认**不**把完整 composed message 再渲染进 system view |
| 附件全文 | 仅当本轮显式需要时进入 **user message**（或 `read_attachment` 结果） | 白板只保留 `active_attachment_refs` 索引（file_id/name/summary） |
| 近期对话原文 | `history_messages`（来自 `recent_turns`） | view **不**再渲染 recent turns 全文 |
| 结构化进度/证据 | system 中的 whiteboard view | history 不复制 observed_facts 全文 |
| 用户偏好 | harness system **一处** | router 可继续读（独立调用）；不向 fallback 重复追加已在主路径注入过的大段文本，除非主路径未跑 |
| 经验/知识/日志/指标 | 工具结果按需 | 禁止静默预注入 system |

### D2. Intent 去重策略（默认开启）

- 新增行为开关：`HARNESS_CONTEXT_INTENT_OMIT_FROM_VIEW_ENABLED`（默认 **true**）。
- 当开关为 true：
  - `set_intent` 仍写入 `current_question` / `current_goal`（供 debug / context_read / 后续轮状态）。
  - `render_context_view` **默认不输出** `current_question`；`current_goal` 仅在与当前 user message **实质不同**时输出（例如用户纠正后框架改写的短目标）。
  - harness 传入 intent 时：
    - `current_question` = **原始问题**（无附件全文包装），若调用方只能给 composed message，则先 `strip_attachment_wrapper`。
    - `current_goal` = 短目标；首轮可等于原始问题，但 view 仍按上条规则省略。
- 当开关为 false：保持今日行为（view 含 question/goal），用于对照/回滚。

### D3. 附件分层（默认开启“索引优先”）

- 新增：`HARNESS_ATTACHMENT_PROMPT_MODE` = `full` | `summary` | `index`（默认 **`summary`**）。
  - `full`：现状——本轮可把全文 compose 进 user message（兼容旧行为）。
  - `summary`：**默认**——user message 只带 summary/index 块 + 原始问题；全文仅在 `should_reload_full_content` 或用户显式要求时升级为 full。
  - `index`：user message 只带 file_id 列表提示，细节一律 `read_attachment`。
- 白板始终只存 refs（已实现），**禁止**把 runtime 全文写入 `recent_turns`。
- `_stamp_current_turn` / history stamp：
  - user 侧只 stamp **原始问题**（或 summary 级 user_context），不 stamp 附件全文。
  - 若必须保留附件线索，只 stamp `attachment_refs` 元数据（conversation DB 已有），whiteboard recent_turns 用短标记如 `[attachment:file_xxx]`。

### D4. 偏好注入收敛

- 新增：`HARNESS_PREFERENCE_INJECT_MODE` = `harness_only` | `router_and_harness`（默认 **`router_and_harness`** 保持路由质量；文档标明后续可收紧为 `harness_only`）。
- 本阶段最小改动：
  - harness base prompt **保留** preference（主回答路径需要）。
  - fallback 若检测到 runtime 已带 preference 段则不再二次 `format_for_prompt`（可选微优化）。
  - 不把 preference 写入白板 view。

### D5. 白板 view 瘦身

- 保持 `harness_context_view_token_budget` 默认 4000，但调整渲染优先级：
  1. working / evidence / gaps / do_not_repeat（高）
  2. attachment refs（中）
  3. model_notes / hypotheses（中低）
  4. current_goal（条件输出）
  5. current_question（默认省略）
- `tool_summaries` 与 `observed_facts` 若同源，view 只保留更短的一侧摘要（默认保留 `tool_summaries` + facts 去重后的短列表；细节由 tool transcript 承担）。
- `cold_start_summary`：本阶段**不渲染**；仅 rebuild 路径可写入，供 debug snapshot。

### D6. 不在本计划删除的内容

- 不物理删除 `ContextBuilder` / rolling summary 代码（H4 双路径政策不变）。
- 不改 SSE event `type` 语义。
- 不自动执行生产处置；附件仍为不可信证据。
- 不把 experience memory 改回 system 预注入。

## Scope

### Phase A — 去重（低风险，默认开）

1. Intent view 省略 / 条件 goal（D2）。
2. API / harness 传入 intent 时剥离附件包装，区分 `raw_question` vs `composed_message`。
3. `_stamp_current_turn` 禁止把附件全文写入 `recent_turns`。
4. 单测：system 不含完整当前问题；user 含问题；多轮 history 无附件全文膨胀。

### Phase B — 附件模式（中风险，默认 summary）

1. `HARNESS_ATTACHMENT_PROMPT_MODE` + assistant compose 分支。
2. `full` 兼容旧测；新默认 `summary`；显式 reload 仍可 full。
3. 更新 assistant/harness 附件相关测试期望。

### Phase C — view 瘦身与偏好微调（低中风险）

1. `render_context_view` 优先级与 tool_summaries/facts 去重。
2. fallback preference 重复注入微优化（可选）。
3. 文档：`docs/pilot/context-dual-path.md` 增补 “dedup rules” 小节。

### Phase D — 观测与退出

1. 在 harness start/complete payload 或 debug 日志增加粗粒度上下文度量（可选开关，默认关）：
   - `system_chars`, `history_chars`, `user_chars`, `view_chars`
2. 记录 focused 测试与一次本地 smoke 的前后对比（至少手工 1 条带附件多轮）。

## Non-goals

- 不做向量压缩 / 外部 summarizer 替换白板。
- 不改 checkpoint schema 主版本（除非 stamp 字段兼容需要；优先不改 schema）。
- 不删除 legacy ContextBuilder。
- 不调整 RAG tenant scope / Milvus 集合。
- 不做前端 UI 的 “context mode” 开关。

## Affected files

| Area | Files |
|---|---|
| Config / flags | `app/config.py`, `.env.example` |
| View / intent | `app/agent/context/views.py`, `app/agent/context/integration.py`, `app/agent/context/operations.py`（若需） |
| Harness wiring | `app/agent/harness/stream_inner.py`, `app/agent/harness/checkpoint_ops.py` |
| Attachment / API | `app/api/assistant.py`, `app/services/attachment_reference_service.py` |
| Fallback pref（可选） | `app/agent/harness/fallback.py` |
| Router preference mode | `app/services/router_service.py` |
| Docs | `docs/pilot/context-dual-path.md`（增补）、本 plan |
| Tests | `tests/test_context_views.py`, `tests/test_harness_stateful_context.py`, `tests/test_harness_service.py`（附件/两轮）, 新增 `tests/test_context_dedup.py` |
| Index | `AGENTS.md` Current Plan Index |

## Flags（新增，均有降级）

| Env | Default | Meaning |
|---|---|---|
| `HARNESS_CONTEXT_INTENT_OMIT_FROM_VIEW_ENABLED` | `true` | view 省略 current_question；goal 条件输出 |
| `HARNESS_ATTACHMENT_PROMPT_MODE` | `summary` | `full` \| `summary` \| `index` |
| `HARNESS_PREFERENCE_INJECT_MODE` | `router_and_harness` | 预留收紧；本阶段行为基本不变 |
| `HARNESS_CONTEXT_VIEW_DEDUP_TOOL_EVIDENCE` | `true` | view 内 tool_summaries/facts 去重 |
| `HARNESS_CONTEXT_SIZE_METRICS_ENABLED` | `false` | 调试用上下文体积日志 |

回滚：全部切回旧语义：

```bash
HARNESS_CONTEXT_INTENT_OMIT_FROM_VIEW_ENABLED=false
HARNESS_ATTACHMENT_PROMPT_MODE=full
HARNESS_CONTEXT_VIEW_DEDUP_TOOL_EVIDENCE=false
```

## Implementation sketch

### A1. 原始问题与 compose 分离

`assistant.py`：

```python
raw_question = request.question
composed_message = _compose_message(raw_question, attachment_payload.runtime_context)
# stream 仍传 composed_message 作为 user 输入
# 新增可选参数 raw_question / 或 harness 内 strip
```

`stream_inner.py` stateful 分支：

```python
intent_question = raw_question or strip_attachment_wrapper(message)
stateful_ctx = await build_stateful_context(
    current_question=intent_question,
    current_goal=intent_question,  # 首轮短目标；后续可由框架改写
    ...
)
# messages user 仍用 message（composed）
```

`strip_attachment_wrapper`：识别 `_compose_message` 固定前缀/后缀，提取「用户问题：」后文本；无法识别则原样返回。

### A2. view 省略

`render_context_view`：

- 若 `omit_intent_question`：跳过 `current_question` 行。
- `current_goal`：空则跳过；若与 `current_question` 相同且 omit 开启则跳过；仅不同时输出。

### A3. stamp 瘦身

`_stamp_current_turn(user_message=...)` 调用点改为传入 **raw question**（或截断后的短文本），assistant 答案仍可按 `answer_max_chars` 截断。

### B1. attachment mode

`_load_attachment_payload` / `_compose_message`：

- `summary`：`runtime_context = persistent_context`（summary），除非 `should_reload_full_content`。
- `index`：`runtime_context` 仅为 file_id 列表说明，引导 `read_attachment`。
- `full`：现逻辑。

### C1. evidence 去重渲染

同一 `raw_ref` 或同 tool+status 的 fact 与 summary 合并为一行；超出 `max_items` 用 `…(+N more)`。

## Verification commands

```bash
# focused unit
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_context_views.py \
  tests/test_context_integration.py \
  tests/test_harness_stateful_context.py \
  tests/test_context_dedup.py \
  -q --no-cov

# soft-path regression (existing harness/assistant attachment + two-turn)
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_harness_service.py \
  -k 'attachment or two_turn or history or context_builder or stateful' \
  -q --no-cov

# static
PYTHONPATH=. .venv/bin/python -m compileall -q app
PYTHONPATH=. .venv/bin/ruff check --select E9,F63,F7,F82 app tests
git diff --check
```

Optional manual smoke（本地服务已起时）：

1. 无附件两轮追问：第二轮 messages = system + history(user,assistant) + user；system **不含**完整当前句。
2. 带附件一轮：user 含 summary 或 full（按 mode）；system 仅 file_id 索引；`recent_turns` 无全文。
3. `HARNESS_ATTACHMENT_PROMPT_MODE=full` 回放旧行为。

## Exit criteria

- [x] 默认配置下，stateful 路径 system prompt **不包含**与最后一条 user 完全相同的当前问题全文。
- [x] 默认附件模式 `summary`：未触发 reload 时 user message **不含**附件全文；refs 仍在白板且 `read_attachment` 可用。
- [x] `_stamp_current_turn` / 多轮 history **不**累积附件全文。
- [x] `HARNESS_ATTACHMENT_PROMPT_MODE=full` + intent omit false 可回到旧 prompt 可观察行为（history stamp 仍受瘦身保护）。
- [x] 既有 soft-path 检查（ContextBuilder 附件/history、stateful 关键路径）通过；新增 dedup 单测通过；旧认证依赖签名断言另行记录。
- [x] 无 SSE `type` 变更；无新生产处置能力；密钥不入库。
- [x] 进度写入本 plan，并更新 AGENTS 索引状态。

## Risks and rollback

| Risk | Mitigation |
|---|---|
| 模型看不到 system 里的问题后“忘记”目标 | user message 仍是权威问题；goal 条件输出；可关 omit 开关 |
| summary 模式导致附件细节不足 | `should_reload_full_content` + `read_attachment`；mode=`full` 回滚 |
| 旧测试断言 system 含 `current_goal: <question>` | 更新断言为 intent 存储或条件渲染；保留 flag off 兼容测 |
| stamp 改短后 rebuild 信息变少 | rebuild 仍可读 conversation DB turns；DB 仍存 persistent attachment summary |
| 偏好收敛影响路由 | 本阶段默认仍 `router_and_harness` |

Rollback path：仅改回本 plan 涉及的 flags/代码；不碰 live Milvus/SQLite 数据；不重置无关工作区。
`HARNESS_ATTACHMENT_PROMPT_MODE=full` restores full prompt composition; bounded
whiteboard history stamps remain in force to prevent accidental historical
attachment amplification.

## Test plan（新增断言要点）

1. `test_render_context_view_omits_current_question_when_enabled`
2. `test_render_context_view_shows_goal_only_if_distinct`
3. `test_stateful_system_prompt_does_not_repeat_user_question`
4. `test_stamp_current_turn_strips_attachment_fulltext`
5. `test_attachment_mode_summary_does_not_compose_fulltext`
6. `test_attachment_mode_full_compat`
7. 既有：`test_stateful_context_redis_hit_skips_legacy_context_builder`（更新 goal 断言）
8. 既有：assistant attachment / two-turn history

## Work sequence

1. 写 flags + config/.env.example 注释。
2. Phase A：view omit + strip + stamp 瘦身 + 单测。
3. Phase B：attachment mode + API 测试更新。
4. Phase C：view evidence 去重 + 文档。
5. 跑 verification；记录证据；索引标为已实现/待验证。

## Progress

- 2026-07-19：用户已批准本计划，进入实施。
- 2026-07-19：Phase A–C 已实现：新增独立配置开关；stateful intent 归一化并默认省略重复问题；stamp 清理当前及历史附件包装；附件 `summary`（默认）/`index`/`full` 三模式；view 优先级调整与 tool/fact 去重；router preference mode 与可选上下文体积指标。
- 2026-07-19：新增 `tests/test_context_dedup.py`，并更新受新默认语义影响的 view/stateful 断言。
- 2026-07-19：验证证据：`tests/test_context_*.py` **103 passed**；去重专项 **12 passed**；`test_m1_w2_context_checkpoint.py` stamp 回归 **2 passed**；stateful 关键路径（Redis hit、附件 refs、flags）**3 passed**；attachment/history ContextBuilder soft checks **9 passed**；compileall、ruff（E9/F63/F7/F82）、`git diff --check` 全部通过。
- 2026-07-19：材料化 API 旧测试中仍有若干 pre-existing `assistant(..., owner_key=...)` 断言与当前认证依赖签名不一致，未将其归因于本计划改动；未修改认证边界。
