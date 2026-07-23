# Completion Review Report: 统一上下文仓储与持久化合并

- Review date: 2026-07-19
- Review scope: `plan/2026-07-19-unified-context-repository.md`、进度文档、统一 repository/projection/render/migration 实现、Harness/API 接线、相关测试与 live 只读数据状态
- Scope assumption: 本报告评审“统一上下文仓储计划”的完成度；上下文去重改动只检查与该计划的交互，不重新评审其全部功能
- Source material: 实施计划、progress、`docs/pilot/context-dual-path.md`、当前 dirty worktree、SQLite live 数据、focused tests
- Verification commands: 见“Test And Verification Notes”；未执行 live schema `up`、projection `apply`、Redis mutation 或 unified flag 切换

## Overall Conclusion

结论：**未完成，不可进入 canary，更不可切换默认值。** 架构方向、默认关闭开关、compact projection、SQLite happy-path 原子提交、只读 auditor 和 migration v3 骨架已经存在；新增实现专项测试 24 项通过，live SQLite 审计也保持只读且数据分类对账正确。

但核心目标“一个加载入口、双模式只做渲染策略”尚未接入实际 Harness，且存在启用后会清空旧 projection、提交失败后重新走 legacy 独立写、behind migration 虚假推进水位、schema/backup gate 不可靠、inflight resume 未实现等 P1 问题。由于 `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false`，这些问题当前未进入默认运行路径；该开关必须继续保持 false。

没有发现需要立即停止当前默认双路径的 P0。完成状态应表述为：**计划与 SQLite 只读验证完成；实现骨架和 happy path 部分完成；统一运行时、迁移 apply、恢复链路和验收证据未完成。**

## Findings

### P1 - 实际 Harness 没有进入统一 loader，legacy renderer 仍是第二套加载路径

- Evidence: `app/agent/harness/stream_inner.py:21` 先取得 `app.agent.harness.loop.build_stateful_context` 并在第 22–23 行直接委派，unified flag 检查在第 24 行之后。`app/agent/harness/loop.py:44` 始终 re-export legacy `app.agent.context.integration.build_stateful_context`。运行时诊断得到 `delegates_before_unified_check=True`。
- Evidence: `app/agent/harness/stream_inner.py:234` 仍按 stateful flag 分支；false 分支在第 301–309 行直接调用 `ContextBuilder.abuild`。`render_unified_envelope` 和两种 render policy 在应用代码中没有消费者。
- Impact: unified=true 时 structured 模式仍使用旧 `ContextStateStore` 加载；stateful=false 时仍使用独立 `ContextBuilder` 加载。计划的首要目标没有落地，pilot 文档“single repository, dual render policy”的描述不成立。
- Recommendation: 在 Harness context 阶段首先按 unified flag 调用一次 `ContextRepository.load_envelope`，然后只选择 renderer；保留 monkeypatch 兼容但不能让 facade re-export 抢在 unified dispatch 之前。
- Verification: 新增真实 `HarnessService.stream` spy tests，分别设置 stateful true/false，断言 repository load 恰好一次、legacy store/`ContextBuilder.abuild` 均不读取数据。

### P1 - `projection=None` 会覆盖并清空既有结构化上下文

- Evidence: `app/services/context_repository.py:254` 在 projection enabled 且 commit 未携带 projection 时创建全新的空 `AgentContextState`，随后标记 ready 并推进水位。
- Evidence: route clarify 在 `app/agent/harness/stream_inner.py:207` 发生于 context load 之前；legacy renderer、早期 fallback 等路径也可能让 `runtime.context_state` 为 `None`。
- Reproduction: `/tmp` 两轮隔离测试先保存 `current_goal=preserve-me`，第二轮以 `projection=None` 提交；结果 `goal_after_projection_none=''`、`status='ready'`、水位推进到最新 turn。
- Impact: unified 启用后，澄清或无 state terminal path 会静默删除之前的目标、计划、证据和工具状态，并宣称 projection 已完整应用最新 turn。
- Recommendation: projection 缺失时在同一事务内读取并保留现有 compact projection，或显式标记 stale/disabled；只有确定性 reducer 成功后才能标记 ready 和推进 `last_applied`。
- Verification: 增加 existing projection + clarify/fallback/legacy-render terminal tests，断言旧 structured blocks 不丢失，缺 reducer 时水位不前移。

### P1 - unified commit 失败后 API 再走 legacy append，重新制造分离提交

- Evidence: Harness 在 committer 异常时设置 `_unified_context_committed=False`（`app/agent/harness/loop.py:164` 等）。API 对任何 false/missing marker 都调用 `_persist_turn`（`app/api/assistant.py:345`–354）。
- Impact: repository 已 rollback 后，API 又用独立事务写入 turn，正好恢复了本计划要消除的“turn 有、projection 无”生命周期；该写入也不会通过统一 repository 标记 projection stale。
- Recommendation: 区分“unified committer 未安装”和“committer 执行失败”。unified 已启用但 commit 失败时，不得调用 legacy `_persist_turn`；如产品要求保留 turn，应调用 repository 的显式 turn-only/stale 事务。
- Verification: committer fault-injection test 断言 legacy append 未调用，或只产生 repository 管理的 turn-only + stale 记录；不得出现第二个 persistence owner。

### P1 - behind migration 没有重建，却把旧 projection 标记为已追平

- Evidence: `scripts/migrate_context_projection.py:196` 对所有“有 snapshot 且有 turns”的会话直接 compact 原 snapshot，并在第 212–214 行把水位设置成最新 turn/ready；没有根据 behind 分类执行 reducer。
- Reproduction: 隔离库中 snapshot 只覆盖 turn 0，随后新增 turn 1。`apply` 返回 `compacted=1, rebuilt=0`，保留旧 `current_goal=old-goal`，但写入 `last_applied=1, status=ready`。
- Impact: 已批准的 3 个 behind 会话会被虚假标记为完整，后续 loader/cache 无法再发现缺失投影；审计水位失去可信度。
- Recommendation: 将 classification 结果驱动 apply；behind 必须执行明确定义的 turn/event reducer，无法重建时保持 stale，不能推进水位。snapshot+turn ahead 也应单独归为不可信，而不是复用 behind/compact。
- Verification: 增加 behind 和 snapshot+turn-ahead fixtures，核对 reducer 产物、状态和水位；故意缺少 reducer 时 apply 必须 blocked 或 stale。

### P1 - schema 与 backup gate 既可能绕过审批，也无法在当前 v2 DB 上可靠启动

- Evidence: `ContextRepository._ensure_database` 在 enforcement=false 时自动 CREATE/ALTER（`app/services/context_repository.py:533`–610），与“只允许显式 migration up”的计划冲突。
- Reproduction: 对 live v2 数据库副本执行第一次 `load_envelope`，在创建 commit-id index 时因列尚未添加而失败：`OperationalError: no such column: commit_id`。
- Evidence: projection `apply` 会自行 `ALTER TABLE`（`scripts/migrate_context_projection.py:144`–157），绕开 migration runner；其“verified backup”校验仅检查文件存在且 size>0（第 130–133 行），没有 quick-check、来源/新鲜度、空间或计数验证。
- Impact: canary 若未先迁移会直接启动失败；若调整语句顺序又可能在未授权 load/apply 时隐式改 live schema。非空但损坏或陈旧的备份也能通过 destructive projection apply gate。
- Recommendation: unified runtime 始终 `require_current(version=3)`，禁止 service 自行 DDL；测试 fixture 显式跑 migration。projection apply 必须拒绝 schema<3，并复用现有 backup/free-space/quick-check gate。
- Verification: v2 fixture load 只报告 schema blocked 且文件 fingerprint 不变；损坏、陈旧、计数不匹配 backup 均拒绝 apply；只有 migration v3 后才允许 canary load。

### P1 - Redis inflight/checkpoint 恢复链路未接入

- Evidence: repository 只有 `save_inflight`（`app/services/context_repository.py:425`），没有 `load_inflight` 或 `delete_inflight`。`persist_runtime_state` 只有定义，无运行时调用；`render_unified_envelope` 同样未使用。
- Evidence: 当前 tool/close wrappers 只是把 legacy `persist_stateful_context` 改成 `persist_snapshot=False`，仍写旧 context key；checkpoint rehydrate 仍读取旧 store/`ContextSnapshotService`（`app/agent/harness/events_emit.py:201` 起）。
- Impact: committed/inflight 分离、stage version coalescing、timeout resume 和完成后清理都未形成闭环。unified 开启后 checkpoint ref 无法恢复新 inflight key，可能丢失已完成工具阶段的上下文。
- Recommendation: 实现并接入 save/load/delete inflight；checkpoint ref 必须包含 run id、base projection version 和 state version；所有 stage persistence 统一走 coalescing helper，resume 只在显式 checkpoint 路径读取 inflight。
- Verification: 中断式 checkpoint test 覆盖保存、保守恢复、scope/version mismatch 拒绝、完成清理和 TTL fallback；普通新请求断言不读 inflight。

### P2 - cache 校验与 history window 不满足计划契约

- Evidence: Redis committed cache 只比较 `projection_version`（`app/services/context_repository.py:163`–176）；cache payload 也只保存 owner/session/version/projection，没有 last-applied watermark，读取时不校验 payload scope。
- Evidence: repository 先读取该 session 的全部 turns 和完整 events JSON（第 106–115 行），再在内存截取。`_select_turn_window` 在 `history_max_turns=0` 时返回全部 turns（第 457–475 行），与 legacy 的“0=禁用历史”语义相反。
- Reproduction: 配置 `history_max_turns=0`、写入两轮后，unified envelope 返回 2 turns。
- Impact: cache 在 restore/migration/version 碰撞时可能接受错误内容；长会话仍全量读取大 events JSON，削弱性能目标；关闭历史反而可能把全量历史送入 prompt。
- Recommendation: cache 同时校验 scope、projection version、last-applied id/index 和 status；SQL 端按预算 LIMIT，只选择渲染必需列；0 明确返回空窗口。
- Verification: cache scope/watermark poisoning tests、history=0 test、长会话 SQL/query-size test。

### P2 - 声明的完成验证不可复现

- Evidence: progress 声明 `55 passed`，实际同一命令输出 52 个通过点后 pytest 不退出；隔离 stateful test 断言通过，但进程在 20 秒后 exit 124。
- Evidence: `make check-harness-size` 实际打印所有文件均 <=1000 后，因为 Makefile 第 627 行执行 `raise print(...)` 触发 `TypeError`，命令 exit 2；progress 却记录为通过。
- Evidence: soft-path 23 项首个失败仍是旧 `assistant(..., owner_key=...)` 签名，完整选择集在本次 120 秒外部超时前未结束。
- Evidence: Redis live auditor 在 15 秒、以及 `REDIS_SOCKET_TIMEOUT=1` + 10 秒两次外部超时，未按文档输出 blocked JSON。
- Impact: 当前验证记录不能作为 canary/验收证据；测试 assertion 通过和进程正常退出被混为一谈。
- Recommendation: 修复异步资源/后台任务清理、Makefile 退出表达式和 Redis audit 超时关闭；修正旧 API 测试签名；用精确命令与 exit code 重写 progress evidence。
- Verification: 所有声明命令无外部 timeout、exit 0；Redis 不健康时在配置超时内 exit 2 并输出脱敏 blocked JSON。

### P3 - 新文件尚未达到完整 lint 基线

- Evidence: critical-only Ruff 通过，但对新增文件运行完整 Ruff 得到 6 项：unused imports/variable、typing import、无占位 f-string。
- Impact: 不影响核心行为，但与“交付质量完成”不一致。
- Recommendation: 修复 6 项并在 progress 中明确采用完整 Ruff 还是 critical-only gate。
- Verification: 对本计划新增/修改文件运行完整 `ruff check`，exit 0。

## Completion Matrix

| Item | Status | Notes |
| --- | --- | --- |
| SOP 计划、审批门、回滚设计 | Complete | 文档结构完整，canonical/migration 策略已记录 |
| SQLite 只读 auditor | Complete | live fingerprint 未变；60 会话分类对账 |
| Redis 实例审计 | Missing | live 命令超时，未产出 blocked JSON |
| Compact projection v2 serializer | Complete | 专项测试通过；去 recent text/patch tail/nested identity |
| Migration v3 代码 | Partial | additive schema 已写；live 仍 version 2；runtime gate 有问题 |
| ContextRepository happy-path commit | Partial | 原子/idempotent fixture 通过；异常/空 projection/并发未达标 |
| 单一加载入口 | Missing | 实际 Harness 被 facade re-export 导回 legacy loader |
| 双 render policy runtime 接线 | Missing | renderer 仅定义/单测，legacy 分支仍直接 ContextBuilder |
| Projection behind reducer | Missing | 只标记/compact，没有确定性补投影 |
| Redis committed cache | Partial | 可读写，但只校验 projection version |
| Redis inflight + checkpoint resume | Missing | 只有未接线 save helper，无 load/delete/resume |
| 运行中 SQLite 写抑制 | Partial | unified 下 suppress snapshot，但仍走 legacy Redis key |
| Terminal path 单一提交 | Partial | happy path 可用；失败时回 legacy append；None 会清空状态 |
| 三个独立降级开关 | Partial | repository DB=false 有测试；unified 双 renderer/tools 组合未完成 |
| Live schema/projection migration | Missing (authorized gate) | version 2，pending migration 3；未执行符合授权 |
| Canary / P50-P95 / write-count 对照 | Missing | 尚未授权且实现未过质量门 |
| Harness 单文件 <=1000 | Complete behavior / Broken command | 实际最大 915；Make target exit 2 |
| Default-off 回退 | Complete | unified flag 默认 false，当前默认路径保持 |

## Test And Verification Notes

通过：

```text
新增实现专项 24 passed in 0.56s
compileall app/scripts: exit 0
Ruff E9/F63/F7/F82: exit 0
git diff --check: exit 0
SQLite auditor: status=ok, mutated=false, quick_check=ok
projection dry-run: 21 aligned / 3 behind / 17 snapshot-only-zero /
                    12 snapshot-only-ahead / 7 turn-only, total=60
```

未通过或未完成：

```text
live schema verify: version 2, latest 3, pending [3], exit 1（符合尚未授权状态）
声明的 55-test command: 52 dots 后不退出，人工终止
isolated stateful harness test: assertion PASSED，pytest process timeout exit 124
soft-path selected: 首项 auth signature failure；完整命令 timeout
make check-harness-size: 实际行数合格，但 target exit 2
full Ruff on new files: 6 findings
Redis include audit: 两次外部 timeout，无 blocked JSON
```

本次对 live SQLite 的操作仅为 read-only status/audit/dry-run；未执行 schema `up`、projection `apply` 或数据更新。隔离行为复现只写 `/tmp`。

## Next Steps

1. 保持 `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false`，禁止 canary；先修复 P1 loader/render dispatch。
2. 修复 `projection=None` 和 commit failure 两条数据完整性路径，补全部 terminal fault tests。
3. 定义确定性 turn/event reducer，并让 load repair 与 migration apply 共用；behind 未成功 reduce 时不得推进水位。
4. 删除 repository/migration script 的隐式 DDL，强制 schema v3 和真实 backup gate。
5. 完成 inflight save/load/delete、checkpoint ref、resume 和 stage coalescing 接线。
6. 修复 cache watermark/scope 校验、SQL history window 和 `history_max_turns=0` 语义。
7. 修复测试进程退出、Make target、旧 API 测试与完整 Ruff；重新生成可复现 progress evidence。
8. 代码评审再次通过后，补健康 Redis 只读审计；再分别申请 schema up、projection apply 和 canary 授权。
