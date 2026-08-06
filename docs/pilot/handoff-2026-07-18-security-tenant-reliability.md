# OnCall Agent 交接文档 — 系统安全、租户隔离与可靠性修复

> **交接日期**：2026-07-18
> **工作树**：`codex/publish-current-worktree`
> **基线提交**：`85875e1`
> **实现提交**：`e718f58`（已推送 `origin/codex/publish-current-worktree`）
> **本棒主题**：API 鉴权、租户 scope、memory/HITL ownership、checkpoint/Redis 正确性、readiness、SQLite migration、deployment contracts
> **产品状态**：**L3 Conditional 不变**；仍是只读预生产白名单值班副驾，非无人值守生产主路径
> **目标读者**：接手 WP-3 live、WP-5 收口、WP-6/7 后续和 WP-8 eval 的工程同学
> **前序产品主交接**：[M3 L3 Conditional](./handoff-2026-07-15-m3-l3-conditional.md)
> **本棒计划**：[完整修复计划](../../plan/2026-07-18-system-security-tenant-reliability-remediation.md)
> **本棒进度**：[实施进度与证据](../../plan/2026-07-18-system-security-tenant-reliability-remediation-progress.md)

---

## 0. 30 秒结论

| 项 | 状态 |
|---|---|
| WP-0 基线/备份门禁 | ✅ 完成；外部服务复核仍受当前执行环境限制 |
| WP-1 RequestContext/API auth | ✅ 实现并验证 |
| WP-2 memory/HITL tenant ownership | ✅ 实现并验证；长期记忆库已完成 version 2 migration |
| WP-3 Milvus `biz_v2` | ✅ live schema/apply 已完成；`biz_v2` 已创建并切换默认，`biz` 保留；当前两者 0 entities |
| WP-4 checkpoint/Redis | ✅ 关键修复实现并验证；stateful pytest 进程退出需在 CI 环境复核 |
| WP-5 readiness/error/lifecycle | ✅ 代码收口；metrics 访问、Redis/MCP readiness、health 脱敏已完成 |
| WP-6 | ✅ 长期记忆真实库已 `up` 到 version 2 并验证；文件治理/其他库未收口 |
| WP-7 | 🟡 deployment/CI contracts Phase 1 已实现；远端 CI 尚未运行 |
| WP-8 | ⬜ 未开始；N4/S1/full eval 待后续 |
| 定向回归 | ✅ **69 passed**；CI smoke 扩展 **88 passed** |
| 前端验证 | ✅ **83 tests passed**，build 通过 |
| 自动处置/HITL | ❌ 无自动处置；`executed=false` 保持不变 |
| `CHANGE_SOURCE_POLICY` / `AUTO_DISTILL` | `unavailable` / `false` 保持不变 |

**接手人一句话**：安全/租户/runtime hardening 已落地；`biz_v2` 和长期记忆 SQLite 已完成本地健康环境收口，旧 `biz` 保留，下一棒收口远端 CI/WP-8 与知识库数据重建，产品仍是 L3 Conditional。

## 1. 必读文档

| 优先级 | 文档 | 用途 |
|---|---|---|
| **P0** | 本文 | 当前工程交接边界、验证、阻塞和下一棒 |
| **P0** | [完整修复计划](../../plan/2026-07-18-system-security-tenant-reliability-remediation.md) | DG-1～DG-4、WP-0～WP-8、迁移/回滚/验收标准 |
| **P0** | [实施进度](../../plan/2026-07-18-system-security-tenant-reliability-remediation-progress.md) | 真实验证命令、数据备份和偏差记录 |
| **P0** | [L3 Conditional 主交接](./handoff-2026-07-15-m3-l3-conditional.md) | 产品出口、评测基线、H3 豁免和只读红线 |
| **P0** | [AGENTS.md](../../AGENTS.md) | 计划治理、先计划后编码、安全边界 |
| P1 | [预生产 Runbook](./preprod-runbook.md) | 白名单、启动和回滚流程 |

## 2. 已批准的决策与不可变红线

- 数据可见性：`system / project / user`。
- 角色：`operator / curator / admin`，角色门禁分阶段启用；未配置角色默认为 `operator`。
- Milvus：新建 `biz_v2`，旧 `biz` 至少保留一个观察窗口，禁止自动 drop。
- 普通流量默认关闭 eval hooks 和请求级 aggressive checkpoint replay。
- 只读工具和只读产品红线不变；不得新增 restart/rollback/scale executor。
- `CHANGE_SOURCE_POLICY=unavailable`，不得伪造变更记录。
- `LONG_TERM_MEMORY_AUTO_DISTILL=false`；蒸馏只能走 draft/confirm 治理。
- HITL 只记录审计，`executed` 永远为 `false`。
- SSE 既有 `type` 语义不改；新增字段必须向后兼容。
- 不在代码、文档、日志提交真实 token、密码、pilot pass marker 或附件正文。

## 3. 本棒已实现

### 3.1 API identity 与输入边界

- 新增 [`app/core/request_context.py`](../../app/core/request_context.py)：不可变 `RequestContext`，包含 stable owner、legacy storage owner、project、role、session、trace。
- [`session_scope_service.py`](../../app/services/session_scope_service.py) 生成 64 字符 stable owner，同时保留旧 8 字符 storage key，避免 migration 前历史数据失联。
- [`authorization_service.py`](../../app/services/authorization_service.py) 集中处理 curator/admin policy；`AUTH_ROLE_ENFORCEMENT_ENABLED` 控制阶段切换。
- 业务认证依赖改为 async，避免当前 Python 3.13/anyio 环境同步依赖线程池挂起。
- `ChatRequest`、memory request 增加长度/数量/范围约束，`extra="forbid"` 阻止 body 伪造 owner/project/role。
- `simulate`、`prefer_parallel` 在 `HARNESS_EVAL_HOOKS_ENABLED=false` 时返回稳定 403。
- `CheckpointReplay=true` 在 `HARNESS_CHECKPOINT_REQUEST_OVERRIDE_ENABLED=false` 时返回稳定 403。

### 3.2 Memory 与 HITL ownership

- `experience_memories` 兼容增加 `owner_key`、`visibility`、`approved_by`、`approved_at` 字段。
- 新 distill draft 默认 `visibility=user`；curated/approved 记录转为 `project`。
- list/get 固定当前 project；user draft 只返回给 owner；旧 pending 且无 owner 的记录 fail closed。
- confirm/reject 使用带 owner/project/status 的 SQL 原子更新，避免先读后写 TOCTOU。
- manual experience、service/baseline、seed import、rebuild index 已挂接 curator policy；阶段开关关闭时保留 pilot 兼容行为。
- HITL 确认必须命中当前 owner 会话中持久化的 suggested action id，审计增加 owner/project/trace，不保存 bearer token。

### 3.3 Checkpoint、Redis 与 readiness

- checkpoint Redis key 使用 `sha256(owner + session)` scope hash，原始输入不出现在 key pattern。
- delete 改为精确删除已知 meta/messages/step keys，不再按用户输入做 `SCAN match`。
- checkpoint step 写入单调化，旧 step 不能覆盖新 step；调度点深拷贝 mutable state/messages。
- Stateful resume 无论 messages 是否为空都恢复 state fields；messages 仅决定 legacy replay。
- FastAPI lifespan 接入 `redis_lifespan`，启动/关闭会 ping/close Redis。
- 新增 `/health/live`（只看进程）和 `/health/readiness`（依赖及安全默认）；旧 `/health` 响应保留。
- assistant/memory/file 用户响应不返回 provider 原文、路径、stack 或异常文本，服务端仍记录结构化日志。

### 3.4 RAG tenant scope 与 `biz_v2` migration

- `scope_type/scope_id` 写入 Milvus schema；system/project/user 过滤只接受认证 principal/config 派生值。
- 缺少 `RequestContext` 时 fail closed；上传文件兼容 stable owner 与 legacy storage owner 过渡期读取。
- `scripts/migrate_rag_scope.py` 默认只读 dry-run，旧 `biz` 永不自动 drop；当前环境连接阻塞，未创建/切换 collection。

### 3.5 SQLite migration 与 deployment contracts

- `scripts/migrate_database.py status/verify/up` + `schema_migrations` v1/v2、checksum、backup/disk gate、事务 rollback。
- `DB_SCHEMA_ENFORCEMENT_ENABLED=false` 默认关闭；打开后 domain services 只验证版本，不执行首次请求 DDL。
- Makefile 上传改为认证 `/api/files`；`check-all` 只读；Dockerfile/Compose 已加入 fail-closed install、持久卷和 healthcheck。
- CI 声明 Python 3.12/3.13、授权/RAG/migration smoke、critical static checks、secret contract、frontend test/build。

## 4. 架构心智模型

```text
Bearer token
  -> AuthenticatedPrincipal
       stable owner / legacy storage owner / project / role
  -> RequestContext(owner, project, role, session, trace)
       |-> API authorization
       |-> conversation/file/checkpoint owner storage
       |-> memory user/project visibility
       |-> HITL action ownership
       |-> future RAG scope filter

POST /api/assistant (SSE)
  -> auth + RequestContext + request guard
  -> attachment/conversation owner scope
  -> HarnessService.stream(owner storage key, session)
  -> checkpoint/context state
  -> read-only tools / verify / complete / HITL audit
```

**后续 RAG 必须接入 RequestContext**：不得继续从全局 handler 检索；检索 scope 应为 `system OR current project OR current owner`，过滤值只能来自 principal/config。

## 5. 验证证据

### 5.1 已通过

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_api_authorization_matrix.py \
  tests/test_memory_tenant_scope.py \
  tests/test_m3_w10_distill.py \
  tests/test_m3_w9_hitl_metrics.py \
  tests/test_harness_checkpoint.py \
  tests/test_redis_key_scope.py \
  tests/test_readiness_security.py \
  tests/test_harness_stateful_checkpoint_resume.py \
  -q -rA --tb=line --no-cov
# 70 passed（本棒最终定向集合）

PYTHONPATH=. .venv/bin/python -m compileall -q app
git diff --check
PYTHONPATH=. .venv/bin/ruff check \
  app/api/assistant.py app/api/auth.py app/api/file.py app/api/health.py \
  app/api/hitl.py app/api/memory.py app/core/request_context.py \
  app/services/auth_service.py app/services/authorization_service.py \
  app/services/session_scope_service.py tests/test_api_authorization_matrix.py
```

本棒最终验证：前端 `npm test -- --run` **83 passed**、`npm run build` 通过；ci-smoke **49 passed**；
`scripts/check_env_secrets.py --strict --env-file /dev/null`（CI 占位配置）通过；
critical Ruff（E9/F63/F7/F82）、compileall、`git diff --check` 通过。

### 5.2 迁移与容器证据

- 真实 `volumes/long_term_memory.db` 已执行 `up`：`current_version=2`、`pending=[]`、`schema_ok=true`；第二次 `up` 幂等通过，`quick_check=ok`。
- `/tmp` 数据库副本执行 `up`：version `0 -> 2`、checksum/schema 正确；第二次 `up` 幂等通过；副本元数据 conversations=60、turns=52、experiences=440、uploaded_files=4、services=1、service_baselines=1。
- Milvus live 复核可达：`biz` schema 为旧无 scope、entities=0；显式 apply 创建 `biz_v2`，字段含 `scope_type/scope_id`，entities=0，旧 `biz` 未修改；默认配置已切换为 `biz_v2`。
- `docker compose -f deploy/compose/docker-compose.pilot.yml --profile full config` 通过；backend 镜像实际构建成功；未启动完整 Compose 栈。

### 5.3 提交与工作树

- 实现提交：`e718f58 feat: harden tenant isolation and deployment contracts`。
- 已推送：`origin/codex/publish-current-worktree`。
- 本文档随后作为独立交接文档提交；不得把真实 `.env`、数据库、日志或 token 加入提交。

### 5.4 基线与外部依赖

- Python 验收环境：`.venv/bin/python` 3.13.14；项目约束 `<3.14`。
- SQLite：长期记忆 8 tables/230 conversations/426 experiences/4 uploaded files；checkpoint 2 tables/15 checkpoints/48 writes；diagnosis 3 tables。
- 备份：`volumes/backups/2026-07-18/`，三个 SQLite 均 `quick_check=ok`。
- Docker 只读状态：Milvus standalone、etcd、minio、attu 显示 running/healthy。
- 当前 live 复核：Milvus `biz_v2` 已创建；未执行知识库 rebuild（两 collection 当前均为 0 entities）；Redis live ping 仍需在应用 lifespan/Compose 中复核。

## 6. 未完成项与阻塞

### P0：下一棒必须完成

1. **知识库数据重建**：`biz`/`biz_v2` 当前均为 0 entities；需按既有 BGE 重建计划重新导入系统文档和已分类文件，完成 scope/A-B 验证。
2. **DB enforcement rollout**：真实库已 version 2；在应用部署环境设置 `DB_SCHEMA_ENFORCEMENT_ENABLED=true` 并跑业务 smoke。
3. **WP-5 剩余可靠性**：checkpoint coordinator bounded flush、Redis circuit-open、metrics 低基数指标仍待后续。

### P1：随后完成

- WP-6 流式上传、owner 并发限制、索引 job 资源治理。
- WP-7 运行远端 CI，并在不含真实 provider 的环境完成 full Compose 启动/healthcheck 验证。
- WP-8 N4 early-refuse、S1 provider/业务错误区分、full 23 回归；保持 L3 Conditional 话术。

### 当前环境阻塞

- Milvus 容器 healthy 不代表本执行沙箱可用；迁移前必须在可达环境重新记录 collection/schema/entity metadata。
- 真实 SQLite `up` 尚未执行；`DB_SCHEMA_ENFORCEMENT_ENABLED` 必须继续保持 `false`，直到迁移证据完成。
- 当前环境 Starlette `TestClient` 对最小应用的同步线程池路径也会挂起；新增 API 测试使用 async ASGITransport。CI 支持环境需复核测试依赖组合。
- 部分 stateful pytest 用例业务断言通过但进程退出阶段超时，需定位后台 MCP/logging 生命周期；不得把超时当作全套回归通过。

## 7. 接手操作顺序

### 第一步：只读复核

```bash
git status --short --branch
.venv/bin/python --version
PYTHONPATH=. .venv/bin/python -m compileall -q app
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_api_authorization_matrix.py tests/test_memory_tenant_scope.py \
  tests/test_harness_checkpoint.py tests/test_redis_key_scope.py \
  tests/test_readiness_security.py -q --tb=line --no-cov
```

### 第二步：Milvus dry-run（健康且获得授权后）

```bash
uv run python scripts/migrate_rag_scope.py --dry-run --target biz_v2
```

必须先输出 schema/entity/scope 缺口清单；没有 owner 的上传向量必须 fail closed 并进入人工清单。禁止 `drop_collection("biz")`。

### 第三步：SQLite migration dry-run / up（获得操作授权后）

- 确认 `volumes/backups/2026-07-18/` 可读。
- 运行 `scripts/migrate_database.py status/verify`，确认 backup 与磁盘空间。
- 在维护窗口执行 `scripts/migrate_database.py up`，记录 version/checksum/表行数；失败时确认事务 rollback。
- migration 在事务中执行，失败回滚；先扩展 schema，再切读写，最后才考虑旧兼容列。
- migration 前后记录表/行数和 schema version，不记录业务正文。

### 第四步：验证与后续变更

本棒已将工作树按用户要求集中提交并推送；后续建议按以下边界拆分新变更：

| 提交 | 范围 |
|---|---|
| C1 | authorization/tenant regression matrix |
| C2 | RequestContext/API policy/input guards |
| C3 | memory/HITL ownership |
| C4 | RAG scoped `biz_v2` + migration |
| C5 | checkpoint/Redis coordinator |
| C6 | readiness/error/observability |
| C7 | SQLite migration/file governance |
| C8 | deploy/CI contracts |
| C9 | N4/S1/full eval evidence |

实现提交已完成文件级审查；后续不得整树 `git add -A`，仍需区分用户既有 README、前端和 `.agents/` 修改。

## 8. 回滚与数据安全

- 应用回滚基点：`85875e1` 加用户既有工作树差异。
- 不执行 destructive `git reset`、SQLite down migration 或 Milvus old collection drop。
- `biz` 保留作为 v1 回退源；`biz_v2` 只有在 count/scope/RAG A-B 证据通过后才可切换配置。
- SQLite migration 失败回滚事务；备份保留在受 `.gitignore` 保护的 `volumes/backups/2026-07-18/`。
- Redis key v2 切换需 dual-read 兼容窗口，不能直接清空旧 namespace。
- 所有新开关必须是环境变量可关闭能力，不得通过关闭 scope 退回全局检索。

## 9. 交接检查清单

- [x] 已读本文、完整修复计划和 L3 Conditional 主交接。
- [x] 已确认产品仍为 L3 Conditional、只读、H3 豁免。
- [x] 已确认 `biz` 不会被自动 drop，且迁移前有 SQLite backup。
- [x] 已跑 API authorization、memory tenant、checkpoint/Redis、readiness、RAG scope、SQLite migration 定向测试。
- [x] 已在健康环境复核 Milvus metadata；`biz_v2` 已创建，未 drop `biz`。
- [x] 已为 WP-3、WP-6、WP-7 建立/更新正式计划，再开始 schema/部署契约变更。
- [x] 已按文件级差异审查提交工作树，保留用户 README/前端/`.agents/` 修改。
- [x] 已记录实现 commit、验证命令、migration 副本 metadata 和剩余风险。
- [x] 已完成 Milvus live dry-run/apply 和真实 SQLite `up`；知识库重建仍未执行。

## 10. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-18 | 完成本棒交接：WP-1/2 完成，WP-4 完成，WP-5 部分完成；47 项定向回归通过；WP-3/6/7/8 留给下一棒 |
| 2026-07-18 | 后续更新：WP-3 scope/schema/dry-run CLI、WP-6 SQLite runner/enforcement Phase 2、WP-7 deployment/CI contracts Phase 1 实现；59 项定向回归通过；SQLite `/tmp` 副本迁移 0→2 并幂等重跑通过；backend 镜像构建成功；live Milvus dry-run 连接阻塞且 `mutated=false` |
| 2026-07-18 | 完成交付提交 `e718f58` 并推送；最终定向后端 70、ci-smoke 49、前端 83；build/secret/critical static/Compose 验证通过；本文档作为下一棒入口 |
| 2026-07-18 | 用户批准后完成 runtime hardening；Milvus `biz_v2` 创建并切换默认（source/target 均 0 entities），长期记忆 SQLite 真实库 `0 -> 2`，backup 刷新并 quick_check/verify 通过；代码回归 69、CI smoke 扩展 88 |
