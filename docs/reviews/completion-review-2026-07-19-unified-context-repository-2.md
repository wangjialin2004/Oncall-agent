# Completion Review Report: 统一上下文仓储修复复审

- Review date: 2026-07-19
- Review scope: 首次评审 6 个 P1、2 个 P2、scoped lint 问题；repository/reducer/migration、Harness/API 接线、checkpoint inflight、测试与只读 live 状态
- Source material: `plan/2026-07-19-unified-context-repository.md`、progress、首次 completion review、当前 dirty worktree
- Verification commands: 115 focused、23 soft-path、49 ci-smoke、compile、critical/scoped Ruff、harness-size、diff-check、SQLite audit/dry-run、Redis blocked audit

## Overall Conclusion

结论：**代码修复完成，具备申请下一阶段 live schema v3 授权的条件；但 live rollout 尚未完成，不可直接 canary。** 首次评审列出的运行时 loader 绕过、`projection=None` 清空、失败后 legacy 二次写、behind 水位虚进、隐式 DDL/弱备份门、inflight 未闭环等问题均已修复并有 focused regression evidence。

统一 flag 仍默认 false；live SQLite 仍为 version 2，Redis 实例仍不可达，projection apply 与 canary/P50-P95 对照未获授权。因此当前准确状态是：**implementation ready / rollout blocked by explicit gates**，不是“功能已在生产完成”。

## Findings

### P1 - Live schema v3 与 projection apply 尚未授权

- Evidence: `scripts/migrate_database.py status` 返回 current 2 / latest 3 / pending `[3]`；live dry-run 分类合计 60，未执行 apply。
- Impact: unified runtime 现在会安全拒绝 v2，而不是隐式改 schema；因此在 schema v3 前开启 flag 会得到 degraded commit，而不是可用 canary。
- Recommendation: 单独批准 schema v3 `up`；verify/audit 通过后，再以新鲜 verified backup 单独批准 projection apply。
- Verification: status/verify current=3；apply 后五类/六类对账、watermark、scope、JSON 完整性全部通过。

### P1 - Redis 实例级 committed/inflight 聚合仍不可验证

- Evidence: auditor 已在约 0.2s 内正确返回 `status=blocked`, `ping_failed:ConnectionError`, exit 2；没有实例 key/TTL/bytes 数据。
- Impact: 代码降级行为已验证，但 canary 前仍缺 Redis 数据面容量、TTL 与 schema 证据。
- Recommendation: Redis 健康后只读运行 auditor，禁止输出 key/scope 原文。
- Verification: ping ok；committed/inflight 分组计数、TTL、bytes、schema/corrupt 聚合完成。

### P2 - Canary 与性能/写放大对照尚未执行

- Evidence: 未运行 unified on/off selected two-turn live eval；无 commit P50/P95、SQLite transactions/turn、Redis writes/stage、projection bytes after 数据。
- Impact: 功能正确性本地通过，但尚不能证明真实环境的连续性与性能收益。
- Recommendation: schema/apply/Redis gates 后，仅新 session/canary owner 开启；记录计划中的 before/after 指标并演练 rollback。
- Verification: two-turn 不回退、每 complete 一个 durable transaction、stage coalescing 符合上限、P50/P95 可接受。

### P2 - 历史测试会话 experience 数据需要单独治理

- Evidence: 只读查询发现 live `experience_memories` 中存在多批 `sess-*` / `visible-session` 测试来源记录；WAL 非零。Make 测试目标与 stateful tests 已默认关闭 distill/anti-pattern hook。
- Impact: 后续测试不再新增这类写入，但已有测试数据影响长期记忆数据卫生；它不改变本次 context turn/snapshot 对账。
- Recommendation: 另开数据治理计划，基于来源/时间/状态生成只读清单，获得授权后再清理；本次不删除 live 数据。
- Verification: 清理前后备份、row counts、quick-check、memory/index 对账，且无真实用户记录被删除。

## Resolved Findings From First Review

| First-review item | Status | Evidence |
|---|---|---|
| Unified loader bypass / second `ContextBuilder` load | Complete | Real `HarnessService.stream` tests for structured/legacy: repository load exactly once; legacy builder/store zero reads |
| `projection=None` clears state | Complete | Existing goal preserved; new committed question/output reduced; watermark advances to actual turn |
| Commit failure falls through to legacy append | Complete | attempted marker + API fault test; legacy `_persist_turn` not called |
| Behind migration falsely advances watermark | Complete | Shared deterministic reducer; behind/ahead fixtures verify state content and exact watermarks |
| Implicit DDL / weak backup | Complete | Runtime requires v3; v2 copy hash unchanged; corrupt/stale/count-mismatch backups rejected |
| Inflight resume absent | Complete | save/load/delete, coalescing, scope/base/state validation, explicit resume, normal-load isolation, completion cleanup |
| Cache/history SQL semantics | Complete | scope/version/id/index/status checks; history=0 empty; SQL LIMIT/no normal events read |
| Hanging pytest / Make / Redis / old auth tests | Complete | 115 + 23 + 49 tests exit 0; Make targets exit 0; Redis blocked exits 2; tests use principal |
| Scoped full Ruff findings | Complete | unified implementation/focused tests full Ruff clean |

## Completion Matrix

| Item | Status | Notes |
|---|---|---|
| SOP plan / approvals / rollback | Complete | Canonical source and audit-first policy approved |
| Single repository load | Complete | Both render modes share one envelope |
| Atomic turn + projection commit | Complete (code) | Includes idempotence, rollback and concurrent scoped indexes |
| Compact projection v2 | Complete | No turn text, nested/top-level scope copies, or patch tail |
| Shared reducer / behind repair | Complete | Runtime, terminal None path and migration share implementation |
| Redis committed cache validation | Complete (code) | Scope + full durable watermark validation |
| Redis inflight recovery | Complete (code) | Explicit resume only; complete cleanup |
| Test/process lifecycle | Complete | Background checkpoint tasks request-scoped and drained |
| Harness file <=1000 | Complete | max 962; Make exit 0 |
| Focused / soft-path / ci-smoke | Complete | 115 / 23 / 49, all exit 0 |
| Live SQLite audit | Complete read-only | Context totals stable; quick-check ok; main DB hash unchanged during audit |
| Live schema v3 | Missing (authorization gate) | current v2 |
| Live projection apply | Missing (authorization gate) | dry-run only |
| Redis instance aggregate | Missing (environment gate) | blocked, fail-fast works |
| Canary/performance evidence | Missing | must follow schema/apply/Redis gates |
| Unified default true | Not approved | remains false |

## Test And Verification Notes

```text
Unified focused: 115 passed, exit 0
Existing soft path: 23 passed / 44 deselected, exit 0
make ci-smoke: 49 tests, exit 0
make check-harness-size: max 962, exit 0
compileall: exit 0
critical Ruff all app/tests/scripts: exit 0
scoped full Ruff: exit 0
git diff --check: exit 0
Redis unavailable audit: blocked JSON, exit 2 in ~0.2s
v2 DB copy runtime gate: RuntimeError, mutated=false
live projection dry-run: total=60, mutated=false
```

未执行 live schema `up`、projection `apply`、Redis mutation、canary 或默认值切换。仓库级 full Ruff/format 仍有历史基线，不是本计划新增问题；本计划 scoped full Ruff 与强制 critical gate 已通过。

## Next Steps

1. 保持 `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false`。
2. 申请 live schema v3 `up` 授权并执行只读复核。
3. 在 fresh verified backup/copy 上复跑 projection apply，再申请 live apply。
4. Redis 健康后补实例聚合审计。
5. 仅在上述门通过后申请 canary，记录 two-turn、写次数、projection bytes、P50/P95 与 rollback。
6. 测试 experience 数据清理另立数据治理计划与授权，不在本次猜测性删除。
