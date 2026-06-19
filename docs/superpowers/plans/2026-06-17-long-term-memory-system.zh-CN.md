# 长期记忆系统实施计划

> **给 agent 工作者：** 必须按任务逐步执行本计划。建议使用 `superpowers:subagent-driven-development`，也可以使用 `superpowers:executing-plans`。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 在 Router + 扁平 Expert 架构中重新引入长期记忆，覆盖 L1 诊断经验召回、L2 服务知识与基线、L3 显式用户偏好，同时不回到旧的 OnCall Planner 流水线。

**架构：** SQLite 是所有记忆层的治理型事实源。Milvus 只用于 L1 语义召回；当向量检索不可用时应优雅降级。L1 和 L2 通过兼容 `RuntimeTool` 的工具或工具结果增强被 Expert 消费；L3 从按 `owner_key` 隔离的偏好中注入 Expert 上下文。

**技术栈：** FastAPI、Pydantic、SQLite、现有 `RuntimeTool` 工具循环、现有 Expert Router、通过 `app/core/milvus_client.py` 使用 Milvus、通过 `app/services/vector_embedding_service.py` 使用 embedding、pytest。

---

## 范围

本计划按四个可独立发布的阶段实现 PRD：

- 阶段 1：L1 经验记忆、反馈时间线蒸馏、手工冷启动录入、弱接受录入、`recall_experience`。
- 阶段 2：L2 服务知识、手工基线、从 monitor MCP 种子导入、`lookup_service_knowledge`、指标/日志结果增强。
- 阶段 3：L3 显式用户偏好、owner 隔离、提示词注入。
- 阶段 4：治理、安全过滤、冲突标记、命中计数去重、回归验证。

本计划不实现：自动基线学习、从对话中自动提取偏好、自动修复、旧的固定 OnCall Planner/Executor/Reporter 流水线。

## 文件结构

- 新建 `app/models/memory.py`
  - L1 反馈、经验记录、服务知识、基线、偏好、治理请求的 Pydantic 模型。

- 新建 `app/services/memory_safety.py`
  - 写入前使用的小型确定性脱敏工具。

- 新建 `app/services/experience_memory_service.py`
  - `experience_memories` 的 SQLite 权威服务。
  - 将事件时间线蒸馏成 `evidence_summary`。
  - 创建显式反馈记忆、弱接受记忆、手工冷启动记忆。
  - 当索引召回发现同根因近重复时进行合并。

- 新建 `app/services/experience_memory_index_service.py`
  - 管理 L1 Milvus collection。
  - 对症状做 embedding、upsert 记忆向量、召回相似记忆、禁用记录、重建索引。
  - Milvus 不可用时写入失败关闭，召回返回空结果。

- 新建 `app/tools/recall_experience.py`
  - 面向 Expert 的历史经验召回 `RuntimeTool` 包装。

- 新建 `app/services/service_knowledge_service.py`
  - L2 `services`、`service_relations`、`service_baselines` 的 SQLite 权威服务。
  - 支持手工基线 CRUD 和从 monitor MCP 元数据种子导入。

- 新建 `app/tools/lookup_service_knowledge.py`
  - 精确服务知识查询的 `RuntimeTool` 包装。

- 新建 `app/services/user_preference_service.py`
  - `user_preferences` 的 SQLite 权威服务，按哈希后的 `owner_key` 作为 key。

- 新建 `app/api/memory.py`
  - L1 反馈和治理接口。
  - L2 服务知识接口。
  - L3 显式偏好接口。

- 修改 `app/config.py`
  - 添加记忆 DB 路径、项目 ID、collection 名称、阈值、功能开关。

- 修改 `app/tools/__init__.py`
  - 将 `recall_experience` 注册到 diagnosis/metric/log 工具中。
  - 将 `lookup_service_knowledge` 注册到 diagnosis/change，可选注册到 knowledge。

- 修改 `app/agent/experts/base.py`
  - 给 `run()` 增加可选 `context` 参数，并加入 system/user message 栈。
  - 将 `get_tools` 改为 `get_tools(self, *, session_id: str = "")`，并让 `run()` 调用 `await self.get_tools(session_id=session_id)`，这样 Expert 可以把真实 scoped session 绑定到 session-aware 工具（如 recall）中。所有覆盖 `get_tools` 的 Expert 都要放宽签名。

- 修改 `app/services/router_service.py`
  - 接收 `owner_key`。
  - 加载 L3 偏好，并把格式化后的上下文传给 Expert。
  - 普通 Router + Expert 完成 payload 中保持 `case_id` 为空。

- 修改 `app/api/assistant.py`
  - 将 `owner_key` 传入 `router_service.stream`。

- 修改 `app/main.py`
  - 注册 `app.api.memory`。

- 修改 `app/agent/experts/metric.py` 和 `app/agent/experts/log.py`
  - 当可以识别服务名时，使用 `transform_tool_result` 追加服务基线对比。

- 添加测试：
  - `tests/test_memory_safety.py`
  - `tests/test_experience_memory_service.py`
  - `tests/test_experience_memory_index_service.py`
  - `tests/test_recall_experience_tool.py`
  - `tests/test_memory_api.py`
  - `tests/test_service_knowledge_service.py`
  - `tests/test_lookup_service_knowledge_tool.py`
  - `tests/test_user_preference_service.py`
  - 更新 `tests/test_experts.py`
  - 更新 `tests/test_router_service.py`
  - 更新 `tests/test_assistant_api.py`

---

## 任务 1：配置、模型与安全辅助函数

**文件：**
- 修改：`app/config.py`
- 新建：`app/models/memory.py`
- 新建：`app/services/memory_safety.py`
- 测试：`tests/test_memory_safety.py`

- [ ] **步骤 1：为密钥和 PII 脱敏编写测试**

创建 `tests/test_memory_safety.py`。测试应覆盖：

- 常见密钥：`token=...`、`api_key: ...`、`password=...`
- 邮箱脱敏为 `[REDACTED_EMAIL]`
- 中国大陆手机号脱敏为 `[REDACTED_PHONE]`
- 敏感原文不应出现在脱敏结果中

- [ ] **步骤 2：运行失败测试**

```powershell
python -m pytest tests/test_memory_safety.py -q
```

预期：失败，因为 `app.services.memory_safety` 尚不存在。

- [ ] **步骤 3：添加配置字段**

在 `app/config.py` 的 `Settings` 中靠近 checkpoint 和 vector 配置处添加长期记忆字段：

```python
    # Long-term memory
    memory_db_path: str = "volumes/long_term_memory.db"
    project_id: str = "super_biz_agent"
    long_term_memory_enabled: bool = True
    experience_memory_collection: str = "experience_memory"
    experience_memory_top_k: int = 3
    experience_memory_similarity_threshold: float = 0.78
    experience_memory_high_confidence: float = 0.8
    experience_memory_weak_confidence: float = 0.4
    service_knowledge_enabled: bool = True
    user_preferences_enabled: bool = True
```

> **重要：这些开关必须在运行时生效，不能只声明。** 工具通过模块级 tuple（`app/tools/__init__.py`）注册，它们在 import 时就会求值，无法响应运行时开关。因此记忆工具要通过 Expert 的 `get_tools()` 内部调用的、带 feature flag 的 helper 暴露（任务 3 步骤 5、任务 6 步骤 4），增强 hook 在开关关闭时提前返回（任务 6 步骤 5）。06-17 简化设计要求长期记忆是显式、可切换功能，不能无条件塞进静态 tuple。如需 opt-in 灰度，可以考虑默认将 `long_term_memory_enabled` 设为 `False`。

- [ ] **步骤 4：添加记忆模型**

创建 `app/models/memory.py`，包括：

- `MemoryFeedbackRequest`
- `ManualExperienceCreateRequest`
- `ExperienceMemoryUpdateRequest`
- `ServiceBaselineRequest`
- `UserPreferenceRequest`

其中 `MemoryFeedbackRequest` 要包含：

- `session_id`
- `user_message`
- `assistant_answer`
- `user_accepted`
- `acceptance_level: Literal["strong", "weak"]`
- `actual_root_cause`
- `final_resolution`
- `comment`
- `environment`
- `service_name`
- `events`

注意：前端必须把 `complete` 事件时间线重新发送给后端，后端才能蒸馏 `evidence_summary`；否则 L1 证据为空。详见任务 4b。

- [ ] **步骤 5：添加脱敏 helper**

创建 `app/services/memory_safety.py`，实现：

- 匹配 `token`、`api_key`、`secret`、`password` 等模式并替换为 `[REDACTED]`
- 邮箱替换为 `[REDACTED_EMAIL]`
- 中国大陆手机号替换为 `[REDACTED_PHONE]`
- 对空文本返回空字符串

- [ ] **步骤 6：验证**

```powershell
python -m pytest tests/test_memory_safety.py -q
```

预期：通过。

- [ ] **步骤 7：提交**

```powershell
git add app/config.py app/models/memory.py app/services/memory_safety.py tests/test_memory_safety.py
git commit -m "feat: add long-term memory config and safety models"
```

---

## 任务 2：L1 经验记忆 SQLite 存储

**文件：**
- 新建：`app/services/experience_memory_service.py`
- 测试：`tests/test_experience_memory_service.py`

- [ ] **步骤 1：为反馈蒸馏和手工录入编写失败测试**

创建 `tests/test_experience_memory_service.py`。测试应覆盖：

- `create_from_feedback` 能从事件时间线中蒸馏证据：
  - `tool_event`
  - `tool`
  - `evidence_id`
  - `summary`
- 初始置信度使用高置信度配置。
- 手工录入会脱敏 `root_cause` 和 `resolution`。
- 同症状且同根因的高相似记录会合并，而不是新建。
- 同症状但不同根因的记录必须分开保存。

- [ ] **步骤 2：运行失败测试**

```powershell
python -m pytest tests/test_experience_memory_service.py -q
```

预期：失败，因为 `ExperienceMemoryService` 不存在。

- [ ] **步骤 3：实现服务**

创建 `app/services/experience_memory_service.py`，公开接口：

```python
class ExperienceMemoryService:
    def __init__(self, db_path: str | Path | None = None, index_service: Any | None = None): ...
    def create_from_feedback(...): ...
    def create_weak_acceptance(...): ...
    def create_manual(...): ...
    def recall(self, *, query: str, project_id: str, top_k: int = 3, session_id: str = "") -> list[dict[str, Any]]: ...
    def get(self, experience_id: str) -> dict[str, Any] | None: ...
    def list(self, *, project_id: str, enabled: bool | None = None, limit: int = 50) -> list[dict[str, Any]]: ...
    def update(self, experience_id: str, *, enabled: bool | None = None, confidence: float | None = None) -> bool: ...
    def rebuild_index(self, *, project_id: str | None = None) -> int: ...
```

SQLite 表结构：

```sql
CREATE TABLE IF NOT EXISTS experience_memories (
    experience_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT '',
    service_name TEXT NOT NULL DEFAULT '',
    symptoms TEXT NOT NULL,
    root_cause TEXT NOT NULL,
    resolution TEXT NOT NULL,
    evidence_summary TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_session_id TEXT NOT NULL DEFAULT '',
    source_feedback_id TEXT NOT NULL DEFAULT '',
    source_event_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    milvus_pk TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

实现要求：

- `create_from_feedback` 在 `user_accepted` 为 false 时返回 `""` 且不写入。
- 显式反馈初始置信度使用 `config.experience_memory_high_confidence`。
- 弱接受初始置信度使用 `config.experience_memory_weak_confidence`。
- 证据摘要由 `tool_event` 构建，格式为 `tool evidence_id: summary`。
- `root_cause`、`resolution`、`evidence_summary` 都要经过 `redact_memory_text`。
- 索引写入失败只记录日志，不影响 SQLite 写入。
- 去重/合并：插入前调用 `index.recall(...)` 检索新症状。如果 top hit 的 `similarity >= config.experience_memory_similarity_threshold`，且已存根因与新根因足够接近（v1 使用大小写/空格不敏感相等即可），则合并而不是插入新记录：追加 `source_session_id` / `source_feedback_id`，合并 `evidence_summary`，更新 `updated_at`，保持或提高 `confidence`，重新 upsert 索引，并返回原有 `experience_id`。
- 如果症状相似但根因不同，必须插入独立记录。
- `recall` 对每条返回记忆递增 `hit_count`，并按 `session_id` 在单次调用内去重；同一 session 多次召回不应重复计数。
- 当召回集合中存在相似症状但不同 `root_cause` 的多条记录时，为返回 dict 添加 `conflict_count`，供召回工具提示冲突。
- 返回排序：先按 `confidence` 降序，再按 `similarity` 降序。

- [ ] **步骤 4：验证**

```powershell
python -m pytest tests/test_experience_memory_service.py tests/test_memory_safety.py -q
```

预期：通过。

- [ ] **步骤 5：提交**

```powershell
git add app/services/experience_memory_service.py tests/test_experience_memory_service.py
git commit -m "feat: add l1 experience memory store"
```

---

## 任务 3：L1 Milvus 索引与召回工具

**文件：**
- 新建：`app/services/experience_memory_index_service.py`
- 新建：`app/tools/recall_experience.py`
- 修改：`app/tools/__init__.py`
- 测试：`tests/test_experience_memory_index_service.py`
- 测试：`tests/test_recall_experience_tool.py`
- 更新：`tests/test_experts.py`

- [ ] **步骤 1：编写失败的工具测试**

创建 `tests/test_recall_experience_tool.py`。测试应覆盖：

- `_recall_experience("checkout latency", session_id="s1")` 能格式化历史经验。
- 输出包含“历史经验仅供参考，必须先验证”的提示。
- 输出包含 `experience_id`、根因、解决方案、证据摘要。
- 当 `conflict_count` 存在时输出冲突提醒。
- `experience_tools()` 必须遵守 `long_term_memory_enabled` 运行时开关。

- [ ] **步骤 2：运行失败测试**

```powershell
python -m pytest tests/test_recall_experience_tool.py -q
```

预期：失败，因为工具尚不存在。

- [ ] **步骤 3：实现索引适配器**

创建 `app/services/experience_memory_index_service.py`，包含：

```python
class ExperienceMemoryIndexService:
    def recall(self, *, query: str, project_id: str, top_k: int) -> list[dict[str, Any]]: ...
    def upsert(self, memory: dict[str, Any]) -> str: ...
    def disable(self, experience_id: str) -> None: ...
    def rebuild(self, memories: list[dict[str, Any]]) -> int: ...
```

要求：

- 使用现有 `vector_embedding_service.embed_query` 对症状做 embedding。
- 使用 `config.experience_memory_collection`。
- 捕获 Milvus 异常，`recall` 返回 `[]`。
- 写入类异常应按“失败关闭”处理，不产生误召回。

- [ ] **步骤 4：实现召回工具**

创建 `app/tools/recall_experience.py`：

- 定义 `RecallExperienceArgs`，只暴露 `query`。
- 不允许 LLM 传入 `session_id`；真实 scoped session 由 per-run factory 通过闭包绑定。
- `_recall_experience(query, session_id="")` 调用 `experience_memory_service.recall(...)`。
- 没有命中时返回“未命中可复用的历史诊断经验。”
- 有命中时输出：
  - 历史经验仅供参考、必须先用当前证据验证。
  - `experience_id`
  - `confidence`
  - `similarity`
  - `symptoms`
  - `verified_root_cause`
  - `effective_resolution`
  - `evidence_summary`
  - 冲突提醒（如有）
- 提供 `build_recall_experience_tool(session_id: str = "") -> RuntimeTool`，把真实 session id 绑定到闭包中。

- [ ] **步骤 5：用运行时开关注册工具，并绑定 session**

修改 `app/tools/__init__.py`，添加：

```python
from app.config import config
from app.tools.recall_experience import build_recall_experience_tool


def experience_tools(session_id: str = "") -> tuple:
    """L1 recall tool bound to the real scoped session, gated by the feature flag."""
    if not config.long_term_memory_enabled:
        return ()
    return (build_recall_experience_tool(session_id),)
```

并将 `experience_tools` 加入 `__all__`。

同时要把真实 `session_id` 从 `run()` 穿透到 `get_tools()`：

- `app/agent/experts/base.py`
  - `async def get_tools(self, *, session_id: str = "") -> list[RuntimeTool]:`
  - `run()` 内部加载工具时调用 `await self.get_tools(session_id=session_id)`。

- `diagnosis.py`
  - `async def get_tools(self, *, session_id: str = ""):`
  - 返回工具时拼入 `*experience_tools(session_id)`。

- `metric.py`
  - 同样拼入 `*experience_tools(session_id)`，MCP server 为 `"monitor"`。

- `log.py`
  - 同样拼入 `*experience_tools(session_id)`，MCP server 为 `"cls"`。

- `knowledge.py` / `change.py`
  - 即使不用 `session_id`，也要放宽签名以匹配 base。

`ExpertAgent` Protocol 只声明 `run`，因此无需为 `get_tools` 修改协议。`run()` 收到的 `session_id` 已经是来自 `assistant.py` 的 scoped key：`owner:{owner_key}:{session_id}`，正适合 hit-count 去重。

- [ ] **步骤 6：验证**

```powershell
python -m pytest tests/test_recall_experience_tool.py tests/test_experts.py -q
```

预期：通过。现有 `test_diagnosis_expert_has_broad_local_tool_set` 仍应只断言未变的静态 tuple，因此不要把它改成期待 `recall_experience`。

- [ ] **步骤 7：提交**

```powershell
git add app/services/experience_memory_index_service.py app/tools/recall_experience.py app/tools/__init__.py tests/test_recall_experience_tool.py tests/test_experience_memory_index_service.py tests/test_experts.py
git commit -m "feat: add experience recall tool"
```

---

## 任务 4：L1 反馈与治理 Memory API

**文件：**
- 新建：`app/api/memory.py`
- 修改：`app/main.py`
- 测试：`tests/test_memory_api.py`

- [ ] **步骤 1：编写失败的 API 测试**

创建 `tests/test_memory_api.py`。测试应覆盖：

- `POST /api/memory/feedback` 能调用 `experience_memory_service.create_from_feedback`。
- `POST /api/memory/experiences` 能调用 `create_manual`。
- 返回结构为 `{"code": 200, "message": "success", "data": {...}}`。
- 反馈接口需要 `X-Session-Owner`。
- 治理/手工接口在本地开发中不强制用户 session ownership。

- [ ] **步骤 2：运行失败测试**

```powershell
python -m pytest tests/test_memory_api.py -q
```

预期：失败，因为 `app.api.memory` 缺失或尚未注册。

- [ ] **步骤 3：实现 API**

创建 `app/api/memory.py`，路由：

```text
POST /api/memory/feedback
POST /api/memory/experiences
GET /api/memory/experiences
GET /api/memory/experiences/{experience_id}
PATCH /api/memory/experiences/{experience_id}
POST /api/memory/experiences/rebuild-index
```

使用：

- `MemoryFeedbackRequest`
- `ManualExperienceCreateRequest`
- `ExperienceMemoryUpdateRequest`

- [ ] **步骤 4：注册路由**

修改 `app/main.py`：

```python
from app.api import assistant, chat, file, health, memory
app.include_router(memory.router, prefix="/api", tags=["long-term-memory"])
```

- [ ] **步骤 5：验证**

```powershell
python -m pytest tests/test_memory_api.py -q
```

预期：通过。

- [ ] **步骤 6：提交**

```powershell
git add app/api/memory.py app/main.py tests/test_memory_api.py
git commit -m "feat: add memory feedback and governance api"
```

---

## 任务 4b：反馈采集串联（证据时间线 + 弱接受）

> **为什么需要这个任务：** 如果没有它，任务 2 的 `create_weak_acceptance` 会变成死代码，`evidence_summary` 也会一直为空，违背 PRD v2 的证据链和反饥饿要求。反馈接口必须根据接受等级分派，前端必须重新发送时间线。

**文件：**
- 修改：`app/api/memory.py`
- 修改：`frontend/src/components/ChatWorkspace.tsx`
- 修改：`frontend/src/api/agentStream.ts`
- 测试：`tests/test_memory_api.py`

- [ ] **步骤 1：后端分派测试（strong vs weak）**

向 `tests/test_memory_api.py` 追加测试：

- `acceptance_level="strong"` 调用 `create_from_feedback`。
- `acceptance_level="weak"` 调用 `create_weak_acceptance`。
- 两者都要求 `user_accepted=True`。

- [ ] **步骤 2：在 `/api/memory/feedback` 中串联分派**

反馈 handler 逻辑：

```python
if not body.user_accepted:
    experience_id = ""
elif body.acceptance_level == "weak":
    experience_id = experience_memory_service.create_weak_acceptance(...)
else:
    experience_id = experience_memory_service.create_from_feedback(...)
```

两条写入路径都必须传入 `events=body.events`，服务会从重新发送的时间线中蒸馏 `evidence_summary`。

- [ ] **步骤 3：前端保留并重新发送 `complete` 事件时间线**

Router 的 `complete` 事件已经携带 `events`。在 `frontend/src/api/agentStream.ts` / `ChatWorkspace.tsx` 中：

- 在 message state 中保留 `complete` 事件的 `events` 数组。
- 在完成的诊断答案上添加“采纳 / 纠正根因”入口。
- POST `/api/memory/feedback`，payload 包含：
  - `user_message`
  - `assistant_answer`
  - `user_accepted: true`
  - `acceptance_level: "strong"`
  - 可选 `actual_root_cause`
  - 可选 `final_resolution`
  - `events`
  - header `X-Session-Owner`

- [ ] **步骤 4：弱接受触发（显式客户端信号，不做服务端推断）**

完全自动的“用户没有继续追问，因此弱接受”推断暂缓，需要可靠的会话结束/无纠正检测。本版本弱信号是显式但低摩擦的客户端动作：当用户关闭答案、或开始新主题且没有纠正时，前端发送一次 `/api/memory/feedback`，其中：

- `acceptance_level: "weak"`
- `user_accepted: true`
- 携带已保留的 `events`

必须清楚记录这一点，不能静默推断。

- [ ] **步骤 5：验证**

```powershell
python -m pytest tests/test_memory_api.py -q
```

预期：通过。前端改动在任务 10 中通过 assistant UI 手工验证。

- [ ] **步骤 6：提交**

```powershell
git add app/api/memory.py frontend/src/components/ChatWorkspace.tsx frontend/src/api/agentStream.ts tests/test_memory_api.py
git commit -m "feat: wire feedback evidence timeline and weak acceptance"
```

---

## 任务 5：L2 服务知识存储

**文件：**
- 新建：`app/services/service_knowledge_service.py`
- 测试：`tests/test_service_knowledge_service.py`

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_service_knowledge_service.py`。测试应覆盖：

- upsert service。
- upsert baseline。
- lookup 能返回 service owner、description、baselines。
- project 隔离：`project_id="p2"` 不应读到 `project_id="p1"` 的记录。

- [ ] **步骤 2：运行失败测试**

```powershell
python -m pytest tests/test_service_knowledge_service.py -q
```

预期：失败，因为服务不存在。

- [ ] **步骤 3：实现服务**

创建 `app/services/service_knowledge_service.py`，包含三张表：

```sql
CREATE TABLE IF NOT EXISTS services (
    project_id TEXT NOT NULL,
    service_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    owner_team TEXT NOT NULL DEFAULT '',
    owner_user TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, service_name, environment)
);

CREATE TABLE IF NOT EXISTS service_relations (
    project_id TEXT NOT NULL,
    source_service TEXT NOT NULL,
    target_service TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    environment TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_baselines (
    project_id TEXT NOT NULL,
    service_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    min_value REAL NOT NULL,
    max_value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    sample_window TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, service_name, environment, metric_name)
);
```

公开接口：

```python
upsert_service(...)
upsert_relation(...)
upsert_baseline(...)
lookup(project_id, service_name, environment="")
compare_metric(project_id, service_name, environment, metric_name, value)
```

- [ ] **步骤 4：验证**

```powershell
python -m pytest tests/test_service_knowledge_service.py -q
```

预期：通过。

- [ ] **步骤 5：提交**

```powershell
git add app/services/service_knowledge_service.py tests/test_service_knowledge_service.py
git commit -m "feat: add service knowledge store"
```

---

## 任务 6：L2 查询工具与指标/日志增强

**文件：**
- 新建：`app/tools/lookup_service_knowledge.py`
- 修改：`app/tools/__init__.py`
- 修改：`app/agent/experts/metric.py`
- 修改：`app/agent/experts/log.py`
- 测试：`tests/test_lookup_service_knowledge_tool.py`
- 更新：`tests/test_experts.py`

- [ ] **步骤 1：编写失败的工具测试**

创建 `tests/test_lookup_service_knowledge_tool.py`。测试应覆盖：

- `_lookup_service_knowledge("checkout-api", "prod")` 输出服务名。
- 输出 owner team。
- 输出 baseline。
- 输出 relations。

- [ ] **步骤 2：运行失败测试**

```powershell
python -m pytest tests/test_lookup_service_knowledge_tool.py -q
```

预期：失败，因为工具不存在。

- [ ] **步骤 3：实现查询工具**

创建 `app/tools/lookup_service_knowledge.py`，定义名为 `lookup_service_knowledge` 的 `RuntimeTool`。返回紧凑文本块，包含：

- owner
- relations
- baselines

无服务时返回“未找到服务知识”。

- [ ] **步骤 4：用运行时开关注册查询工具**

修改 `app/tools/__init__.py`，添加与 `experience_tools` 相同模式的 helper：

```python
from app.tools.lookup_service_knowledge import lookup_service_knowledge


def service_knowledge_tools() -> tuple:
    return (lookup_service_knowledge,) if config.service_knowledge_enabled else ()
```

接入方式：

- `diagnosis.py` 的 `get_tools()` 中拼入 `*service_knowledge_tools()`。
- 可选将其加入 change expert。
- 不要加入 metric/log；它们通过增强 hook 处理，避免 LLM 被迫选择更多工具。

- [ ] **步骤 5：添加增强 hook，且不能破坏现有日志流水线**

注意：`log.py` 已经覆盖了 `transform_tool_result`，用于大日志聚类/摘要流水线（`analyze_logs`）。`metric.py` 当前没有覆盖。

先添加一个共享 helper，例如放在 `app/tools/__init__.py` 或小文件 `service_enrichment.py` 中：

- `_extract_service_name(text: str) -> str`
  - 从 `service=`、`service_name=`、`服务=` 等 marker 中尝试提取服务名。
- `append_service_baseline(content: str) -> str`
  - 开关关闭时 no-op。
  - 找不到服务名时 no-op。
  - 查不到服务知识时 no-op。
  - 找到时追加服务/owner/baseline 文本块。

接入要求：

- `metric.py`：新增 `transform_tool_result` 覆盖，返回 `append_service_baseline(content)`；跳过 `get_current_time`。
- `log.py`：不要替换已有覆盖逻辑。先运行现有日志处理，再对结果调用 `append_service_baseline(...)`。绝不能丢掉大日志 pipeline。

注意：基于 marker 的服务名提取很脆弱，真实 Prometheus/日志摘要不一定包含 `service=`。因此增强必须始终 best-effort，失败时返回原内容。

- [ ] **步骤 6：验证**

```powershell
python -m pytest tests/test_lookup_service_knowledge_tool.py tests/test_experts.py -q
```

预期：通过。

- [ ] **步骤 7：提交**

```powershell
git add app/tools/lookup_service_knowledge.py app/tools/__init__.py app/agent/experts/metric.py app/agent/experts/log.py tests/test_lookup_service_knowledge_tool.py tests/test_experts.py
git commit -m "feat: add service knowledge lookup and enrichment"
```

---

## 任务 7：L2 Memory API 与种子导入

**文件：**
- 修改：`app/api/memory.py`
- 修改：`app/services/service_knowledge_service.py`
- 测试：`tests/test_memory_api.py`

- [ ] **步骤 1：添加 API 测试**

向 `tests/test_memory_api.py` 追加服务基线 API 测试，覆盖：

- `PUT /api/memory/services/checkout-api/baselines`
- 请求体包含 `service_name`、`environment`、`metric_name`、`min_value`、`max_value`、`unit`
- 服务层收到 `project_id="super_biz_agent"`

- [ ] **步骤 2：运行失败测试**

```powershell
python -m pytest tests/test_memory_api.py -q
```

预期：失败，因为 L2 路由缺失。

- [ ] **步骤 3：添加 L2 路由**

在 `app/api/memory.py` 增加：

```text
GET /api/memory/services/{service_name}
PUT /api/memory/services/{service_name}/baselines
POST /api/memory/services/import-seed
```

`import-seed` 调用 `ServiceKnowledgeService.import_from_monitor_mcp()` 并返回 `{"imported": count}`。

- [ ] **步骤 4：实现种子导入**

在 `ServiceKnowledgeService` 中实现：

```python
async def import_from_monitor_mcp(self, *, project_id: str) -> int:
    from app.agent.mcp_client import get_mcp_client_with_retry

    client = await get_mcp_client_with_retry()
    services = await client.call_tool("monitor", "list_all_services", {})
    count = 0
    for item in services if isinstance(services, list) else []:
        self.upsert_service(
            project_id=project_id,
            service_name=str(item.get("service_name") or item.get("name") or ""),
            environment=str(item.get("environment") or "prod"),
            owner_team=str(item.get("owner_team") or item.get("team") or ""),
            description=str(item.get("description") or ""),
        )
        count += 1
    return count
```

注意真实签名是 `call_tool(server_name, tool_name, arguments)`，见 `app/agent/mcp_client.py`。

- [ ] **步骤 5：验证**

```powershell
python -m pytest tests/test_memory_api.py tests/test_service_knowledge_service.py -q
```

预期：通过。

- [ ] **步骤 6：提交**

```powershell
git add app/api/memory.py app/services/service_knowledge_service.py tests/test_memory_api.py
git commit -m "feat: add service knowledge api"
```

---

## 任务 8：L3 用户偏好存储与提示词注入

**文件：**
- 新建：`app/services/user_preference_service.py`
- 修改：`app/agent/experts/base.py`
- 修改：`app/services/router_service.py`
- 修改：`app/api/assistant.py`
- 测试：`tests/test_user_preference_service.py`
- 更新：`tests/test_router_service.py`
- 更新：`tests/test_assistant_api.py`

- [ ] **步骤 1：编写偏好服务测试**

创建 `tests/test_user_preference_service.py`。测试应覆盖：

- owner 隔离：`owner-a` 的偏好不能被 `owner-b` 读到。
- 保存并读取：
  - `default_environment`
  - `language`
  - `detail_level`
  - `focused_services`
  - `notes`
- `format_for_prompt(owner_key)` 输出“用户偏好”和偏好内容。

- [ ] **步骤 2：运行失败测试**

```powershell
python -m pytest tests/test_user_preference_service.py -q
```

预期：失败，因为服务不存在。

- [ ] **步骤 3：实现偏好服务**

创建 `app/services/user_preference_service.py`，表结构：

```sql
CREATE TABLE IF NOT EXISTS user_preferences (
    owner_key TEXT PRIMARY KEY,
    default_environment TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'zh-CN',
    detail_level TEXT NOT NULL DEFAULT 'normal',
    focused_services_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
```

公开接口：

```python
upsert(owner_key, default_environment="", language="zh-CN", detail_level="normal", focused_services=None, notes="")
get(owner_key)
format_for_prompt(owner_key)
```

- [ ] **步骤 4：添加 Expert context 支持**

修改 `ToolCallingExpert.run`：

```python
def run(self, *, message: str, session_id: str, trace_id: str, context: str = ""):
```

构造消息时将 context 加入 system prompt：

```python
system_content = self.system_prompt
if context:
    system_content = f"{system_content}\n\n{context}"
```

同时更新 `ExpertAgent` protocol 的签名。

- [ ] **步骤 5：从 Router 注入偏好**

修改 `RouterService.stream`：

```python
async def stream(self, message: str, session_id: str, owner_key: str = ""):
```

在 `_iter_expert` 之前加载：

```python
preference_context = user_preference_service.format_for_prompt(owner_key) if owner_key else ""
```

调用 expert 时传入：

```python
expert.run(message=message, session_id=session_id, trace_id=session_id, context=preference_context)
```

同时修改 `RouterService.answer` 接收 `owner_key: str = ""` 并向下传递。

- [ ] **步骤 6：从 API 传入 owner key**

修改 `app/api/assistant.py`：

```python
async for event in router_service.stream(
    request.question, session_id=scoped_session_id, owner_key=owner_key
):
```

- [ ] **步骤 7：验证**

```powershell
python -m pytest tests/test_user_preference_service.py tests/test_router_service.py tests/test_assistant_api.py -q
```

预期：通过。

- [ ] **步骤 8：提交**

```powershell
git add app/services/user_preference_service.py app/agent/experts/base.py app/services/router_service.py app/api/assistant.py tests/test_user_preference_service.py tests/test_router_service.py tests/test_assistant_api.py
git commit -m "feat: inject user preferences into experts"
```

---

## 任务 9：L3 偏好 API

**文件：**
- 修改：`app/api/memory.py`
- 测试：`tests/test_memory_api.py`

- [ ] **步骤 1：添加 API 测试**

向 `tests/test_memory_api.py` 追加测试，覆盖：

- `PUT /api/memory/preferences`
- 必须使用 `X-Session-Owner`
- `user_preference_service.upsert(...)` 收到非空 `owner_key`
- 能保存 `default_environment` 和 `language`

- [ ] **步骤 2：运行失败测试**

```powershell
python -m pytest tests/test_memory_api.py -q
```

预期：失败，因为偏好路由不存在。

- [ ] **步骤 3：添加偏好路由**

在 `app/api/memory.py` 添加：

```text
GET /api/memory/preferences
PUT /api/memory/preferences
```

两个路由都必须通过 `require_session_owner` 要求 `X-Session-Owner`。

- [ ] **步骤 4：验证**

```powershell
python -m pytest tests/test_memory_api.py tests/test_user_preference_service.py -q
```

预期：通过。

- [ ] **步骤 5：提交**

```powershell
git add app/api/memory.py tests/test_memory_api.py
git commit -m "feat: add explicit user preference api"
```

---

## 任务 10：治理语义与回归验证

**文件：**
- 根据前面任务失败情况按需修改。
- 测试：目标测试集加相关回归测试。

- [ ] **步骤 1：验证 L1**

```powershell
python -m pytest tests/test_memory_safety.py tests/test_experience_memory_service.py tests/test_experience_memory_index_service.py tests/test_recall_experience_tool.py tests/test_memory_api.py -q
```

预期：通过。

- [ ] **步骤 2：验证 L2**

```powershell
python -m pytest tests/test_service_knowledge_service.py tests/test_lookup_service_knowledge_tool.py tests/test_experts.py -q
```

预期：通过。

- [ ] **步骤 3：验证 L3 与 router 集成**

```powershell
python -m pytest tests/test_user_preference_service.py tests/test_router_service.py tests/test_assistant_api.py -q
```

预期：通过。

- [ ] **步骤 4：验证现有核心路由**

```powershell
python -m pytest tests/test_backend_agent_gateway_stream.py tests/test_backend_agent_router.py tests/test_vector_search_and_knowledge_tool.py tests/test_tool_calling.py -q
```

预期：通过。

- [ ] **步骤 5：运行广泛测试集**

```powershell
python -m pytest -q
```

预期：通过，除非有依赖外部 Milvus/MCP 服务但本地不可用的测试。如果因为本地服务可用性失败，交付前记录准确的测试名和错误信息。

- [ ] **步骤 6：审查 diff**

```powershell
git diff -- app/config.py app/models/memory.py app/services/memory_safety.py app/services/experience_memory_service.py app/services/experience_memory_index_service.py app/services/service_knowledge_service.py app/services/user_preference_service.py app/tools/recall_experience.py app/tools/lookup_service_knowledge.py app/tools/__init__.py app/agent/experts/base.py app/agent/experts/metric.py app/agent/experts/log.py app/services/router_service.py app/api/assistant.py app/api/memory.py app/main.py
```

预期：diff 只包含长期记忆实现和必要集成改动。

- [ ] **步骤 7：提交最终验证修复**

如果步骤 1 到步骤 6 中做了修复，提交：

```powershell
git add app tests
git commit -m "test: verify long-term memory integration"
```

如果验证期间没有文件变化，不要创建空提交。

---

## 自审笔记

规格覆盖：

- L1 经验卡片、合并/去重、召回工具、project 隔离、Milvus 优雅降级：任务 2-4。证据时间线蒸馏与 weak/strong 分派在任务 4b 中完成，不只是声明。
- L2 服务列表、拓扑/基线存储、手工基线维护、种子导入、查询工具、指标/日志结果增强：任务 5-7。`log.py` 的增强必须扩展现有大日志 pipeline，不能替换；`metric.py` 新增 override。
- L3 显式设置、owner 隔离、Expert 提示词注入：任务 8-9。
- 治理 API、禁用/更新、重建索引、冲突标签、命中计数、脱敏：任务 1、4、10。
- `long_term_memory_enabled` / `service_knowledge_enabled` 必须在运行时通过 `experience_tools()` / `service_knowledge_tools()` 生效，且这些 helper 要在每个 Expert 的 `get_tools()` 内调用，不能烘焙进静态 `*_LOCAL_TOOLS` tuple。这样才能满足 06-17 简化设计中“长期记忆必须是显式可切换功能”的要求。

已知排除 / 限制：

- 自动基线学习故意排除。
- 基于对话的偏好抽取故意排除。
- 旧 OnCall Planner 记忆注入故意排除。
- 复杂同症状冲突图/版本管理排除；召回仅做冲突标记。
- 弱接受是显式客户端信号，不是服务端根据“用户没有追问”推断。自动推断暂缓。

已解决设计点：

- 召回现在能看到真实 scoped `session_id`。`RuntimeTool` handler 只接收 LLM 提供的参数，因此不能把 `session_id` 暴露给模型；改为每次运行通过 `build_recall_experience_tool(session_id)` 构建工具，并经 `get_tools(session_id=...)` 到 `run(session_id=...)` 传递。hit-count 去重使用真实 `owner:{owner_key}:{session_id}` key。

执行备注：

- 当前 worktree 已包含很多 Router + Expert 简化相关的无关改动。每个任务只应 stage 该任务列出的文件。
- 完整 `pytest -q` 可能因为本 worktree 已删除旧 legacy aiops 模块而出现预存失败；记录这些失败，但不要归因于本计划。

