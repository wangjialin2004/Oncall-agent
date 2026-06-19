# PRD：智能 OnCall Agent 长期记忆系统

> 状态：评审修订 v2 · 日期：2026-06-17 · 适用架构：Router + 扁平 Expert（无 Planner 流水线）

> **v2 修订要点**（针对评审意见）：
> 1. 补全 **L1 证据链来源**——反馈请求回传 events timeline，后端即时蒸馏为 evidence_summary，解决"中段记忆已删、采纳时无证据"的断层。
> 2. 新增 **冷启动与被动信号**——手工录入经验卡 + 弱采纳低置信入库，避免记忆库长期饿死。
> 3. **L2 自动基线学习拆分**——首版只做"人工维护基线 + 结构化拓扑/归属人"，自动基线学习移为独立后续课题。
> 4. **L2 消费方式调整**——服务知识优先以"工具结果增强"附带，而非新增独立工具，降低 Expert 工具选择负担。
> 5. **L3 首版降级**——只做显式设置 API，对话自动抽取偏好移到后续阶段。

---

## 一、背景与问题（为什么现在做）

本项目是企业级 OnCall/AIOps 智能助手，当前架构为 **Router + 5 个扁平 Expert**（knowledge / metric / log / change / diagnosis），每个 Expert 是 [app/agent/experts/base.py](../../../app/agent/experts/base.py) 中的 `ToolCallingExpert`，通过工具调用循环作答。除 RAG 走 SQLite checkpoint（[app/services/checkpoint_service.py](../../../app/services/checkpoint_service.py)）外，其余 Expert 基本**无状态**。

历史上项目曾实现过完整的长期记忆（2026-06-14 设计，见 [2026-06-14-long-term-experience-memory-design.md](2026-06-14-long-term-experience-memory-design.md)），代码仍存于 git HEAD（`experience_memory_service.py`、`experience_memory_index_service.py`、`diagnosis_memory_service.py`、`app/models/memory.py`、`app/api/memory.py`）。但 **2026-06-17 的路由简化**（[2026-06-17-router-experts-simplification-design.md](2026-06-17-router-experts-simplification-design.md)）将其从主链路移除，并明确写道："*若后续产品方向再次需要长期记忆，应作为一次明确的功能决策重新引入*"。

**本 PRD 即是这次"明确的功能决策"**。核心差异：旧记忆由 **Planner** 消费，而新架构已无 Planner——因此长期记忆必须重新设计为**面向扁平 Expert 的工具化召回**，而非注入 Planner 提示词。

**预期结果**：Agent 能跨会话、跨事件复用已验证的诊断经验，理解服务拓扑与正常基线，并记住每个用户的运维偏好，从而给出更快、更准、更贴合上下文的诊断。

---

## 二、目标与非目标

### 目标
- 定义**长期记忆应该装什么、不装什么**，并给出专业准入原则。
- 在当前扁平 Expert 架构下，设计记忆的**写入（学习）路径**与**读取（召回）路径**。
- 复用现有 Milvus / 向量检索 / SQLite 基础设施，最小化新增组件。
- 提供分阶段落地路线与验证方式。

### 非目标
- 不引入自主修复 / 自动改动机器的动作（只读诊断）。
- 不重做前端视觉外壳。
- 不在本期实现"同症状不同根因"的复杂冲突消解与版本图谱（仅做隔离存放）。
- 不替换短期 checkpoint 或对话历史机制。

---

## 三、核心决策：长期记忆装什么（PRD 的核心）

采用业界 Agent 记忆三分法，落到本系统的具体内容（采用**完整三类体系**）：

| 记忆层 | 类型 | 装什么 | 归属/隔离 | 价值 |
|---|---|---|---|---|
| **L1 经验记忆** | episodic→semantic | 诊断经验卡：症状 → 已验证根因 → 有效处置 → 证据摘要 | **项目共享**（`project_id`） | 让相似事件"一眼复用历史结论" |
| **L2 实体/语义知识** | semantic | 服务清单、依赖拓扑、归属人、**正常基线**（CPU/内存/QPS/P95）、已知误报签名、环境标签 | **项目共享**（`project_id`） | 让诊断知道"什么是正常"，区分噪声与真异常 |
| **L3 用户/团队偏好** | procedural | 关注的服务/环境、默认环境（prod/staging）、回答风格/详略、语言、"先看 X"习惯 | **用户私有**（`owner_key`） | 让回答贴合每个用户的运维习惯 |

> L2 是相对旧设计的**新增重点**——纯经验卡无法回答"这个 CPU 80% 算不算异常"，必须有基线/拓扑这类实体知识支撑。

### 不应进入长期记忆（同样关键的边界）

| 不入库内容 | 原因 | 正确归属 |
|---|---|---|
| 原始日志 / 原始指标全量 payload | 体量大、时效性强、含敏感信息 | 流式 timeline / MCP 实时查询 |
| 完整对话逐字记录 | 属于短期上下文 | checkpoint（[app/services/checkpoint_service.py](../../../app/services/checkpoint_service.py)） |
| **未验证的 AI 结论** | 会污染记忆、形成错误复用 | 仅作本次回答，不落库 |
| 密钥 / token / 凭证 / PII | 安全合规 | 永不入库，写入前做红线过滤 |
| 一次性瞬时值（当前告警值、时间戳） | 不可复用 | 不入库 |

---

## 四、准入原则（写入前必须同时满足）

任何内容进入长期记忆前，必须通过这 5 道闸：

1. **已验证（Verified）**：要么经用户反馈确认（L1），要么是确定性事实（L2 来自 MCP 服务清单/确定性基线），要么是用户显式声明（L3）。绝不入库推测性结论。
2. **可复用（Reusable）**：跨会话/跨事件普遍有效，而非一次性。
3. **已蒸馏（Distilled）**：存摘要与 `evidence_id` 引用，不存原始大 payload。
4. **可治理（Governable）**：可启用/禁用、可审计、可编辑、带置信度与衰减。
5. **已隔离且安全（Isolated & Safe）**：按 `project_id`/`owner_key` 隔离；写入前过滤密钥与 PII。

---

## 五、三类记忆详细设计

### L1 经验记忆（诊断经验卡）— 复用并改造旧设计

- **Schema**：复用 [2026-06-14-long-term-experience-memory-design.md](2026-06-14-long-term-experience-memory-design.md) 的 `experience_memories` 表与 `experience_memory` Milvus collection（git HEAD 中 `experience_memory_service.py` / `experience_memory_index_service.py` 是可直接复活的参考实现）。字段：`experience_id, project_id, environment, service_name, symptoms, root_cause, resolution, evidence_summary, source_*_json, confidence, hit_count, success_count, enabled, milvus_pk, created_at, updated_at`。
- **证据链来源（v2 关键补充，阻塞项 #1）**：06-17 简化后主链路**不再落 case/evidence 表**，响应 `case_id` 恒为空——因此用户点"采纳"时后端手里没有证据链。本期**不重建完整 case 表**，改为：`/api/memory/feedback` 请求体**回传本次回答的 events timeline**（前端本就持有，含各 `tool_event` 的 `evidence_id` 与 summary），后端**即时蒸馏**为 `evidence_summary` 写入经验卡。这是 L1 能否产出有价值经验卡的前提，必须在 Phase 1 内实现。
- **写入触发（多路，v2 补充阻塞项 #2）**：单靠"用户主动采纳"在真实 OnCall 场景采纳率极低，会导致经验库长期饿死。本期提供三条写入路：
  1. **显式高置信反馈**：`/api/memory/feedback` 且 `user_accepted=true`（用户确认/纠正根因），初始置信 `0.8`。
  2. **被动弱采纳**：同一 session 内用户对诊断回答未追问、未纠正即视为弱认可，以**低置信度（如 0.4）**入库，靠后续命中提权（`hit_count`/`success_count`）。
  3. **手工冷启动录入**：运维通过治理 API 直接录入经验卡，解决上线初期空库问题。
- **生成方式**：规则生成为主（symptoms = 用户输入 + 回答要点 + timeline 证据摘要；root_cause 优先取用户纠正值），LLM 增强为辅；LLM 失败不阻断。
- **去重合并**：写入前按 symptoms 向量检索 top-3，相似度 > `0.78` 且根因接近则合并（追加来源、更新证据、保/升置信度）；根因不同则另存。
- **读取（关键改造）**：以**工具**形式暴露 `recall_experience(query)`，挂入 diagnosis / metric / log Expert 的工具集（参照 [app/tools/knowledge_tool.py](../../../app/tools/knowledge_tool.py) 的 `retrieve_knowledge` 模式与 [app/tools/__init__.py](../../../app/tools/__init__.py) 的分 Expert 工具集）。召回结果须标注"历史经验仅供参考、需先验证再采信"。

### L2 实体/服务知识 — 新增

- **存储**：SQLite 结构化为主（`services` 实体表 + `service_relations` 依赖表 + `service_baselines` 基线表），服务名/描述的模糊匹配可选挂 Milvus。SQLite 为事实源。
- **核心字段**：服务名、环境、归属人/团队、依赖关系（上下游）、正常基线（CPU/内存/QPS/延迟 P95 的区间与采样窗口）、已知误报告警签名。
- **写入路径（v2 收敛为确定性两路，阻塞项 #3）**：
  - **种子导入**：从 monitor MCP 的 `list_all_services` / `get_service_info` 拉取服务清单、拓扑、归属人等元数据（见 [mcp_servers/](../../../mcp_servers/)）——确定性强、立刻可用。
  - **人工维护基线**：首版基线由运维**手工维护**（每服务 CPU/内存/QPS/P95 的正常区间），通过治理 API 增删改。
  - ⚠️ **自动基线学习移为独立后续课题**：基线漂移、日/周周期、冷启动无数据是 AIOps 难点，且当前 monitor/cls MCP 仍是**模拟数据**，统计沉淀学不出真值。不让它阻塞 L2 中确定性强的拓扑/归属人/手工基线价值。
- **读取（v2 调整，阻塞项 #4）**：**优先以"工具结果增强"方式附带**——复用 [app/agent/experts/base.py](../../../app/agent/experts/base.py) 已有的 `transform_tool_result` 钩子，在 metric/log 工具返回里自动拼接"该服务正常基线 + 当前值对比"，**Expert 无需多学一个工具**。仅在确需主动按服务名查拓扑时，再提供轻量 `lookup_service_knowledge(service_name)` 工具（结构化精确查询，非语义召回）。

### L3 用户/团队偏好 — 新增，按 owner_key 私有

- **存储**：SQLite 轻量表 `user_preferences`，主键 `owner_key`（复用 [app/services/session_scope_service.py](../../../app/services/session_scope_service.py) 的 owner 哈希，不存原始 token）。结构化字段 + 自由文本 notes。
- **内容**：默认环境、关注服务、回答详略/语言、"先查 X"习惯、升级/通知偏好。
- **写入（v2 降级首版，阻塞项 #5）**：首版**只做显式设置 API**（用户在设置页填默认环境/语言/关注服务），交互链路清晰、前端实现简单。"对话中 LLM 自动抽取偏好 + 确认落库"涉及流式问答里的确认卡片交互，复杂度高，**移到后续阶段**。
- **读取（不走工具）**：请求时由 [app/services/router_service.py](../../../app/services/router_service.py) 按 owner_key 取出，**注入 Expert 系统提示词**（体量小、对该用户始终生效），而非工具召回。

---

## 六、存储架构（复用现有基础设施）

```text
SQLite（事实源 / 可治理 / 可审计）
  experience_memories     (L1, project 共享)
  services / relations / baselines (L2, project 共享)
  user_preferences        (L3, owner_key 私有)

Milvus（语义召回索引，SQLite 失配时以 SQLite 为准）
  experience_memory       (L1 症状向量, 1024维)
  [可选] service_catalog  (L2 服务名/描述模糊匹配)
```

复用：[app/core/milvus_client.py](../../../app/core/milvus_client.py)、[app/services/vector_embedding_service.py](../../../app/services/vector_embedding_service.py)（`embed_query`→1024维）、[app/services/vector_search_service.py](../../../app/services/vector_search_service.py)、SQLite 模式参照 [app/services/checkpoint_service.py](../../../app/services/checkpoint_service.py)。**经验记忆 collection 与 RAG 文档 collection 物理隔离**，互不污染。

---

## 七、消费机制：工具召回 vs 提示注入（架构关键点）

| 记忆层 | 消费方式 | 理由 |
|---|---|---|
| L1 经验 | **工具** `recall_experience` | 体量不定、语义检索、由 Expert 按需调用，契合工具调用循环 |
| L2 服务知识 | **工具** `lookup_service_knowledge` | 结构化精确查询，按服务名取基线/拓扑 |
| L3 用户偏好 | **提示词注入** | 体量小、对该用户始终生效，无需 Expert 主动调用 |

工具统一遵循 [app/core/runtime_tools.py](../../../app/core/runtime_tools.py) 的 `RuntimeTool` 规范，注册进 [app/tools/__init__.py](../../../app/tools/__init__.py) 的分 Expert 工具集，并在 [app/agent/events.py](../../../app/agent/events.py) 产出 `tool_event` 供前端展示召回过程。

---

## 八、治理、隔离与安全

- **隔离**：每次 L1/L2 召回强制过滤 `project_id == 当前项目 && enabled == true`；L3 强制按 `owner_key`。
- **治理 API**（参照 git HEAD 的 `app/api/memory.py` 复活并扩展）：列表/详情/启用禁用/重建索引，新增反馈写入端点。
- **置信度与衰减**：`confidence` + `hit_count` + `success_count`；长期未命中或多次验证失败的经验自动降权（衰减策略可后置）。`hit_count` **按 session 去重**，避免多个 Expert 对同一事件重复召回时虚高。
- **冲突召回（v2 补充）**："同症状不同根因"另存后，召回时可能返回矛盾经验。召回工具须**按置信度排序**，并在结果中**标注"存在 N 条冲突经验"**，由 Expert 谨慎采信，而非默认取第一条。
- **审计**：经验卡保留 `source_case_ids` / `source_feedback_ids` 溯源；禁用不删除。
- **安全红线**：经验卡的 `root_cause`/`resolution`/`notes` 写入前走一次脱敏；**证据只存 `evidence_id` 引用，不存原文**，从源头收窄密钥/PII 暴露面（正则过滤作为补充，不作为唯一防线）。
- **降级**：Milvus 不可用时召回静默跳过、不阻断回答；反馈写入失败不影响主回答（沿用旧设计的 graceful degradation 原则）。

---

## 九、关键文件改动

**新增/复活（services & models）**
- `app/services/experience_memory_service.py`、`app/services/experience_memory_index_service.py`（从 git HEAD 复活并改造为工具召回）
- `app/services/service_knowledge_service.py`（L2 新增）
- `app/services/user_preference_service.py`（L3 新增）
- `app/models/memory.py`（复活并扩展三类模型）

**工具层**
- `app/tools/recall_experience.py`、`app/tools/lookup_service_knowledge.py`（新增，参照 [app/tools/knowledge_tool.py](../../../app/tools/knowledge_tool.py)）
- [app/tools/__init__.py](../../../app/tools/__init__.py)：把新工具加入 diagnosis/metric/log/change 工具集

**消费接入**
- [app/services/router_service.py](../../../app/services/router_service.py)：请求时注入 L3 偏好到 Expert 提示词
- [app/agent/experts/diagnosis.py](../../../app/agent/experts/diagnosis.py) 等：挂载召回工具

**API & 配置**
- `app/api/memory.py`（复活）：治理端点 + `POST /api/memory/feedback`
- [app/config.py](../../../app/config.py)：`project_id`、`experience_memory_*` 阈值、各开关
- [app/main.py](../../../app/main.py)：注册路由

---

## 十、分阶段落地

- **Phase 1 — L1 经验记忆（最高 ROI）**：复活并改造 experience memory 为工具召回；**必含**：回传 events timeline 并蒸馏为证据链（#1）、三路写入含弱采纳与手工冷启动录入（#2）。复用最多既有代码。
- **Phase 2 — L2 服务知识（确定性部分）**：MCP 服务清单种子导入 + 人工维护基线 + 工具结果增强对比；**不含**自动基线学习。
- **Phase 3 — L3 用户偏好（显式设置）**：设置 API + 提示注入；**不含**对话自动抽取。
- **Phase 4 — 治理与衰减**：治理 UI/API、置信度衰减、冲突召回标注、脱敏红线。
- **后续课题（不在本 PRD 范围）**：L2 自动基线学习、L3 对话偏好抽取、同症状冲突消解与版本图谱。

每个 Phase 独立可交付、可灰度。Phase 1 的 #1/#2 是上线即有效的关键，不可裁剪。

---

## 十一、验证方式

**自动化测试**（参照 git HEAD 的 `tests/test_experience_memory_service.py`、`tests/test_aiops_feedback_api.py` 复活改造）
- L1：`user_accepted=true` 生成/合并经验，`false` 不生成；相似症状+接近根因合并、不同根因另存；按 `project_id` 隔离；Milvus 不可用不阻断。
- L2：种子导入产出服务实体；`lookup_service_knowledge` 按名返回基线/拓扑。
- L3：偏好抽取需确认后落库；按 owner_key 隔离；注入提示词生效。
- 工具：召回产出 `tool_event`；Expert 不再依赖 `aiops_service`。

**端到端手测**（`/api/assistant`）
1. 提交一次诊断 → 反馈采纳 → 再问相似事件，确认 `recall_experience` 命中历史经验且提示"需先验证"。
2. 问"X 服务 CPU 现在多少正常吗"→ 确认 Expert 调用 `lookup_service_knowledge` 对比基线。
3. 声明"以后默认看生产环境"并确认 → 下一轮问答确认默认环境生效。

**回归**：确认知识 RAG 检索、短期 session scoping、各 Expert 路由均不受影响。
