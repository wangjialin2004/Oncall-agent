# Plan：升级短期记忆召回窗口（解决"只记住最近 6 轮"）

- 日期：2026-06-22
- 主题：会话超过 6 轮后丢失更早上下文，表现为"忘记之前的记忆"
- 选定方向：**待用户确认**（本文给出分层方案 + 推荐，最终范围需签字）
- 状态：仅计划（本文件只描述如何做，不含代码改动；进入实现需用户解除 CLAUDE.md 限制）

---

## 1. 背景与根因（一句话）

短期记忆并未丢失存储，而是**召回时被按轮数硬截断**：`ContextBuilder._load_recent_turns` 把全量历史裁成"最后 `harness_history_max_turns=6` 轮"再喂给 LLM，会话一旦超过 6 轮，更早的问答就不再进入上下文。

证据定位：

- 截断点（核心）：[app/agent/harness/context.py:80](../app/agent/harness/context.py#L80) `return turns[-self.history_max_turns:]`
- 默认值 6：[app/config.py:57](../app/config.py#L57) `harness_history_max_turns: int = 6`
- 窗口取值逻辑：[app/agent/harness/context.py:34-39](../app/agent/harness/context.py#L34-L39)
- 历史拼装为消息：[app/agent/harness/context.py:82-92](../app/agent/harness/context.py#L82-L92)（仅注入 `user_message` + `assistant_answer`，不含 events）
- 全量已持久化：[app/services/conversation_service.py:115-139](../app/services/conversation_service.py#L115-L139) `get_turns` 返回全部轮次
- 线上路径确认：[app/api/assistant.py:43](../app/api/assistant.py#L43) `stream_service = harness_service`

> 关键事实 1：此处"1 轮" = 一条 DB 记录 = 一次"用户问 + 助手答"。所以 6 = 最近 6 次问答。
> 关键事实 2：历史 token **不计入** `harness_token_budget`（[app/agent/harness/state.py:40-54](../app/agent/harness/state.py#L40-L54) 只累计 system_prompt 与答案）。调大窗口会让真实 prompt 变长，但当前预算守卫看不到它。
> 关键事实 3：`harness_history_max_turns` 是 Pydantic Settings 字段，已支持 `HARNESS_HISTORY_MAX_TURNS` 环境变量覆盖——**零代码即可临时调大**。

---

## 2. 目标设计（分层方案）

按"投入 / 鲁棒性"从低到高，给出 4 层。建议至少做到 Tier 2，Tier 3 视"是否要求永不遗忘"再定。

### Tier 1 — 直接调大窗口（零代码 / 一行默认值）

- 做法：把 `harness_history_max_turns` 由 6 提到 12~20。两种落地：
  - 临时：`.env` 设 `HARNESS_HISTORY_MAX_TURNS=16`，不动代码、可立即验证。
  - 永久：改 [app/config.py:57](../app/config.py#L57) 默认值。
- 优点：成本几乎为零，立刻缓解。
- 缺点：token 随轮数线性增长；单条 `assistant_answer` 可能很长，长答案会迅速撑大 prompt；无 token 安全阀；最终会触达模型上下文上限与成本上限。**只是缓解，不是根治。**

### Tier 2 — Token 感知滑动窗口（推荐核心改造）

- 做法：召回时从最新轮往旧轮累加 `estimate_tokens`，直到达到新预算 `harness_history_token_budget`（建议 6000~8000），保留"能放下的最多轮次"；同时保留一个轮数硬上限兜底。复用现有 [app/agent/agent_loop.py](../app/agent/agent_loop.py) 的 `estimate_tokens`。
- 可选增强：对单条过长 `assistant_answer` 在注入历史时做压缩/截断（如保留前 N 字 + "…(已折叠)"），避免一条长答案吃光预算。
- 优点：对"长短不一的轮次"鲁棒，在安全 token 边界内最大化保留上下文；天然给历史加上预算护栏。
- 缺点：超预算的更早轮次仍会被丢弃（不是"永不遗忘"）。

### Tier 3 — 滚动摘要（真正的"长记忆"，可选）

- 做法：最近 N 轮逐字保留 + 维护一段"更早对话的滚动摘要"注入 system_prompt。超出窗口的轮次被增量摘要进这段文本。
- 存储：在 `conversations` 表加一列存摘要，或每次按需重算（latency/成本权衡）。需要一次 LLM 摘要调用。
- 优点：在固定 token 成本下实现近似无限记忆，真正缓解"忘记早期事实"。
- 缺点：复杂度最高；新增 LLM 调用带来延迟与成本；摘要质量有风险（可能丢关键细节）；需管理摘要更新时机与失效。

### Tier 4 — 历史语义检索（未来增强，可选）

- 做法：对每轮做向量化，查询时检索 top-k 相关历史 + 最近 N 轮拼接。项目已有向量基建（experience_memory / Milvus）。
- 优点：可扩展到超长会话，按相关性而非时间距离召回早期上下文。
- 缺点：工程量最大；检索相关性有风险；与现有"逐字历史"拼接策略需协调。

**推荐路线**：Phase 1（Tier 1 立即缓解，仅 env）→ Phase 2（Tier 2 作为正式上线方案）。Phase 3（Tier 3）作为"要求永不遗忘"时的后续迭代。Tier 4 暂列为远期选项。

---

## 3. 实施阶段与关键任务

> 以下为 Phase 1 + Phase 2 的实施描述；Phase 3 仅列要点，待决定是否启动。

### 阶段 0：目标确认（前置，需用户签字）

- 确认目标语义：是"把窗口调大到 N 轮"，还是"在 token 预算内尽量多留"，还是"永不遗忘（摘要/检索）"。
- 确认每请求可接受的历史 token / 成本 / 延迟预算上限。
- 产出：选定 Tier 与具体数值，作为阶段 2 输入。

### 阶段 1：立即缓解（Tier 1，零代码或一行）

- 关键任务：在 `.env` 设置 `HARNESS_HISTORY_MAX_TURNS`（建议 16）并验证多轮不再遗忘；或同时调整 [app/config.py:57](../app/config.py#L57) 默认值。
- 涉及文件：`.env`（或 `app/config.py` 单行）。
- 依赖：无。可与阶段 0 并行作为临时止血。

### 阶段 2：Token 感知滑动窗口（Tier 2，核心）

- 关键任务：
  1. `app/config.py` 新增 `harness_history_token_budget: int = 6000`（沿用现有注释风格），并保留 `harness_history_max_turns` 作为硬上限兜底。
  2. `ContextBuilder._load_recent_turns`：由 `turns[-N:]` 改为"从最新向旧累加 token 直到预算用尽"的选择逻辑；仍受 `harness_history_max_turns` 上限约束。复用 `estimate_tokens`。
  3.（可选）`_turns_to_messages` 或新辅助函数：对超长 `assistant_answer` 注入时压缩/截断。
  4.（建议）评估是否把历史 token 计入 `state.add_text_budget`，让 `over_budget` 守卫能看到历史占用（见第 7 节风险）。
- 涉及文件：`app/agent/harness/context.py`、`app/config.py`，（可选）`app/agent/harness/loop.py` / `state.py`。

### 阶段 3：测试调整与补充

- 关键任务：
  1. 现有用例 [tests/test_harness_service.py:736](../tests/test_harness_service.py#L736)、[tests/test_harness_service.py:764](../tests/test_harness_service.py#L764) 用 `history_max_turns=1/3` 断言行为，需评估是否受新逻辑影响并相应调整。
  2. 新增用例：构造长会话（如 20 轮），断言（a）Tier 1 下保留 N 轮；（b）Tier 2 下保留"token 预算内最多轮次"且不超预算；（c）单条超长答案被正确压缩/不撑爆预算。
- 涉及文件：`tests/test_harness_service.py`。

### 阶段 4（仅 Tier 3，待决定）

- 关键任务：表结构加摘要列或按需重算；在召回时拼接"滚动摘要 + 最近 N 轮"；新增摘要 LLM 调用与更新时机控制。
- 涉及文件：`app/services/conversation_service.py`、`app/agent/harness/context.py`、`app/config.py`，相应测试。
- 前置：阶段 0 明确要求"永不遗忘"。

---

## 4. 依赖与前置条件

- 阶段 0 目标与数值签字（最关键前置）。
- 确认接受历史 token / 成本 / 延迟的上升（Tier 1/2 必然带来）。
- 确认是否需要 Tier 3（决定是否进入阶段 4 与是否改表结构）。
- 用户明确授权解除 CLAUDE.md 限制、进入实现阶段（当前仅计划）。

---

## 5. 验证方式

- 单元测试：`pytest tests/test_harness_service.py -k history`（新增/调整用例全绿）。
- 行为验证：开一个长会话（>10 轮），在早期轮埋入一个事实（如某 service 名 / 阈值），在第 12+ 轮提问引用该事实，确认助手仍能召回。
- 预算验证：Tier 2 下打印/断言历史注入 token ≤ `harness_history_token_budget`，且长答案场景不超限。
- 回归：恢复默认配置后旧用例行为不变。

---

## 6. 回滚 / 降级方案

- Tier 1：改回 `HARNESS_HISTORY_MAX_TURNS`/默认值即回滚，无代码风险。
- Tier 2：建议 token 感知逻辑在 `harness_history_token_budget<=0` 或开关关闭时退化为旧"按轮数截断"，实现一键降级。
- Tier 3：摘要功能加独立开关，异常时降级为"仅最近 N 轮逐字"。

---

## 7. 风险点与阻塞项

| 级别 | 风险 | 说明 / 缓解 |
| --- | --- | --- |
| 高 | token 成本 / 延迟上升 | 召回更多历史 → prompt 变大、每请求更贵更慢。Tier 2 用 token 预算封顶；数值需阶段 0 定。 |
| 高 | 历史不计入预算守卫 | 当前 `over_budget` 看不到历史 token（[state.py:52-54](../app/agent/harness/state.py#L52-L54)），调大窗口可能让真实 prompt 远超 16000 而不触发降级。阶段 2 建议把历史计入预算。 |
| 中 | 长答案撑爆上下文 | 单条 `assistant_answer` 可能很长，少数几轮即吃满预算。需注入时压缩/截断。 |
| 中 | 触达模型上下文上限 | 纯 Tier 1 无上限保护，极长会话可能超模型窗口。Tier 2 的 token 封顶可消除。 |
| 中 | 摘要丢关键细节（Tier 3） | 滚动摘要可能丢失后续需要的具体值。靠"最近 N 轮逐字 + 摘要"组合缓解。 |
| 低 | 改动既有测试期望 | 现有用例固化了按轮数行为，需同步调整。 |
| 阻塞 | 目标 / 数值未签字 | 阻塞阶段 2。 |
| 阻塞 | 未授权进入实现 | 当前停留在计划层。 |

---

## 8. 假设与待确认

1. 假设线上稳定走 harness 路径（[app/api/assistant.py:43](../app/api/assistant.py#L43) 已确认），无其他历史召回入口。
2. 假设"短期记忆"指 harness 的逐字对话历史，而非 experience_memory / user_preference 这类长期记忆库。
3. 待确认：目标是"调大轮数"、"token 预算内尽量多留"还是"永不遗忘"？（决定做到哪个 Tier）
4. 待确认：每请求可接受的历史 token / 成本 / 延迟上限。
5. 待确认：是否将历史 token 计入 `harness_token_budget` 守卫（建议是）。
6. 待确认：是否允许对历史中的超长答案做压缩/截断。
