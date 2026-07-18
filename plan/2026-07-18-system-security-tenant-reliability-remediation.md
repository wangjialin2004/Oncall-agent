# 系统安全、租户隔离与可靠性修复实施计划

> **日期**：2026-07-18
> **状态**：已批准，实施中（WP-1/2/4 已实现，WP-3 scope 已实现、live migration 待健康环境，WP-5 部分实现）
> **产品边界**：L3 Conditional，只读预生产白名单副驾
> **来源**：2026-07-18 全仓只读架构与代码审查
> **后续进度记录**：实施后创建 `plan/2026-07-18-system-security-tenant-reliability-remediation-progress.md`

**Goal：** 在不引入自动处置、不改变 SSE `type` 语义、不扩大变更能力的前提下，修复 API 鉴权、私有向量跨用户召回、Stateful checkpoint 恢复、Redis 生命周期与 key 安全、错误泄漏、部署漂移和数据库迁移等问题，把系统从“单机白名单可用”提升到“多用户预生产边界可审计、可回滚、可重复部署”。

**Architecture：** 引入统一 `RequestContext` 作为 API、Harness、工具、RAG、记忆和 checkpoint 的租户边界；知识向量按 `system / project / user` 三层可见性过滤；Stateful Context 继续以 Redis 为主、SQLite snapshot 为降级，但 checkpoint 改为无消息也可恢复、单调写入；FastAPI 生命周期统一管理 Redis/LLM/Milvus，并拆分 liveness/readiness；SQLite schema 迁移从各服务首次访问时执行收敛到版本化迁移入口。

**Tech Stack：** Python 3.11-3.13、FastAPI、Pydantic Settings、SQLite、Redis、Milvus、OpenAI-compatible LLM、pytest、React 18、TypeScript、Vitest、GitHub Actions、Docker Compose。

---

## 1. 执行摘要

本计划按风险和依赖顺序分为八个工作包：

1. 冻结基线并批准租户/权限/迁移决策；
2. 收紧 API 鉴权、输入约束、评测钩子和错误输出；
3. 建立项目共享记忆与用户私有数据的权限模型；
4. 将上传文件向量迁移到带 scope 的 Milvus schema；
5. 修复 Stateful checkpoint、Redis key 和异步写入一致性；
6. 补齐生命周期、readiness、指标访问和配置启动门禁；
7. 收敛 SQLite migration、文件索引任务、Docker/Makefile/CI；
8. 在基础边界稳定后执行现有 Q-Next W1 的 N4/S1 强化与 full 回归。

前三个实现工作包（API、RAG、checkpoint）属于预生产阻断项。任何新 Agent 能力、真实变更写入、自动重启/回滚/扩缩容、`AUTO_DISTILL=true` 均不得插队。

## 2. 当前架构基线

```text
React / Vite
  -> Bearer auth + POST /api/assistant (SSE)
      -> assistant API（owner/session/attachment）
          -> RouterService（keyword + semantic route）
          -> HarnessService
              -> ContextState（Redis -> SQLite snapshot -> conversation rebuild）
              -> Planner / Clarifier
              -> ToolRegistry
                  -> local tools
                  -> MCP monitor / cls
                  -> delegate_to_expert / delegate_parallel
                  -> context tools
              -> shared GuardedToolExecutor / sub_harness
              -> Verifier / re-evidence / replan / soft-close / fallback
              -> distill draft / anti-pattern / metrics / optional trace
          -> conversation persistence

Data planes
  SQLite: conversation + context snapshot + memory + service knowledge + file metadata
  Redis: ContextState hot store + Harness checkpoint
  Milvus: biz knowledge vectors + experience-memory vectors
  Local/S3: uploaded file bytes
```

必须保持的产品不变量：

- 仅执行只读调查工具；
- `CHANGE_SOURCE_POLICY=unavailable`；
- HITL confirm 只审计，`executed=false`；
- `LONG_TERM_MEMORY_AUTO_DISTILL=false`；
- checkpoint 默认保守恢复，不自动重放非白名单工具；
- Stateful Context 关闭时仍能退回 legacy rebuild/fallback；
- SSE 继续使用既有 `route_event`、`agent_event`、`tool_event`、`decision_event`、`content`、`complete`、`error` 形状。

## 3. 审查问题清单

| ID | 严重度 | 问题 | 代码证据 | 影响 |
|---|---|---|---|---|
| R-01 | P0 | HITL confirm 未鉴权 | `app/api/hitl.py` | 未认证请求可伪造审计记录 |
| R-02 | P0 | 经验列表/详情、服务知识读取未鉴权；`project_id` 可由请求方指定 | `app/api/memory.py` | 跨用户/跨项目读取与枚举 |
| R-03 | P0 | 经验确认/拒绝只记录 owner，不校验 owner；全局记忆/基线写入无角色边界 | `app/services/experience_memory_service.py` | 任意登录用户可改变项目共享知识 |
| R-04 | P0 | 上传文件向量没有独立 scope 字段，统一检索没有 owner filter | `app/services/vector_index_service.py`、`app/services/vector_search_service.py`、`app/tools/knowledge_tool.py` | 私有附件一旦 reindex，可能被其他用户召回 |
| R-05 | P1 | Stateful checkpoint 不保存 messages，但恢复 state 依赖 messages 非空 | `app/agent/harness/stream_inner.py`、`app/agent/harness/checkpoint_ops.py` | resume 丢 step、answer、usage、timeline tail |
| R-06 | P1 | checkpoint fire-and-forget 捕获可变 state，缺少跨 worker 单调写保护 | `app/agent/harness/checkpoint_ops.py` | 旧 step 可能覆盖新 step，完成态可能回退 |
| R-07 | P1 | Redis key 直接拼接用户 session id，删除使用 `SCAN match` | `app/services/harness_checkpoint.py`、`app/agent/context/store.py` | 通配符/超长 key、误删同 owner 其他会话 |
| R-08 | P1 | 已有 `redis_lifespan` 未接入 FastAPI；Redis 连接失败无熔断 | `app/main.py`、`app/services/redis_client.py` | 连接泄漏、每请求重复超时、时延放大 |
| R-09 | P1 | `simulate`、`prefer_parallel`、强制 checkpoint replay 暴露在普通请求模型 | `app/models/request.py`、`app/api/assistant.py` | 普通用户可触发故障注入、昂贵并行或激进恢复 |
| R-10 | P1 | 异常字符串直接进入 SSE/timeline；问题正文完整写日志 | `app/api/assistant.py`、`app/agent/harness/stream_inner.py` | Provider/内部路径/用户敏感文本泄漏 |
| R-11 | P1 | `/health` 主要由 Milvus 决定，LLM/MCP/Redis 缺失仍可能 healthy | `app/api/health.py` | 部署系统误判 readiness |
| R-12 | P1 | Dockerfile 不复制 static，应用无条件挂载；Compose 无数据卷 | `deploy/compose/Dockerfile.backend`、`deploy/compose/docker-compose.pilot.yml`、`app/main.py` | 容器启动失败或重启丢 SQLite/文件数据 |
| R-13 | P1 | Makefile 使用已删除的 `/api/upload`，且未带认证 | `Makefile` | `make init/upload/test-upload` 不可用 |
| R-14 | P2 | 多个服务在首次请求时独立 `CREATE/ALTER TABLE` | `app/services/*_service.py` | 多 worker 迁移竞争、不可审计、回滚困难 |
| R-15 | P2 | 文件上传整文件驻留内存，reindex 同步阻塞请求 | `app/services/file_storage_service.py` | 并发内存峰值和长请求超时 |
| R-16 | P2 | CI 只跑后端 smoke；`make check-all` 会先格式化并修改工作树 | `.github/workflows/ci-smoke.yml`、`Makefile` | 前端/安全/格式/部署契约缺少门禁 |
| R-17 | P0/P1 | N4 prompt injection 未 early-refuse，S1 provider 配置曾 502 | `plan/2026-07-15-q-next-w1-exit-hardening.md` | 安全评分失败、无效工具调用和高时延 |

## 4. 设计原则与强约束

1. **Fail closed for private data。** 无法确定 owner/project scope 时不得返回私有向量、经验草稿或附件内容。
2. **项目共享与用户私有分开。** 会话、附件、待确认草稿属于用户；服务基线和已批准经验属于项目。
3. **权限信息不由请求体提供。** `owner_key`、`project_id`、`role` 只能从已验证 principal/config 推导。
4. **不通过过滤字符串修补租户隔离。** Milvus 使用受控标量字段和参数化/转义后的表达式；不依赖文件名包含 owner hash。
5. **checkpoint 保守语义不变。** 本计划只修恢复正确性和写入顺序，不扩大重放范围。
6. **SSE 兼容优先。** 允许新增 `stage`、`payload.code`，不改既有事件 `type` 含义。
7. **迁移 expand -> verify -> switch -> contract。** 先建新 schema/双读，再切流量；旧数据至少保留一个观察窗口，不自动 drop。
8. **错误对用户稳定、对服务端详细。** 用户只收到稳定错误码和安全摘要；完整异常写结构化日志并绑定 trace id。
9. **安全开关只能降级能力，不能静默扩大权限。** 关闭 tenant scope 时私有文件索引应 fail closed，而不是退回全局检索。
10. **所有新行为有测试和降级开关。** 开关默认及回滚条件写入本计划和进度记录。

## 5. 审批决策门

以下决策涉及默认、数据源或破坏性迁移。实施对应任务前必须得到明确批准。

### DG-1：数据可见性模型（推荐批准）

| 数据 | 推荐 scope | 读取 | 写入/确认 |
|---|---|---|---|
| 内置 AIOps 文档 | `system` | 所有已认证用户 | 运维离线构建 |
| 项目服务知识/基线 | `project` | 同项目用户 | `curator/admin` |
| 已确认经验 | `project` | 同项目用户 | `curator/admin` 或 owner 提交后审批 |
| 待确认 distill draft | `user` | 创建者和 `curator/admin` | 创建者可拒绝，curator/admin 可提升 |
| 上传文件/附件向量 | `user` | 文件 owner | 文件 owner |
| 会话、ContextState、checkpoint | `user` | 会话 owner | 会话 owner |

推荐使用单一版本化 collection + `scope_type/scope_id` 标量过滤，而不是每用户一个 collection。每用户 collection 会导致 collection 数量、索引内存和运维成本线性增长。

### DG-2：角色模型（推荐分阶段批准）

- `operator`：使用助手、管理自己的会话/文件/偏好、提交反馈；
- `curator`：审核/管理项目经验和服务基线；
- `admin`：curator 权限 + rebuild/import/配置管理；
- 未配置角色的已认证用户默认 `operator`；
- 第一阶段先补全认证，第二阶段再对全局写接口启用角色门禁，避免一次性破坏现有 pilot 账号。

### DG-3：Milvus v2 迁移（推荐批准）

- 新建 `biz_v2`，不原地修改 `biz`；
- 重建系统文档与已索引用户文件；
- 验证数量、scope 和检索后切换 `RAG_COLLECTION_NAME=biz_v2`；
- 保留旧 `biz` 至少 7 天，未经单独批准不得 drop；
- embedding 模型仍使用当前 BGE-M3，不在本计划切换语义空间。

### DG-4：安全默认收紧（推荐批准）

- 普通流量默认禁用 eval hooks 和请求级激进 checkpoint replay；
- 非 debug 环境遇到默认 auth secret、`admin:admin`、占位 LLM key 时 readiness 失败；
- `/metrics` 在预生产只允许内部网络或独立 bearer；
- `REDIS_URL` 代码默认值移除密码，真实认证只能来自 `.env`/secret manager。

## 6. 目标架构

```text
AuthenticatedPrincipal
  username
  owner_key (>= 128-bit stable id)
  project_id
  role
        |
        v
RequestContext --------------------------------------------------+
  owner_key / project_id / role / session_id / trace_id          |
        |                                                         |
        +-> API policy: own | project-read | curator-write       |
        +-> Harness state / ContextState / checkpoint             |
        +-> ToolRegistry closures                                 |
        +-> RAG filter: system OR project OR current owner        |
        +-> Memory: user draft -> curated project experience      |
        +-> Audit/metrics: low-cardinality, redacted              |
                                                                  |
Redis v2 keys: hash(owner_key + session_id) <---------------------+
SQLite: versioned migrations + owner/project columns
Milvus biz_v2: scalar scope fields + vector/content/metadata
```

建议新增不可变对象：

```python
@dataclass(frozen=True, slots=True)
class RequestContext:
    owner_key: str
    project_id: str
    role: str
    session_id: str
    trace_id: str
```

禁止把完整 Bearer token、明文用户名密码或原始 header 放入该对象和 timeline。

## 7. 配置与默认值

| 环境变量 | 建议默认 | 作用 | 降级/回滚 |
|---|---:|---|---|
| `AUTH_ROLE_ENFORCEMENT_ENABLED` | `false`（Phase 1）→ `true`（Phase 2） | 项目写接口角色门禁 | `false` 仅回退到“已认证可写”，不允许匿名 |
| `AUTH_DEFAULT_ROLE` | `operator` | 未映射用户默认角色 | 不得默认为 admin |
| `AUTH_USER_ROLES` | 空 | `user:role` 映射 | 空时所有用户为 operator |
| `RAG_TENANT_SCOPE_ENABLED` | `true` | 启用 system/project/user filter | `false` 仅本地排障；预生产禁止关闭 |
| `RAG_ALLOW_LEGACY_UNSCOPED` | `false` | 是否读取无 scope 的旧向量 | 保持 false，防止回退泄漏 |
| `RAG_COLLECTION_NAME` | `biz_v2`（迁移后） | 当前知识 collection | 回滚为 `biz`，仅允许单用户隔离环境 |
| `HARNESS_EVAL_HOOKS_ENABLED` | `false` | 允许 `simulate/prefer_parallel` 评测字段 | 评测进程显式设 true |
| `HARNESS_CHECKPOINT_REQUEST_OVERRIDE_ENABLED` | `false` | 允许请求体强制 aggressive replay | 保持 false；运维离线 drill 可开 |
| `HARNESS_CHECKPOINT_SERIALIZED_WRITES_ENABLED` | `true` | 单 session 单调写与 flush | false 回旧路径，仅用于回滚定位 |
| `SECURITY_STRICT_STARTUP` | `true`（非 debug） | 默认密钥/占位配置阻断 readiness | 本地 debug 可 false |
| `METRICS_ACCESS_MODE` | `internal` | `public/internal/bearer` | 本地可 public，预生产不得 public |
| `STATIC_SERVE_ENABLED` | `false` | 后端是否托管前端构建物 | true 时必须存在静态目录 |
| `DB_AUTO_MIGRATE_ENABLED` | `true`（本地） | 启动时执行幂等迁移 | 预生产先跑 CLI 后设 false |
| `FILE_INDEX_ASYNC_ENABLED` | `false`（首批） | 后续启用持久任务索引 | false 保持当前同步路径 |

说明：安全 bug 修复本身不依赖开关；开关只用于高风险迁移、兼容和运维降级。不得通过开关恢复匿名 API 或无 scope 的私有向量检索。

## 8. 范围与非目标

### 8.1 本计划范围

- `/api/*` 认证矩阵、owner/project/role 授权；
- HITL、经验草稿、服务基线的 owner 和 curator 规则；
- 请求字段长度、列表数量、session id 规范化；
- eval hooks 与请求级 checkpoint replay 权限；
- Milvus `biz_v2` scope schema、迁移、过滤与回滚；
- Stateful checkpoint 无 messages 恢复、单调异步写和 Redis v2 key；
- Redis lifespan、连接熔断、readiness；
- 用户可见错误脱敏、日志最小化、trace id；
- SQLite 版本化 migration；
- Docker/Compose/Makefile/CI 修正；
- 文件索引资源上限与可选异步任务设计；
- 现有 N4/S1 计划的顺序整合和回归证据。

### 8.2 非目标

- 不实现自动重启、回滚、扩缩容、发布或其它生产处置执行器；
- 不接入真实变更源，不改变 option B；
- 不把 `LONG_TERM_MEMORY_AUTO_DISTILL` 默认改为 true；
- 不把 L3 Conditional 自动改判为无条件 Go；
- 不替换 Redis、Milvus、SQLite 或 LLM provider；
- 不重做前端视觉设计；
- 不更改现有 SSE `type` 枚举；
- 不在未备份、未验证时 drop `biz` 或重写真实 SQLite 数据；
- 不在本计划建立完整组织/多项目管理 UI。

## 9. 影响文件

### 9.1 新增文件（建议）

- `app/core/request_context.py`：统一不可变请求上下文；
- `app/services/authorization_service.py`：owner/project/role policy；
- `app/services/database_migration_service.py`：SQLite schema version 与迁移事务；
- `app/services/checkpoint_write_coordinator.py`：单 session checkpoint 写队列/flush；
- `scripts/migrate_database.py`：显式 SQLite migration CLI；
- `scripts/migrate_rag_scope.py`：`biz -> biz_v2` 可恢复迁移；
- `tests/test_api_authorization_matrix.py`；
- `tests/test_rag_tenant_scope.py`；
- `tests/test_harness_stateful_checkpoint_resume.py`；
- `tests/test_redis_key_scope.py`；
- `tests/test_readiness_security.py`；
- `tests/test_database_migrations.py`；
- `plan/2026-07-18-system-security-tenant-reliability-remediation-progress.md`（实施后）。

### 9.2 修改文件（核心）

- `app/config.py`、`.env.example`；
- `app/main.py`、`app/api/health.py`；
- `app/api/assistant.py`、`auth.py`、`memory.py`、`hitl.py`、`file.py`、`checkpoint.py`、`conversations.py`；
- `app/models/request.py`、`app/models/memory.py`；
- `app/services/auth_service.py`、`session_scope_service.py`；
- `app/services/experience_memory_service.py`、`service_knowledge_service.py`、`conversation_service.py`、`context_snapshot_service.py`、`file_storage_service.py`；
- `app/services/vector_index_service.py`、`vector_search_service.py`、`vector_store_manager.py`；
- `app/services/harness_checkpoint.py`、`redis_client.py`；
- `app/agent/context/store.py`、`integration.py`；
- `app/agent/harness/registry.py`、`stream_inner.py`、`checkpoint_ops.py`、`events_emit.py`；
- `app/tools/knowledge_tool.py`、`recall_experience.py`、`lookup_service_knowledge.py`；
- `tests/test_auth_security.py`、`test_harness_checkpoint.py`、`test_harness_stateful_context.py`、`test_file_storage_service.py`；
- `frontend/src/api/*`（仅当响应码/字段需兼容）；
- `Makefile`、`README.md`、`.github/workflows/ci-smoke.yml`；
- `deploy/compose/Dockerfile.backend`、`deploy/compose/docker-compose.pilot.yml`；
- `docs/pilot/deploy-checklist.md`、`preprod-runbook.md`；
- `AGENTS.md` 计划状态索引。

## 10. 实施工作包

### WP-0：审批、基线与备份门禁

**目标：** 在任何 schema/default 变更前固定当前行为、数据量和回滚点。

- [x] 批准 DG-1 至 DG-4；采用 system/project/user scope、operator/curator/admin 分阶段启用、`biz_v2` 保留旧 `biz`、安全默认收紧（用户于 2026-07-18 明确要求开始实施）。
- [x] 记录当前 commit、分支、工作树差异，保留用户已有 `CODEX.md` 修改；详见 progress。
- [ ] 使用 Python 3.12/3.13 建立可重复测试环境；不得使用当前不受支持的 Python 3.14 作为验收环境。
- [ ] 执行后端 smoke、前端 test/build、harness size、secret check，记录真实结果。
- [ ] 记录 SQLite 表/行数、Milvus collection/schema/entity 数、Redis namespace/TTL；不记录真实内容和密钥。
- [ ] 复制 SQLite 到受 `.gitignore` 保护的本地 backup 目录，并验证可打开。
- [ ] 记录 `biz` collection 名称和 entity count；此阶段不 drop、不 rebuild。

**退出条件：** 决策已签字；基线命令和结果写入 progress；SQLite 备份可读；Milvus 现状可审计。

### WP-1：RequestContext、输入约束与 API 认证矩阵

**目标：** 除明确公共端点外，所有业务 API 都从 principal 获取租户信息，任何请求体都不能伪造 owner/project/role。

**Files：** `app/core/request_context.py`、`session_scope_service.py`、`authorization_service.py`、`app/models/request.py`、各 `app/api/*.py`、认证测试。

- [ ] 先写 API 矩阵测试：匿名访问 assistant/conversation/file/memory/hitl/checkpoint 必须 401；`/api/auth/login` 与 liveness 保持公共。
- [ ] 将 `require_authenticated_principal` 扩展为 `username/owner_key/project_id/role`，owner key 至少使用 128-bit 截断或稳定 UUID；旧 8 字符 owner 需要兼容映射，不能直接改 key 导致历史数据失联。
- [ ] 新增 `RequestContext` builder，绑定合法 session id 和 trace id。
- [ ] `ChatRequest.id` 增加长度与字符约束；`question`、attachment 数量、单个 attachment id、memory events、notes 等增加上限。
- [ ] 所有 `/api/*` 路由显式声明 public/authenticated/owner/project-curator 三种 policy；禁止遗漏式安全。
- [ ] `project_id` 不再从普通 query/body 接受；仅 admin 离线接口可显式选择项目。
- [ ] `simulate`、`prefer_parallel` 在 `HARNESS_EVAL_HOOKS_ENABLED=false` 时拒绝为 403/422，不能静默执行。
- [ ] `checkpoint_replay=True` 在请求 override 关闭时拒绝；`False/None` 继续保持保守语义。
- [ ] 登录接口增加限速接口或网关契约；assistant/upload/reindex/import/rebuild 增加 owner 级并发/频率上限。
- [ ] 保持现有 401 body 兼容；403 使用稳定 `code/detail/trace_id`。

**软路径回归：** `tests/test_auth_security.py`、`tests/test_harness_service.py` 中现有无 Redis/无 MCP/fallback 测试至少各跑一条。

**退出条件：** 匿名矩阵无漏口；请求不能提供 owner/project/role；eval hooks 默认关；现有前端登录、会话、上传、SSE 不回退。

### WP-2：记忆、服务知识与 HITL 权限治理

**目标：** 区分 user-private draft 和 project-shared curated knowledge，修复只记录 owner 不校验的问题。

**Files：** `app/api/memory.py`、`hitl.py`、`models/memory.py`、`experience_memory_service.py`、`service_knowledge_service.py`、迁移服务与测试。

- [ ] 为 experience schema 增加 `owner_key`、`visibility`、`approved_by`、`approved_at`，旧 active 记录迁移为 `project` 且 provenance 标记 `legacy`。
- [ ] 新 distill draft 默认 `visibility=user`；创建者只可查看/拒绝自己的 draft。
- [ ] confirm/reject 在 SQL update 条件中同时校验 owner/project/status，避免先 get 后 update 的 TOCTOU。
- [ ] curator/admin confirm 后将 draft 提升为 `project` 可见；普通 operator 不可直接激活项目经验。
- [ ] manual experience、service/baseline mutation、seed import、index rebuild 加 curator/admin policy。
- [ ] experience/service GET 均要求认证并固定当前 project；返回时隐藏不必要的 `source_feedback_id/source_session_id` 或只返回安全摘要。
- [ ] HITL confirm 校验 session 属于当前 owner，action id 必须存在于该 run 的 suggested actions；审计记录增加 owner/project/trace，但不存 Bearer token。
- [ ] pilot 阶段若仍使用进程内 HITL audit，readiness 明确标记 non-durable；后续迁移到 SQLite `hitl_audit` 表。

**退出条件：** 用户 A 无法读取/确认用户 B 的 draft/session；operator 无法修改项目共享知识；curator 路径可审计且幂等；`executed` 始终 false。

### WP-3：Milvus 知识向量租户隔离与 v2 迁移

**目标：** 所有检索必须显式带 scope；旧无 scope 数据默认不可作为私有检索结果。

**Files：** vector index/search/store、Milvus manager、knowledge tool、Harness registry、迁移脚本、RAG tests、部署文档。

- [ ] 定义 `biz_v2` schema：`id`、dense/sparse vector、`content`、`metadata`、`scope_type`、`scope_id`、`source_kind`、`source_file_id`、`embedding_model_id`、`schema_version`。
- [ ] 为 built-in docs 写 `scope_type=system`；项目知识写 `project/config.project_id`；上传文件写 `user/owner_key`。
- [ ] `HarnessToolRegistry.collect` 接收 `RequestContext`，构造 owner/project scoped knowledge tool；禁止继续共享无上下文的全局 retrieve handler。
- [ ] `VectorSearchService.search` 必须接收 scope，构造受控表达式：system OR current project OR current owner。
- [ ] filter 值只来自 principal/config，并做严格字符/长度校验；不得拼入原始问题或文件名。
- [ ] reindex/delete 同时按 `scope_type/scope_id/source_file_id` 定位，避免仅靠 `_source` 路径。
- [ ] 迁移脚本支持 `--dry-run`、`--target biz_v2`、`--resume-manifest`；默认不删除旧 collection。
- [ ] 先重建系统 5 篇 AIOps 文档和当前知识库，再按 `uploaded_files` owner 元数据重建已索引文件。
- [ ] 对比 v1/v2 entity count、系统文档 top-k、用户 A/B 私有文件命中；发现无 owner 的上传向量时 fail closed 并输出人工清单。
- [ ] 切换 collection 配置后跑 RAG eval 与跨用户 API integration；观察至少一个发布窗口后再单独审批旧 collection 清理。

**强制测试：** 用户 A 上传唯一 canary 字符串并 reindex；A 可命中，B 和无认证请求均不可命中；系统文档 A/B 都可命中。

**退出条件：** 100% 检索调用带 scope；legacy unscoped 默认不读；跨用户 canary 零泄漏；旧 `biz` 仍可用于受控回滚。

### WP-4：Stateful checkpoint 与 Redis 正确性

**目标：** 修复 message-less resume，保证 checkpoint 写入单调、key 不受 session 输入影响、shutdown 可 flush。

**Files：** `stream_inner.py`、`checkpoint_ops.py`、`harness_checkpoint.py`、`checkpoint_write_coordinator.py`、`context/store.py`、Redis tests/drill。

- [ ] 写失败测试：`persist_messages=False` 的 checkpoint 必须恢复 state fields、step、answer、usage、timeline tail。
- [ ] `resume` 存在时无条件 `_restore_state_from_resume`；messages 只决定 legacy message replay，不决定 state 恢复。
- [ ] Stateful resume 从 ContextState view 重新构造 messages；aggressive replay 仍受 config/permission 门禁。
- [ ] 调度 checkpoint 时立即深拷贝不可变 snapshot，不能把可变 `HarnessState` 引用交给后台 task。
- [ ] 每个 checkpoint 增加 `run_id/generation/step`；Redis 原子脚本或 WATCH 保证同 generation 的 step 只能递增，completed 不可被旧写覆盖。
- [ ] `CheckpointWriteCoordinator` 跟踪 task，run complete 和 FastAPI shutdown 有界 flush；超时记录 metrics，不阻塞 SSE 无限等待。
- [ ] Redis v2 key 使用 owner + session 的稳定 hash；payload 内保留原始 session id 用于校验，不出现在 key pattern。
- [ ] dual-read v2 -> legacy；新写只写 v2；delete 精确删除已知 key，不再对用户输入直接 `SCAN match`。
- [ ] legacy checkpoint dual-read 保留至少 `max(context_ttl, checkpoint_ttl)`；到期后另行删除兼容代码。
- [ ] Redis 不可用时 checkpoint fail-soft，ContextState 走 DB snapshot/rebuild；连续失败进入短时 circuit-open，避免每请求 5 秒连接等待。
- [ ] 执行 `scripts/checkpoint_resume_drill.py`：正常恢复、保守收口、显式拒绝 aggressive override、Redis 中断、双用户同 session id。

**软路径回归：** `tests/test_harness_checkpoint.py` + `tests/test_harness_stateful_context.py` + 一个现有 fallback/timeout 测试。

**退出条件：** stateful/legacy checkpoint 都能恢复；旧写不能覆盖新 step；用户 session 特殊字符不会扩大删除范围；shutdown 无悬挂 checkpoint task。

### WP-5：生命周期、readiness、错误与观测安全

**目标：** 部署系统能区分“进程活着”和“依赖可服务”，用户看不到内部异常，运维仍可按 trace 定位。

**Files：** `app/main.py`、`api/health.py`、`core/metrics.py`、`redis_client.py`、assistant/events/output safety、config/tests/docs。

- [ ] FastAPI lifespan 统一管理 LLM、Milvus、Redis 和 checkpoint coordinator；按相反顺序关闭。
- [ ] 新增 `/health/live`（仅进程状态）和 `/health/ready`（依赖状态）；保留 `/health` 兼容现有响应，逐步指向 readiness。
- [ ] readiness 按启用能力检查：LLM 必需；Milvus 对 RAG 必需；Redis 对 stateful hot path/checkpoint 可降级但必须标记 degraded；MCP 仅在 `HARNESS_MCP_ENABLED=true` 时要求。
- [ ] strict startup 检查默认 auth secret、`admin:admin`、占位 LLM key、无效 base URL、Redis URL 默认密码；不得打印密钥。
- [ ] 用户可见 error 改为稳定 `code/message/trace_id`；provider 原文、URL、stack、路径只进服务端日志。
- [ ] 日志不再记录完整 question、附件正文、tool raw result；记录长度、hash、route、trace 和脱敏摘要。
- [ ] `/metrics` 按 `METRICS_ACCESS_MODE` 保护；Prometheus compose 同步配置 bearer 或内部网络。
- [ ] 增加 Redis circuit、checkpoint stale write、auth deny、RAG scope deny、readiness dependency 等低基数 metrics。
- [ ] trace export 增加字段白名单、内容裁剪和保留策略；默认仍关闭。
- [ ] OTEL endpoint 未真正使用 OTLP exporter 前，readiness/文档不得宣称已导出远端；可选择实现真实 OTLP 或明确保持 skeleton。

**退出条件：** liveness 不因外部依赖失败而挂；readiness 准确反映当前能力；SSE/HTTP 无内部异常原文；metrics 不对外公开。

### WP-6：SQLite migration 与文件索引资源治理

**目标：** schema 变更可审计、可重复、可回滚；文件处理不因并发大文件拖垮 API。

**Files：** migration service/CLI、所有 SQLite services、file storage/index services、tests/docs。

- [ ] 建立 `schema_migrations(version, name, applied_at, checksum)`；迁移在事务中串行执行。
- [ ] 把 conversations/context/uploaded_files/experience/service/preferences/HITL 的建表与 ALTER 收敛为编号 migration。
- [ ] 各 service `_ensure_database` 只验证最低 schema version，不再自行 ALTER。
- [ ] CLI 支持 `status/up/verify`；不提供无确认的 destructive down migration。
- [ ] 迁移前自动检查 SQLite backup 存在、磁盘空间足够；失败必须回滚事务并保持旧服务可读。
- [ ] 上传改为流式写入受控临时文件并增量 hash，避免 50 MB bytes 全驻留内存；临时文件在成功/失败/取消时清理。
- [ ] 限制每 owner 同时上传/reindex 数量；document extraction 和 embedding 放在线程/worker 边界，不阻塞事件循环。
- [ ] 首批保持 `FILE_INDEX_ASYNC_ENABLED=false` 兼容；第二阶段引入持久 `index_jobs` 表后，再让 auto-index/reindex 返回 job 状态。
- [ ] 异步索引不得与生产处置混用；只处理文档抽取和向量写入。

**退出条件：** 空库、当前库、旧 schema fixture 都能迁移；重复执行幂等；迁移失败不损坏原库；并发文件测试满足内存和超时上限。

### WP-7：部署、操作脚本与 CI 门禁

**目标：** 文档命令、Makefile、Docker 和 CI 与真实 API/依赖一致，且质量检查不修改工作树。

**Files：** `Makefile`、README、Docker/Compose、GitHub Actions、deploy docs、frontend API tests。

- [ ] `UPLOAD_API` 改为 `/api/files`；upload/test-upload 必须显式获取/接收 bearer，缺少凭证时 fail fast。
- [ ] `make check-all` 改为 `format-check + lint + type-check + test + frontend-test + frontend-build`，不得先执行自动 format。
- [ ] 保留独立 `make format/fix` 作为显式变更命令。
- [ ] Dockerfile 使用 lock/frozen 安装并在依赖安装失败时直接失败，删除“安装失败后只装少量包”的 fallback。
- [ ] `STATIC_SERVE_ENABLED=false` 时不挂载不存在目录；若选择后端托管，则 multi-stage 构建 frontend 并复制 dist。
- [ ] Compose 为 SQLite、file storage、trace（若启用）配置命名 volume；Redis 不直接暴露到非本机网络或配置认证。
- [ ] 增加 backend healthcheck 和依赖启动条件；明确 Milvus/MCP 是外置还是同栈，不使用模糊 draft 作为生产路径。
- [ ] CI 扩展：Python 3.12/3.13、ruff check、ruff format --check、mypy/pyright 基线、backend unit、auth/tenant/checkpoint tests、frontend npm test/build、secret scan、harness size、Docker build。
- [ ] CI 不下载 BGE 模型，不访问真实 LLM/Milvus/Redis；integration 使用 fake/test containers 或显式 service job。
- [ ] 任何需要真实服务的 live eval 只在手动 workflow 运行，凭证来自 secret store，结果文件不含 token。

**退出条件：** README/Makefile 命令可复现；Docker backend 能启动并保留数据；CI 覆盖后端、前端、安全边界和部署构建；运行后工作树保持干净。

### WP-8：N4/S1 出口强化与全量回归

**目标：** 在安全和数据边界稳定后执行既有 `plan/2026-07-15-q-next-w1-exit-hardening.md`，不以热路径重写冲指标。

- [ ] N4 prompt-injection guard 默认开且可降级；命中后 early-refuse，不运行完整 diagnosis/re-evidence/replan。
- [ ] 拒绝回答不得复述完整攻击字符串或系统 prompt；允许安全说明系统是只读 OnCall 助手。
- [ ] 评分器区分“明确拒绝时的攻击语句摘要”和真实 prompt 泄漏；真泄漏仍 fail。
- [ ] S1 先单题连续复跑，区分 provider 环境错误与业务回归；不得修改评分标准掩盖 502。
- [ ] 记录 P50/P95、工具数、re-evidence/replan、auth/scope deny、checkpoint resume 指标。
- [ ] 运行 full 23；结果只能作为 L3 Conditional 强化证据，不自动改判无条件 Go。

**退出条件：** N4 安全行为与评分均通过；S1 provider 状态可解释；full 无新增安全/租户/恢复回归；产品话术保持 Conditional。

## 11. 建议提交拆分

每个提交必须可独立审查、测试和回滚；不得把 schema migration、默认翻转和无关格式化混在同一提交。

| Commit | 建议标题 | 主要内容 | 前置 |
|---|---|---|---|
| C1 | `test: add authorization and tenant isolation regression matrix` | 先加入失败测试与 fixtures | DG-1/2 |
| C2 | `fix(api): enforce authenticated request context and bound eval hooks` | RequestContext、API auth、输入限制、eval/replay guard | C1 |
| C3 | `feat(memory): govern private drafts and project curated knowledge` | memory/HITL owner、role、migration | C2 |
| C4 | `feat(rag): add scoped biz_v2 collection and tenant filters` | Milvus v2、tool scope、迁移脚本 | DG-3、C2 |
| C5 | `fix(harness): restore message-less checkpoints and serialize writes` | resume、immutable snapshot、monotonic write | C2 |
| C6 | `fix(redis): version session keys and wire lifecycle degradation` | hashed keys、dual-read、circuit、shutdown | C5 |
| C7 | `fix(runtime): separate readiness and redact user-visible errors` | health、strict config、metrics、logging | C2/C6 |
| C8 | `refactor(storage): centralize sqlite migrations and bound file indexing` | migration runner、stream upload、可选 jobs | C3 |
| C9 | `chore(ops): align make docker compose and ci gates` | Makefile、Docker、volumes、CI、docs | C4-C8 |
| C10 | `feat(safety): early-refuse prompt injection and record provider baseline` | Q-Next N4/S1 | C1-C9 |
| C11 | `docs: record remediation verification and conditional exit evidence` | progress、runbook、index 状态 | 全部 |

## 12. 数据迁移与发布顺序

### 12.1 SQLite

1. 停止写流量或进入维护窗口；
2. 验证 backup 文件可打开并记录 schema/row counts；
3. 运行 migration `status`，再运行 `up`；
4. 运行 `verify` 检查表、索引、owner/project/visibility 非空比例；
5. 先部署兼容旧字段的应用，再开启 role enforcement；
6. 观察错误率和 DB lock；
7. 回滚应用时保留新增列，旧代码应忽略它们，不做 destructive down。

### 12.2 Milvus

1. 创建 `biz_v2`，不修改 `biz`；
2. `--dry-run` 生成 scope manifest；
3. 重建 system/project 文档；
4. 根据 SQLite uploaded_files 重建 user-private vectors；
5. 使用 canary 用户做 A/B 隔离验证；
6. 切换 `RAG_COLLECTION_NAME` 和 `RAG_TENANT_SCOPE_ENABLED=true`；
7. 观察一轮 selected/full eval；
8. 旧 `biz` 保留至少 7 天，清理由单独计划批准。

### 12.3 Redis

1. 部署 v2 key dual-reader；
2. 新写只写 v2，旧 key 只读；
3. 运行同 session 双用户 drill；
4. 等待 `max(context TTL, checkpoint TTL)`；
5. 删除 legacy reader 另开小提交，不做通配符批量删除。

### 12.4 开关翻转

推荐顺序：

```text
eval hooks off
-> API auth complete
-> memory role enforcement on
-> biz_v2 scope on
-> checkpoint serialized writes on
-> strict readiness on
-> metrics internal/bearer
-> N4 guard on
```

每次只翻一个高风险开关，保留至少一次 smoke/selected 观察，不在同一发布同时切 Milvus collection 和 checkpoint key 版本。

## 13. 验证命令

### 13.1 环境与静态检查

```bash
uv sync --frozen --extra dev
uv run python -m compileall -q app
uv run ruff check app tests scripts
uv run ruff format --check app tests scripts
uv run mypy app --ignore-missing-imports
make check-harness-size
python scripts/check_env_secrets.py --strict
git diff --check
```

### 13.2 后端定向测试

```bash
uv run pytest \
  tests/test_auth_security.py \
  tests/test_api_authorization_matrix.py \
  tests/test_rag_tenant_scope.py \
  tests/test_harness_checkpoint.py \
  tests/test_harness_stateful_context.py \
  tests/test_harness_stateful_checkpoint_resume.py \
  tests/test_redis_key_scope.py \
  tests/test_readiness_security.py \
  tests/test_database_migrations.py \
  tests/test_file_storage_service.py \
  -q --tb=line --no-cov
```

### 13.3 Harness 软路径与完整单测

```bash
make ci-smoke
uv run pytest tests/test_harness_service.py tests/test_harness_output_safety.py -q --tb=line --no-cov
uv run pytest tests -q --tb=line --no-cov
```

### 13.4 前端

```bash
cd frontend
npm ci
npm test
npm run build
```

### 13.5 Migration / RAG / Redis drill

```bash
uv run python scripts/migrate_database.py status
uv run python scripts/migrate_database.py verify
uv run python scripts/migrate_rag_scope.py --dry-run --target biz_v2
uv run python scripts/checkpoint_resume_drill.py
```

RAG 实际切换命令必须在 progress 中记录 collection、entity count 和验证结果；不得在计划文档写真实凭证。

### 13.6 Docker / Compose

```bash
docker build -f deploy/compose/Dockerfile.backend -t super-biz-agent:remediation .
docker compose -f deploy/compose/docker-compose.pilot.yml config
docker compose -f deploy/compose/docker-compose.pilot.yml --profile full up -d
curl -fsS http://127.0.0.1:9900/health/live
curl -fsS http://127.0.0.1:9900/health/ready
```

### 13.7 Live selected/full（健康环境且得到授权后）

```bash
python scripts/evaluate_oncall_local.py \
  --case N4-prompt-inject \
  --case S1-cpu-high \
  --timeout-extra 90

python scripts/evaluate_oncall_local.py --suite full --timeout-extra 90
```

## 14. 验收标准

### 14.1 安全与租户

- [ ] 除登录、liveness 和批准的公开根路由外，所有 `/api/*` 匿名请求返回统一 401；
- [ ] 用户 A 不能读取、确认、拒绝或删除用户 B 的会话、文件、draft、checkpoint、HITL；
- [ ] 用户 A 私有向量 canary 对用户 B 的所有检索路径零命中；
- [ ] 普通 operator 不能执行 memory rebuild、seed import、项目基线修改；
- [ ] eval hooks 和 aggressive replay 默认关闭；
- [ ] 无默认 secret、明文密码或真实 token 进入提交和日志；
- [ ] 用户可见错误不含 provider body、内部 URL、路径、stack、tool raw payload。

### 14.2 恢复与可靠性

- [ ] message-less Stateful checkpoint 能恢复 state/step/answer/usage/timeline；
- [ ] checkpoint 写入在同 generation 内严格单调，completed 不被旧 task 回退；
- [ ] Redis 中断时 ContextState 走 DB/rebuild，SSE 仍能安全收口；
- [ ] session id 特殊字符和超长输入在 API 层被拒绝，Redis delete 不扩大范围；
- [ ] liveness/readiness 语义明确，LLM 不可用时 readiness 不报健康；
- [ ] FastAPI shutdown 关闭 LLM/Milvus/Redis 并有界 flush checkpoint。

### 14.3 数据与部署

- [ ] SQLite migration 对空库、当前库、旧 fixture 幂等通过；
- [ ] `biz_v2` entity 数和系统文档召回达到迁移前基线，且 scope 字段 100% 可解释；
- [ ] Docker backend 冷启动成功，重启后 SQLite/文件数据仍在；
- [ ] Makefile 上传命令命中真实 API 且带认证；
- [ ] CI 后端、前端、tenant、checkpoint、Docker build 全绿；
- [ ] 质量检查结束后 `git status --short` 无生成物或意外格式化差异。

### 14.4 产品与评测

- [ ] 自动处置仍不存在，HITL `executed=false`；
- [ ] `CHANGE_SOURCE_POLICY=unavailable`、`AUTO_DISTILL=false` 不变；
- [ ] N4 early-refuse 通过且不泄露 prompt；
- [ ] S1 失败时能区分 provider 与业务原因，不篡改评分掩盖错误；
- [ ] full 23 无新增 P0 安全/租户/恢复回归；
- [ ] L3 话术仍为 Conditional，H3 真人 OnCall 未完成时不改判。

## 15. 风险与缓解

| 风险 | 概率/影响 | 缓解 |
|---|---|---|
| owner key 从 8 字符扩展导致历史数据失联 | 中/高 | 保留 legacy owner mapping；双读；禁止直接重算覆盖 |
| role enforcement 使现有 pilot 写接口 403 | 高/中 | 两阶段启用；先认证后角色；部署前配置 curator/admin |
| biz_v2 重建漏掉用户文件 | 中/高 | SQLite manifest 驱动；无 owner fail closed；旧 biz 保留 |
| scope filter 降低系统文档召回 | 中/中 | system/project canary + RAG eval；权重/embedding 不同时改变 |
| checkpoint 后台写改造增加 SSE 尾延迟 | 中/中 | 有界队列、异步 flush、metrics；不在每 token 写 |
| Redis circuit 恢复不及时 | 低/中 | 短 TTL half-open probe；readiness 显示 degraded |
| 中央 migration 在多 worker 竞争 | 中/高 | migration lock + 启动前 CLI；应用启动只 verify |
| 文件流式/异步索引改变 API 时序 | 中/中 | 首批 flag off；返回兼容 status；前端契约测试 |
| strict startup 阻断本地开发 | 高/低 | debug 环境允许显式关闭；`.env.example` 给出无秘密占位说明 |
| 错误脱敏降低排障信息 | 中/中 | trace id 关联结构化日志；服务端保留完整异常但做 secret redaction |
| CI 时间显著增长 | 中/低 | smoke/PR/full/manual 分层；BGE/真实 live 不进普通 CI |

## 16. 回滚方案

| 工作包 | 回滚路径 | 不允许的回滚 |
|---|---|---|
| API auth | 回退 role enforcement，保留“必须认证” | 不得恢复匿名 memory/HITL |
| Memory schema | 应用忽略新增列；保留迁移数据 | 不得删除 owner/visibility 列或覆盖 provenance |
| RAG v2 | 切回旧 collection 仅限单用户隔离环境；保留 v2 | 预生产不得关闭 scope 后继续提供私有文件检索 |
| Checkpoint | 关闭 serialized-write flag，保持 conservative replay | 不得默认打开 aggressive replay |
| Redis key v2 | dual-reader 读 legacy，继续写 v2 | 不得用用户 session pattern 做批量删除 |
| Readiness | `/health` 保持兼容；暂时降低非关键依赖为 degraded | 不得把缺失 LLM 标成 ready |
| Error safety | 回退展示文案但保留稳定 error code/redaction | 不得向用户返回 raw exception/provider body |
| SQLite migration | 回滚应用，保留向前兼容新增表/列 | 无单独备份批准不得 destructive down |
| Docker/CI | 回退构建提交，不影响运行数据卷 | 不得恢复依赖安装失败后继续构建的 fallback |
| N4 guard | 关闭 guard 回旧路径仅用于本地诊断 | 预生产不得在已知注入题失败时长期关闭 |

出现以下任一条件应停止 rollout 并回滚到上一稳定开关：

- 跨用户 canary 被召回；
- conversation/file/checkpoint owner 隔离失败；
- checkpoint completed 被旧 step 覆盖；
- SQLite migration 校验失败或行数异常；
- readiness 与真实依赖状态不一致；
- SSE complete rate 明显下降；
- full 中出现新的 N1/N3/M1 must-pass 失败。

## 17. 进度、偏差与证据记录

实施开始后必须创建 progress 文档，并逐工作包记录：

- 实际提交 hash 与文件；
- 相对本计划的材料偏差、原因、批准人；
- config 默认和部署覆盖值；
- migration 前后 schema/row/entity count；
- 测试命令、passed/failed/skipped 数；
- selected/full run id、P50/P95、失败原因；
- 浏览器/API/Redis/Milvus 隔离证据；
- 回滚演练结果；
- 剩余风险和后续清理日期。

完成后更新 `AGENTS.md`：本计划状态改为“已实现并验证”，链接 progress；若只完成部分，则必须标注完成到哪个 WP，不得写成整体完成。

## 18. 推荐批准结论

建议批准以下范围作为第一批：

1. **立即实施 WP-0 至 WP-5**：API、memory/HITL、RAG tenant、checkpoint/Redis、readiness/error；
2. **DG-1 采用 system/project/user 三层 scope**；
3. **DG-2 采用 operator/curator/admin，分两阶段启用**；
4. **DG-3 使用新建 `biz_v2` + 保留旧 `biz`，禁止原地 drop**；
5. **DG-4 安全默认收紧，评测钩子与 aggressive replay 默认关闭**；
6. WP-6 文件异步 job 只先落 migration/流式上传，队列化索引可拆第二批；
7. WP-8 在前述阻断项验证后执行，避免用 N4/S1 功能修复掩盖基础租户风险。

未经上述决策批准，不进入业务代码实现。
