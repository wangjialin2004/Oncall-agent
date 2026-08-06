# Progress: Unified Context Repository (2026-07-19)

## Status

**Live schema v3 + projection apply 已完成；`.env` canary 已开启 unified；代码默认仍为 `false`。**

2026-07-23 用户明确要求完成剩余 live 门并开启统一路径。已按交接 SOP 执行：

1. Gate 0 只读复核（基线更新为 62 会话 / 57 turns / 55 snapshots）
2. Gate 1 live schema v3 `up`
3. Gate 2 projection apply（去掉 recent_turns / patch_tail / identity 重复）
4. Gate 3 Redis 只读聚合（8 context keys，schema v1 旧 cache 可自然 miss）
5. Gate 4 `.env` canary：`HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=true`

代码默认值与 `.env.example` 仍保持 `false`，方便一键回滚。默认 true 仍未改。

最新复审：
`docs/reviews/completion-review-2026-07-19-unified-context-repository-2.md`。

交接入口：
`docs/pilot/handoff-2026-07-23-unified-context-runtime-canary.md`
（架构细节仍见 `docs/pilot/handoff-2026-07-19-unified-context-repository.md`）。

## Approvals

| Gate | Decision | When |
|---|---|---|
| Canonical source / atomic commit | Approved | 2026-07-19 |
| Migration policy (audit-first) | Approved | 2026-07-19 |
| Live schema v3 `up` | Approved by user request + executed | 2026-07-23 |
| Projection `apply` on live DB | Approved by user request + executed | 2026-07-23 |
| Redis read-only audit | Executed (healthy instance) | 2026-07-23 |
| `.env` canary true | Approved by user request + executed | 2026-07-23 |
| Code / example default true | Not requested | — |

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

## 2026-07-23 Live Rollout Evidence

### Gate 0 before mutation

- schema current=2 / latest=3 / pending `[3]`
- conversations / turns / snapshots = 62 / 57 / 55
- dry-run classes: aligned 22 / behind 3 / snapshot-only-zero 18 /
  snapshot-only-ahead 12 / turn-only 7（合计 62）
- planned actions: compact 22 / rebuild 10 / clear untrusted 30
- snapshot_json_total_chars ≈ 486,709；patch_tail ≈ 219,695；recent_turn_chars ≈ 32,853
- DB hash before apply path: recorded in audit output; backup created under
  `volumes/backups/context-v3/`

### Gate 1 schema v3

```bash
# verified online backup then:
PYTHONPATH=. .venv/bin/python scripts/migrate_database.py up \
  --db volumes/long_term_memory.db \
  --backup-dir volumes/backups/context-v3
# status/verify: current=3, pending=[], schema_ok=true, verified=true
# row counts unchanged: conversations=62, turns=57
# new columns present: context_projection_* + conversation_turns.commit_id
```

### Gate 2 projection apply

```bash
PYTHONPATH=. .venv/bin/python scripts/migrate_context_projection.py apply \
  --db volumes/long_term_memory.db \
  --backup-dir volumes/backups/context-v3 \
  --format json
# changed_sessions=62, compacted=22, rebuilt_from_turns=10,
# cleared_untrusted_snapshots=30, mutated=true
```

Post-apply checks:

| 指标 | 结果 |
|---|---:|
| quick_check | ok |
| conversations / turns | 62 / 57 |
| projection status ready / missing | 32 / 30 |
| compact projection payloads | 32 / 32 |
| noncompact payloads | 0 |
| recent_turn_entries / chars | 0 / 0 |
| patch_tail_entries | 0 |
| snapshot_json_total_chars | 117,613（约 -76%） |
| exact watermark alignment (applied_idx vs max turn) | 32 aligned / 0 behind / 0 ahead |

说明：旧 auditor 的 `snapshot_turn_behind` 启发式仍把部分 ready 行标成 behind，
是因为 compact v2 不再把 recent-turn 文本放进 projection；以
`context_last_applied_turn_index` 与 `MAX(turn_index)` 精确对账为准。

### Gate 3 Redis

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_context_storage.py \
  --db volumes/long_term_memory.db --read-only --include-redis --format json
```

- ping_ok=true，namespace present
- context_key_count=8，schema_versions `{1: 8}`
- bytes_total≈128,976；TTL 均 `<1d`
- 未执行 Redis 写/删；旧 v1 committed cache 与 schema v3 水位不匹配时会 miss，
  回源 SQLite projection

### Gate 4 canary enable

`.env` only:

```text
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=true
HARNESS_STATEFUL_CONTEXT_ENABLED=true   # unchanged default
```

Runtime smoke:

- `unified_context_repository_enabled()` → True
- ready session `load_envelope` → `source=sqlite_projection`，compact=True，
  recent/patch_tail 为空
- missing session → `source=fresh`，status=missing

### Focused revalidation after rollout

```bash
LONG_TERM_MEMORY_DISTILL_ENABLED=false \
HARNESS_ANTI_PATTERN_CAPTURE_ENABLED=false \
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_context_repository.py \
  tests/test_context_projection_migration.py \
  tests/test_context_unified_wiring.py \
  tests/test_audit_context_storage.py \
  tests/test_context_projection.py \
  tests/test_database_migrations.py \
  -q --no-cov
# 41 passed
```

顺手修复：`tests/test_context_unified_wiring.py` 的 `FixedRouter._resolve_route`
兼容后续 `previous_route` 参数（router lightpath 变更），否则真实 Harness
接线测试在 stream 入口失败。

### Still open

- 代码默认 / `.env.example` 仍为 false；默认 true 需单独批准
- 未跑 live two-turn / P50-P95 性能对照
- 未清理 Redis 旧 v1 context keys（依赖 miss + TTL）
- 历史 test-session experience 数据治理仍独立
- 未 `VACUUM`；文件大小仍约 11 MB，freelist 增大

只读 live 复核结果保持一致：schema current=2 / pending `[3]`；因此
`migrate_database.py verify` 按设计 exit 1，防止把 v2 误判为 ready。
Context audit 与 projection dry-run 均 exit 0、`mutated=false`，分类仍为
21 aligned / 3 behind / 17 snapshot-only-zero / 12 snapshot-only-ahead /
7 turn-only（合计 60），DB 文件 before/after hash 一致。

## Supersession Note (2026-08-01)

This historical progress record is superseded for current runtime status by
`plan/2026-08-01-remove-context-snapshot-service-progress.md`. The current
worktree defaults unified context to `true`, uses `ContextRepository` as the
runtime persistence owner, and retains stateful rendering as a compatibility
layer. The operator has explicitly chosen not to rebuild or clear the
historical projection drift reported by the current read-only dry-run; no
database mutation was performed. The older `false` defaults and open rollout
gates below are historical evidence only.

## Remaining Authorized Gates

1. 用户单独批准后执行 live schema v3 `up`，随后立即 status/verify/audit。
2. 基于新鲜、已验证 backup 在副本上重跑 projection apply；再单独申请 live apply。
3. Redis 健康后补 committed/inflight key count、TTL、bytes、schema 聚合。
4. 仅对新 session/canary owner 开启 unified，记录 two-turn 连续性、
   SQLite transactions/turn、Redis writes/stage、projection bytes、P50/P95。
5. 上述结果通过后，才讨论默认 true；当前必须保持 false。
