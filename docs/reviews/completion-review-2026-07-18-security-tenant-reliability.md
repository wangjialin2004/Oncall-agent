# Completion Review Report: 系统安全、租户隔离与可靠性修复

- Review date: 2026-07-18
- Review scope: 2026-07-18 交接所覆盖的 WP-0～WP-8，重点检查 API 鉴权、租户 scope、memory/HITL ownership、checkpoint/Redis、readiness、SQLite migration、部署/CI 契约及 L3 产品边界
- Source material: [`docs/pilot/handoff-2026-07-18-security-tenant-reliability.md`](../pilot/handoff-2026-07-18-security-tenant-reliability.md)、主计划及 WP-3/WP-6/WP-7 计划、当前提交 `84278d5`/`e718f58`
- Verification commands: 定向后端回归、CI smoke 测试、compileall、critical Ruff、secret contract、前端 test/build、Compose config、SQLite `status/verify`

## Overall Conclusion

代码交付具备条件接手状态：鉴权和租户边界、memory/HITL ownership、checkpoint key/单调写、RAG scope 过滤、SQLite migration runner、Docker/Compose/CI 声明均有实现，当前可验证回归通过。定向后端回归 `61 passed`，按 CI 工作流复跑的 smoke 集合 `77 passed`，前端 `83 passed` 且 build 通过；compileall、critical Ruff、`git diff --check` 和 Compose 配置检查通过。

项目仍**不具备无条件发布或将 L3 Conditional 改判为 Go 的证据**。本次批准后已完成 Milvus `biz_v2` schema/apply、真实 SQLite `up`、metrics/readiness/health 代码收口；但知识库当前 0 entities、enforcement rollout、远端 CI 和 WP-8 N4/S1/full eval 尚未完成。产品边界必须继续保持“只读预生产白名单值班副驾”。

## Findings

### P1 - `biz_v2` live tenant migration 未闭环（已收口）

- Evidence: 交接记录 `scripts/migrate_rag_scope.py --dry-run --target biz_v2` 返回 `status=blocked`、`mutated=false`；当前真实 collection 仍未创建、切换或重建。
- Impact: 代码层 scope filter 已 fail closed，但无法证明现有 `biz` 数据已完成 scope 分类、计数一致性和跨租户检索隔离；生产切换后可能出现历史知识不可见或迁移遗漏。
- Recommendation: 在应用可达且获批的健康环境先执行只读 dry-run，输出 schema/entity/scope 缺口；对无可信 owner 的行形成人工清单；再创建 `biz_v2`、复制有 scope 行、做 count/RAG A-B 验证后切换，保留旧 `biz`，禁止 drop。
- Verification: 记录 source/target schema、entity count、scope 缺口、抽样跨租户查询结果和 `mutated` 变化；失败时确认旧 collection 未被修改。

### P1 - 真实 SQLite migration 尚未执行，enforcement 仍关闭（已收口 migration）

- Evidence: `scripts/migrate_database.py status` 当前为 `current_version=0`、`pending=[1,2]`、`schema_ok=false`；`verify` 按设计返回未验证并退出码 1。`DB_SCHEMA_ENFORCEMENT_ENABLED` 仍为默认 `false`。
- Impact: 线上仍可能由旧服务在首次请求路径补建/变更 schema，无法取得真实库版本、checksum、行数和回滚证据；打开 enforcement 会直接拒绝未迁移实例。
- Recommendation: 在维护窗口确认 dated backup、磁盘空间和表/行数快照后执行 `up`，再执行 `verify` 与业务 smoke；证据完整后才逐步打开 enforcement。不得执行 down migration 或无备份写入。
- Verification: 真实库记录 `0 -> 2`、checksum、表/行数、第二次 `up` 幂等结果及失败回滚结果。

### P1 - `/metrics` 访问保护未实现（已修复）

- Evidence: [`app/core/metrics.py`](../../app/core/metrics.py) 直接 `app.mount("/metrics", make_asgi_app())`，未读取 `METRICS_ACCESS_MODE`、未配置 bearer/internal-network guard；主计划明确要求预生产只允许内部网络或独立 bearer。
- Impact: 未认证调用者可读取请求、agent、tool、token usage 等运行指标，造成运维信息泄露，并未满足 WP-5 退出条件。
- Recommendation: 实现默认 fail-closed 的 metrics access mode（内部网络或独立 bearer 二选一），在 Compose/Prometheus 中同步配置，并为 401/403/允许抓取分别加测试。
- Verification: 未带凭据的 `/metrics` 请求必须拒绝；合法 Prometheus 请求成功；响应不包含高基数或敏感原文。

### P2 - readiness 未反映 Redis/MCP 能力状态（已修复）

- Evidence: [`app/api/health.py`](../../app/api/health.py) 的 `readiness_issues` 只检查 Milvus、LLM 和 auth 默认值；没有 Redis degraded/circuit 状态，也没有按 `HARNESS_MCP_ENABLED` 检查 MCP。交接已将 Redis circuit、MCP capability readiness 列为 WP-5 未完成项。
- Impact: 部署平台可能在 stateful/checkpoint 或启用 MCP 的能力不可用时仍判定 ready，导致流量进入降级路径并放大时延/丢失恢复能力。
- Recommendation: 按启用能力报告 Redis `ready/degraded`、MCP `required/optional`，并将 checkpoint flush/circuit 状态纳入低基数 metrics；保持 `/health/live` 只表示进程存活。
- Verification: 分别注入 Redis 不可用、MCP 开关开/关、LLM/Milvus 不可用场景，断言 readiness 状态和 issue 列表符合配置。

### P2 - 公开 health 响应包含内部拓扑和异常原文（已修复）

- Evidence: [`app/api/health.py`](../../app/api/health.py) 在无认证路由 `/health` 中返回 MCP URL、RAG collection/config 字段；Milvus 异常分支将 `str(e)` 放入响应 `message`。
- Impact: 未认证调用者可获得内部主机/端口和 provider 错误细节，与本棒“用户响应不返回内部异常原文、日志最小化”的安全目标不一致。
- Recommendation: 将公开 health 响应收敛为状态码和低敏感摘要；详细 URL/异常仅写结构化服务端日志或受保护的运维端点。
- Verification: 模拟连接异常和自定义 MCP URL，公开响应不得包含异常文本、主机名、端口或 collection 细节。

## Completion Matrix

| Item | Status | Notes |
| --- | --- | --- |
| WP-0 基线/备份门禁 | Partial | 本地静态、前端和 SQLite backup 证据可复核；Milvus/Redis 应用侧可达性仍受环境限制 |
| WP-1 API identity/auth/input guards | Complete | 定向授权矩阵通过；默认 eval/replay override 拒绝 |
| WP-2 memory/HITL ownership | Complete | owner/project/status 原子门禁和 audit 通过；旧库正式迁移仍依赖 WP-6 |
| WP-3 RAG scope/schema | Partial | live `biz_v2` schema/apply 已完成，但 source/target 均 0 entities，知识库重建待后续 |
| WP-4 checkpoint/Redis correctness | Partial | scope hash、单调写、无 SCAN 删除和 stateful resume 断言通过；真实 Redis/circuit 生命周期未证实 |
| WP-5 readiness/error/lifecycle | Complete | metrics 保护、Redis/MCP readiness、公开 health 脱敏已实现并有回归 |
| WP-6 SQLite migration/enforcement | Partial | 真实库已 `0 -> 2` 并 verify/幂等通过；enforcement rollout 仍待部署 |
| WP-7 deployment/CI contracts | Partial | Makefile/Docker/Compose 声明和本地 config/build 通过；远端 workflow 未运行 |
| WP-8 N4/S1/full eval | Missing | 交接明确未开始；L3 继续 Conditional |

## Test And Verification Notes

- `pytest` 定向集合：`61 passed`。
- 按 `.github/workflows/ci-smoke.yml` 的测试列表复跑：`77 passed`；当前 workflow 已包含新增 RAG/migration 测试，交接中的“49 passed”是历史计数，应以当前命令为准。
- 前端：`npm test -- --run` 为 `83 passed`；`npm run build` 通过。Vite 仅报告一个已有的 dynamic import 分块提示。
- 静态：`python -m compileall -q app scripts`、`ruff --select E9,F63,F7,F82`、`git diff --check` 通过。
- Secret contract：使用 CI dummy env 通过；使用空 env 文件会按预期拒绝缺少 `LLM_API_KEY/AUTH_TOKEN_SECRET`，不能把空 env 结果当作部署通过证据。
- SQLite：真实库只读 `status/verify` 未通过 schema gate，未写入；副本迁移证据来自交接文档，需在真实维护窗口重做并留存。
- 外部验证未执行：真实 Milvus migration、真实 Redis health/circuit、远端 GitHub Actions、完整 Compose 栈启动和 WP-8 full eval。

## Next Steps

1. 在获批健康环境完成 Milvus `biz_v2` dry-run、分类、复制和 A-B 隔离证据；旧 `biz` 保留。
2. 在维护窗口完成真实 SQLite `up`/`verify`，保留 backup、checksum、行数和回滚证据，再评估打开 enforcement。
3. 收口 WP-5：metrics 访问策略、Redis degraded/circuit、MCP capability readiness 和健康响应脱敏。
4. 运行远端 CI 与 full Compose healthcheck；随后开始 WP-8 N4/S1/full eval，并更新 L3 出口评审。

## Follow-up Hardening

2026-07-18 针对本报告的代码级问题已完成修复：

- `/metrics` 默认改为内网访问，支持独立 bearer，并限制 public 模式只能在 debug + 显式开关下启用。
- readiness 增加 Redis 生命周期状态和按开关启用的 MCP 状态；MCP 未启用时不再预热或探测内部 URL。
- 公开 health 只返回低敏感状态/issue code；详细诊断改为默认关闭的 admin 端点。
- RAG legacy dry-run 增加缺失 scope 字段、JSON metadata 和 unresolved row 报告，仍保持只读。

用户批准后已完成两项 live 操作：Milvus `biz_v2` 已创建并切换默认（源/目标均为 0 entities，旧 `biz` 保留）；真实长期记忆 SQLite 已从 version 0 迁移到 version 2，backup 刷新、`quick_check`、checksum、verify 和幂等重跑均通过。当前剩余阻塞转为知识库重建、enforcement 部署 rollout、远端 CI 和 WP-8 full eval。
