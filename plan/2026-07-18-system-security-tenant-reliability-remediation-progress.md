# 系统安全、租户隔离与可靠性修复进度

> 主计划：[`2026-07-18-system-security-tenant-reliability-remediation.md`](./2026-07-18-system-security-tenant-reliability-remediation.md)
> 状态：实施中（WP-1/2/4 已实现，WP-3 scope、WP-6 Phase 2、WP-7 Phase 1 已实现，WP-5 部分实现；live migration/CI 待验证）
> 开始日期：2026-07-18

## 1. 已批准决策

- DG-1：采用 `system / project / user` 三层数据可见性。
- DG-2：采用 `operator / curator / admin`，分阶段启用角色门禁。
- DG-3：新建 Milvus `biz_v2`，保留旧 `biz`，禁止自动 drop。
- DG-4：普通流量默认禁用 eval hooks 和请求级 aggressive checkpoint replay；收紧非 debug readiness。
- 保持 L3 Conditional、只读工具、`CHANGE_SOURCE_POLICY=unavailable`、`LONG_TERM_MEMORY_AUTO_DISTILL=false`、HITL `executed=false` 和现有 SSE `type` 语义。

## 2. WP-0 基线

### 2.1 代码与工作区

- 分支：`codex/publish-current-worktree`（跟踪 `origin/codex/publish-current-worktree`）。
- 基线提交：`85875e1`。
- 开始实施时已有修改：`AGENTS.md`、`CODEX.md`、`README.md`、`frontend/src/App.tsx`、`frontend/src/components/__tests__/conversationHistory.test.tsx`，以及未跟踪的 `.agents/` 和主计划文件。
- 所有既有修改均按用户工作保留；本修复不回退或覆盖未归属的前端、README 和 `.agents/` 改动。

### 2.2 运行环境

- 项目约束：Python `>=3.11,<3.14`。
- 验收解释器：`.venv/bin/python`，Python `3.13.14`。
- pytest：`9.0.2`。
- 系统 Python 3.14 不作为验收环境。

### 2.3 数据资产（仅元数据）

| 资产 | 路径/名称 | 基线状态 |
|---|---|---|
| 长期记忆 SQLite | `volumes/long_term_memory.db` | 11,042,816 bytes；schema/行数待记录 |
| checkpoint SQLite | `volumes/checkpoints.db` | 4,096 bytes；schema/行数待记录 |
| diagnosis SQLite | `data/diagnosis_memory.sqlite3` | 32,768 bytes；schema/行数待记录 |
| Milvus 知识 collection | `biz` | 既有记录为 21 entities；本轮需从运行环境复核 |
| Redis | namespace `super_biz_agent` | 可达性、key 数与 TTL 待记录；不记录 key 内容 |

本地数据库路径均受 `.gitignore` 保护。迁移前备份、可读性验证、Milvus 和 Redis 运行态复核尚未完成。

### 2.4 基线验证

| 检查 | 结果 |
|---|---|
| `python -m compileall -q app` | 前期审查已通过；本轮待复跑 |
| 后端 smoke | 待执行 |
| 前端 tests/build | 待执行；需保留现有前端改动 |
| harness 单文件行数 | 待执行 |
| secret check | 待执行；只记录规则结果，不记录 secret |

## 3. 工作包状态

| 工作包 | 状态 | 说明 |
|---|---|---|
| WP-0 | 已完成（部分外部依赖待复核） | 三个 SQLite 均 `quick_check=ok`，备份可读；Python 3.13、前端 76 tests/build、secret check、compileall 通过；Milvus/Docker 运行态可见但应用侧连接受当前沙箱网络限制，Redis ping 超时 |
| WP-1 | 已实现并验证 | immutable RequestContext；稳定 owner + legacy storage key 兼容；业务 API async 认证矩阵 6/6；输入 extra forbid/长度上限；eval hooks 与 aggressive replay request override 默认拒绝；角色门禁具备分阶段开关 |
| WP-2 | 已实现并验证（旧库迁移待单独 dry-run） | experience owner/visibility/approval 字段与兼容 schema；user draft 隔离、原子 confirm/reject；memory 项目固定；HITL 会话 action owner 校验；27 项定向回归通过 |
| WP-3 | scope 实现完成；live dry-run 待健康环境 | RequestContext task binding；system/project/user 标量过滤；stable + legacy owner 迁移兼容；上传 user scope、系统文档 system scope；只读默认 migration CLI；未创建/切换/drop collection |
| WP-4 | 已实现并验证（stateful 全套进程退出待环境复核） | Redis scope hash、无 SCAN 删除、单调 step、调度快照深拷贝；checkpoint/Redis 定向断言通过；stateful 测试断言通过但当前 Python 3.13/anyio 测试进程在后台线程退出阶段超时 |
| WP-5 | 部分实现并验证 | Redis lifespan 接入；`/health/live` 与 `/health/readiness` 分离；默认 secret/admin、LLM/Milvus readiness 门禁；assistant/memory/file 错误响应脱敏 |
| WP-6 | Phase 2 runner/enforcement 完成；live up/文件治理待后续 | migration v1/v2 + checksum、legacy columns、transactional `status/verify/up`；`DB_SCHEMA_ENFORCEMENT_ENABLED=false` 默认关闭，未执行 live up |
| WP-7 | Phase 1 完成；CI/生产编排扩展待后续 | Makefile `/api/files` + bearer fail-fast；check-all 改为只读；Dockerfile fail-closed install/healthcheck；Compose data volume、Redis healthcheck、backend readiness |
| WP-8 | 未开始 | N4/S1 与全量回归 |

## 4. 偏差、风险与回滚

- 材料偏差：为保持现有 SQLite 数据可读，principal 的新 64 字符稳定 owner 与旧 8 字符 storage owner 并存；WP-6 完成已批准的显式迁移后再切 storage 主键。
- 当前 Python 3.13 环境的 Starlette `TestClient`（同步线程池路径）对最小应用也会挂起；新增 API 矩阵使用可执行的 async ASGITransport。CI/支持环境需复核 TestClient 版本组合。
- Milvus 容器显示 healthy，但当前沙箱内 pymilvus 连接不可达；不得在此状态执行 `biz_v2` migration。
- Redis 默认 URL 已移除代码内密码；本地运行需通过 `REDIS_URL` 注入认证。
- stateful context 相关 pytest 用例的业务断言可通过，但某些用例完成后 pytest 进程未在 20 秒内退出；需在 CI 支持环境复核后台 MCP/logging 线程生命周期。
- 当前工作树非干净状态，实施时必须用文件级差异审查区分本轮修改与既有用户修改。
- 在 SQLite 备份和 Milvus dry-run 证据完成前，不执行 schema migration、collection 切换或 rebuild。
- 回滚基点为提交 `85875e1` 加当前用户工作树；旧 `biz` 始终保留，不能由脚本自动删除。

## 5. 验证证据日志

### 2026-07-18 WP-1～WP-5 增量验证

- `PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_api_authorization_matrix.py tests/test_memory_tenant_scope.py tests/test_m3_w10_distill.py tests/test_m3_w9_hitl_metrics.py tests/test_harness_checkpoint.py tests/test_redis_key_scope.py tests/test_readiness_security.py tests/test_harness_stateful_checkpoint_resume.py -q -rA --tb=line --no-cov`：**47 passed**。
- `PYTHONPATH=. .venv/bin/python -m compileall -q app`：通过。
- 受影响模块 ruff check：通过；`git diff --check`：通过。
- 前端 WP-0 基线：`npm test -- --run` **76 passed**；`npm run build` 通过。
- `scripts/check_env_secrets.py --strict`：`OK no issues detected`。
- Docker 只读状态：Milvus standalone/etcd/minio/attu healthy/running；当前沙箱内 pymilvus 连接仍不可达，未执行迁移。
- SQLite：`long_term_memory.db` 8 tables/11,042,816 bytes、`checkpoints.db` 2 tables/4,096 bytes、`diagnosis_memory.sqlite3` 3 tables/32,768 bytes；备份 `volumes/backups/2026-07-18/` 三文件均可读，`quick_check=ok`。

后续按工作包追加 migration 前后 metadata、回滚演练、RAG/Redis live 隔离证据和 full eval。不得在本文记录真实 token、密码、业务内容或 pilot pass marker。

### 2026-07-18 WP-3 scope implementation

- `PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_rag_tenant_scope.py tests/test_api_authorization_matrix.py tests/test_memory_tenant_scope.py tests/test_harness_checkpoint.py tests/test_redis_key_scope.py tests/test_readiness_security.py tests/test_harness_stateful_checkpoint_resume.py tests/test_file_storage_service.py -q --tb=short --no-cov`：**45 passed**。
- `PYTHONPATH=. .venv/bin/python scripts/migrate_rag_scope.py --dry-run --target biz_v2`：返回 `status=blocked`、`mutated=false`；当前沙箱无法连接 `localhost:19530`，未创建或修改 collection。
- 受影响文件 Ruff、`compileall`、`git diff --check`：通过。
- 迁移期偏差：上传文件仍以 legacy storage owner 保存；RAG filter 同时接受同一认证 principal 的 stable owner 与 legacy storage owner，待 WP-6 数据迁移后收敛。

### 2026-07-18 WP-6 migration runner phase 2

- 新增 `app/services/database_migration_service.py` 与 `scripts/migrate_database.py`，覆盖长期记忆 SQLite 的 8 张业务表、索引和 `schema_migrations` 版本记录；v2 负责已知旧列补齐与 checksum 校验。
- `tests/test_database_migrations.py`：空库只读、backup 门禁/幂等 up、事务失败回滚、legacy fixture、service enforcement **6 passed**。
- 现有 `volumes/long_term_memory.db` 只读 `status`：`current_version=0`、`pending=[1,2]`、`missing=[]`；`verify` 返回未验证并以退出码 1 结束，未修改数据库。
- 新增 `DB_SCHEMA_ENFORCEMENT_ENABLED=false`；开启后 conversation/context/file/preference/experience/service services 只验证 migration version，不执行首次请求 DDL。
- WP-6 偏差：未执行真实库 `up`；文件流式上传、并发索引和 checkpoint/diagnosis 独立库迁移留后续工作包。触及的旧服务已有 Ruff 风格问题，本次未扩大范围修复。
- 使用 `/tmp` 副本和对应备份副本完成一次接近真实数据的迁移演练：version `0 -> 2`，`schema_ok=true`、checksum 正确；第二次 `up` 幂等通过。副本元数据为 conversations=60、conversation_turns=52、experiences=440、uploaded_files=4、services=1、service_baselines=1、service_relations=0、user_preferences=0；真实库未写入。

### 2026-07-18 WP-7 deployment/CI contracts phase 1

- Makefile 上传路径改为 `/api/files`，`make upload/test-upload` 在无
  `AUTH_TOKEN` 时 fail-fast；`check-all` 改为 `format-check`，不再先修改工作树。
- `STATIC_SERVE_ENABLED=false` 默认关闭；启用时缺少 static 目录会明确失败。
- Dockerfile 依赖安装去掉 fallback，增加 backend liveness healthcheck；Compose 增加 backend volume、Redis loopback binding/healthcheck、backend readiness healthcheck 和持久化路径。
- `docker compose ... config`（含 `--profile full`）通过；backend 镜像实际构建成功；无 token 的 `make upload` 在发起网络请求前失败。
- CI 增加 Python 3.12/3.13、授权/RAG/migration smoke、静态 critical-error 检查、secret contract 和 frontend test/build job；未执行真实 provider/数据库调用。
