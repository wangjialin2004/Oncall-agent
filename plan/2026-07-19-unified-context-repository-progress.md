# Progress: Unified Context Repository (2026-07-19)

## Status

**代码修复与本地项目门禁已完成；已具备申请 live schema v3 的条件，但尚不可 canary。**

2026-07-19 首次 completion review 提出的 6 个 P1、2 个 P2 与 scoped Ruff
问题均已修复并通过回归。统一总开关仍默认 `false`。Live schema `up`、
projection `apply`、Redis mutation、canary 和默认 true 均未执行，仍需分别授权。

最新复审：
`docs/reviews/completion-review-2026-07-19-unified-context-repository-2.md`。

下一棒入口：
`docs/pilot/handoff-2026-07-19-unified-context-repository.md`。

## Approvals

| Gate | Decision | When |
|---|---|---|
| Canonical source / atomic commit | Approved | 2026-07-19 |
| Migration policy (audit-first) | Approved | 2026-07-19 |
| Live schema v3 `up` | Not requested | — |
| Projection `apply` on live DB | Not requested | — |
| Canary / default true | Not requested | — |

## Resolved Completion-Review Findings

1. **Single loader / dual renderer**
   - `HarnessService` 在 unified 模式只调用一次
     `ContextRepository.load_envelope`。
   - structured/legacy 只选择 renderer；均不调用
     `ContextBuilder.abuild` 或 legacy `ContextStateStore`。
   - 真实 Harness spy tests 覆盖两种 renderer。
2. **`projection=None` 数据完整性**
   - terminal commit 在同一 SQLite 事务内读取并保留可信旧 projection，
     用共享 reducer 应用缺失的 committed turns；不再写空 projection。
3. **Single persistence owner on failure**
   - Harness 标记 unified commit `attempted` / `committed`。
   - API 仅在 committer 未安装时走 legacy append；统一提交失败不再二次写 turn。
4. **Shared deterministic reducer**
   - 新增 `app/agent/context/reducer.py`。
   - Runtime behind repair、`projection=None` commit 与 migration apply 共用。
   - Behind/ahead/corrupt/turn-only fixtures 均验证状态与实际水位。
5. **Explicit schema and backup gates**
   - `ContextRepository` 始终要求 schema v3，删除所有隐式 DDL。
   - v2 数据库副本 load 返回明确 blocked，文件 hash 不变。
   - Projection apply 拒绝 schema<3；备份验证覆盖 SQLite quick-check、
     source freshness、table set、canonical row counts 和 free space。
6. **Inflight checkpoint lifecycle**
   - 实现 save/load/delete、scope/base/state-version 校验、同 version coalescing、
     explicit checkpoint-only resume 和 complete 后清理。
   - 普通新请求不读取 inflight；legacy checkpoint ref 只读兼容且不触发旧 snapshot 回填。
7. **Cache/history/query semantics**
   - committed cache 校验 scope、projection version、last-applied id/index、status。
   - `history_max_turns=0` 返回空历史；正常 load 在 SQL 端 LIMIT，且不读取 events JSON。
8. **Delivery gates**
   - 修复 pytest checkpoint task 生命周期、Make harness-size 表达式、Make Python 解释器、
     Redis auditor timeout/close、旧 API auth 测试签名和 scoped full Ruff。
   - Make 测试目标默认关闭经验蒸馏/反模式写入，避免测试污染 live memory。

## Verification Evidence

### Unified focused

```bash
LONG_TERM_MEMORY_DISTILL_ENABLED=false \
HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=false \
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_context_repository.py \
  tests/test_context_projection_migration.py \
  tests/test_context_store.py \
  tests/test_context_integration.py \
  tests/test_context_snapshot_service.py \
  tests/test_context_views.py \
  tests/test_context_dedup.py \
  tests/test_harness_stateful_context.py \
  tests/test_harness_stateful_checkpoint_resume.py \
  tests/test_harness_checkpoint.py \
  tests/test_database_migrations.py \
  tests/test_audit_context_storage.py \
  tests/test_context_projection.py \
  tests/test_context_unified_wiring.py \
  -q --no-cov
# 115 passed in 1.06s, exit 0
```

### Existing soft path

```bash
LONG_TERM_MEMORY_DISTILL_ENABLED=false \
HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=false \
PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_harness_service.py \
  -k 'attachment or two_turn or history or context_builder or stateful or fallback or clarify' \
  -q --no-cov
# 23 passed, 44 deselected, exit 0
```

### Project ci-smoke and static gates

```bash
make ci-smoke
# 49 tests, exit 0

make check-harness-size
# max stream_inner.py 962 lines; exit 0

PYTHONPATH=. .venv/bin/python -m compileall -q app scripts
PYTHONPATH=. .venv/bin/ruff check --select E9,F63,F7,F82 app tests scripts
# exit 0

.venv/bin/ruff check <unified implementation and focused test files>
# All checks passed

git diff --check
# exit 0
```

仓库级 `ruff check app tests scripts` 与 `ruff format --check` 仍暴露大量历史基线
（不属于本计划新增文件）；本计划 scoped full Ruff 已清零，AGENTS 要求的 critical Ruff 已通过。

## Read-Only Live Evidence

- Live SQLite 仍为 version 2，pending `[3]`；未执行 schema `up`。
- Projection dry-run：21 aligned / 3 behind / 0 snapshot+turn-ahead /
  17 snapshot-only-zero / 12 snapshot-only-ahead / 7 turn-only，合计 60。
- Live context audit：60 conversations / 52 turns / 53 snapshots，
  `quick_check=ok`，主 DB hash
  `5ed75bb788654787761dbbd29d58ff0b321a99aaa22c9e26512a2856939493ef`
  before/after 一致。
- 对 live v2 文件副本首次 `load_envelope`：明确
  `database schema is not current: version=2 latest=3`，副本 hash 不变。
- Redis 不可达时 auditor 在约 0.2s 内输出脱敏 `status=blocked` 并 exit 2，
  不再挂起或伪造空成功。

## Material Deviations / Operational Notes

1. Checkpoint background tasks 现在由 request-scoped registry 管理，并在 stream 结束时收口；
   默认 Redis 只有在 lifespan health=`ready` 后才执行 checkpoint I/O，注入 store 不受影响。
2. 复审期间发现历史测试会触发经验蒸馏/反模式写入。测试目标已关闭这些 hook；
   live DB 中已存在的 test-session experience/WAL 不做清理，因清理属于另一次 live 数据写授权。
3. Full repository formatting/lint baseline 仍有历史债；未进行跨 100 个文件的无关格式化。

## Final Closure Revalidation

2026-07-19 最终交付前再次执行本地门禁：115 focused、23 soft-path、
49 ci-smoke、harness size、compileall、critical Ruff、`git diff --check` 和
默认开关断言均 exit 0。计划第 9 节验收矩阵已按证据同步；仅 live rollout
门禁保持未勾选。

只读 live 复核结果保持一致：schema current=2 / pending `[3]`；因此
`migrate_database.py verify` 按设计 exit 1，防止把 v2 误判为 ready。
Context audit 与 projection dry-run 均 exit 0、`mutated=false`，分类仍为
21 aligned / 3 behind / 17 snapshot-only-zero / 12 snapshot-only-ahead /
7 turn-only（合计 60），DB 文件 before/after hash 一致。

## Remaining Authorized Gates

1. 用户单独批准后执行 live schema v3 `up`，随后立即 status/verify/audit。
2. 基于新鲜、已验证 backup 在副本上重跑 projection apply；再单独申请 live apply。
3. Redis 健康后补 committed/inflight key count、TTL、bytes、schema 聚合。
4. 仅对新 session/canary owner 开启 unified，记录 two-turn 连续性、
   SQLite transactions/turn、Redis writes/stage、projection bytes、P50/P95。
5. 上述结果通过后，才讨论默认 true；当前必须保持 false。
