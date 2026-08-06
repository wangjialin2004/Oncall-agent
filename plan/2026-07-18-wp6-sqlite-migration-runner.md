# WP-6 SQLite migration runner

> Status: phase 2 implementation complete; real long-term-memory database migrated and verified

## Problem

Several services create or alter tables on first request. That is not
auditable and can race across workers. Operators need a read-only schema
status/verify command and an explicit transactional upgrade path before the
services are switched to minimum-version validation.

## Decisions and defaults

- Manage the configured long-term-memory database first (`memory_db_path`);
  checkpoint/diagnosis stores remain separate follow-up work.
- `status` and `verify` never write. `up` is the only mutating command and
  requires a readable backup directory plus a free-space check.
- Migration rows are versioned and checksummed in `schema_migrations`.
- No destructive down migration is exposed.
- Application startup behavior remains phase-gated by `DB_SCHEMA_ENFORCEMENT_ENABLED`;
  the real database is migrated, but rollout of enforcement is a separate deployment step.

## Scope and non-goals

- Scope: schema inventory, required tables/columns/indexes, transactional
  `up`, idempotent reruns, and CLI tests.
- Non-goals: live production migration, file streaming/concurrency limits,
  checkpoint schema, or automatic startup ALTER.

## Affected files

- `app/services/database_migration_service.py`
- `scripts/migrate_database.py`
- `tests/test_database_migrations.py`
- progress/index documentation

## Verification and exit criteria

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_database_migrations.py -q --no-cov
PYTHONPATH=. .venv/bin/python scripts/migrate_database.py status
PYTHONPATH=. .venv/bin/python scripts/migrate_database.py verify
git diff --check
```

Empty, current-like, and known legacy SQLite fixtures must report missing/ready
state, transactional `up` must be idempotent, and a failed migration must leave
the database readable with no applied version recorded. When enforcement is
enabled, services verify the explicit migration version and do not run legacy
first-request DDL.

## Risks and rollback

`up` only adds tables/columns/indexes and never drops data. Restore the verified
backup and point `MEMORY_DB_PATH` back to it if an operator stops a migration;
the service remains on its existing compatibility path during this phase.

## Progress and verification (2026-07-18)

- Added migration version 2 for known legacy columns and checksum validation;
  empty/current/legacy fixture tests cover idempotence and rollback.
- Added `DB_SCHEMA_ENFORCEMENT_ENABLED` (default `false`). When enabled,
  conversation, context snapshot, file, preference, experience, and service
  knowledge services call `require_current()` and skip their legacy DDL path.
- `status` on the real database remains read-only: version `0`, pending `[1, 2]`,
  no missing columns; `up` was not executed.
- A copy of the current database migrated `0 -> 2` successfully with the dated
  backup gate; a second `up` was idempotent. The real database was not written.
- Focused regression set: **59 passed**. New migration files Ruff-clean;
  touched legacy services pass syntax/critical-error lint. Full Ruff still has
  pre-existing style findings in those services, recorded as a deviation.

## Live verification (2026-07-18)

- Existing dated backup was found stale relative to the live database; it was
  preserved as `long_term_memory.db.prior-20260718`, then refreshed using the
  SQLite backup API. Both live and backup copies returned `quick_check=ok` and
  matching counts (conversations=60, turns=52, experiences=484, uploaded_files=4).
- `scripts/migrate_database.py up` completed on the real database: version
  `0 -> 2`, `schema_ok=true`, checksums valid. A second `up` was idempotent.
- Final read-only verification: `schema_migrations=[(1, baseline_domain_schema),
  (2, legacy_compatibility_columns)]`; live data counts unchanged.
