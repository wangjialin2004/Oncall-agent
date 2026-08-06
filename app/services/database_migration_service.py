"""Explicit, transactional SQLite schema migrations.

The runner is deliberately independent from the domain services. ``status``
and ``verify`` are read-only; only an operator-invoked ``up`` may alter a
database. No destructive down migration is provided.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import config


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...] = ()
    column_additions: tuple[tuple[str, str, str], ...] = ()

    @property
    def checksum(self) -> str:
        additions = ["|".join(item) for item in self.column_additions]
        payload = "\n".join([*self.statements, *additions]).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _table_sql() -> tuple[str, ...]:
    return (
        """CREATE TABLE IF NOT EXISTS conversations (
            owner_key TEXT NOT NULL, session_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, memory_summary TEXT NOT NULL DEFAULT '',
            memory_summary_turn_index INTEGER NOT NULL DEFAULT -1,
            context_state_json TEXT, context_state_version INTEGER,
            context_state_updated_at TEXT,
            PRIMARY KEY (owner_key, session_id)
        )""",
        """CREATE TABLE IF NOT EXISTS conversation_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_key TEXT NOT NULL,
            session_id TEXT NOT NULL, turn_index INTEGER NOT NULL,
            user_message TEXT NOT NULL DEFAULT '', user_context TEXT NOT NULL DEFAULT '',
            attachment_refs_json TEXT NOT NULL DEFAULT '[]',
            assistant_answer TEXT NOT NULL DEFAULT '', route TEXT NOT NULL DEFAULT '',
            case_id TEXT NOT NULL DEFAULT '', events_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS uploaded_files (
            id TEXT PRIMARY KEY, owner_key TEXT NOT NULL, original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL, storage_backend TEXT NOT NULL,
            storage_key TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            mime_type TEXT, file_hash TEXT NOT NULL, status TEXT NOT NULL,
            indexed_chunks INTEGER NOT NULL DEFAULT 0, indexing_error TEXT,
            auto_indexed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, UNIQUE(owner_key, file_hash)
        )""",
        """CREATE TABLE IF NOT EXISTS experience_memories (
            experience_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            owner_key TEXT NOT NULL DEFAULT '', visibility TEXT NOT NULL DEFAULT 'project',
            approved_by TEXT NOT NULL DEFAULT '', approved_at TEXT NOT NULL DEFAULT '',
            environment TEXT NOT NULL DEFAULT '', service_name TEXT NOT NULL DEFAULT '',
            symptoms TEXT NOT NULL, root_cause TEXT NOT NULL, resolution TEXT NOT NULL,
            evidence_summary TEXT NOT NULL, source_type TEXT NOT NULL,
            source_session_id TEXT NOT NULL DEFAULT '', source_feedback_id TEXT NOT NULL DEFAULT '',
            source_event_ids_json TEXT NOT NULL DEFAULT '[]', confidence REAL NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0, success_count INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active',
            milvus_pk TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS services (
            project_id TEXT NOT NULL, service_name TEXT NOT NULL, environment TEXT NOT NULL,
            owner_team TEXT NOT NULL DEFAULT '', owner_user TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL, PRIMARY KEY(project_id, service_name, environment)
        )""",
        """CREATE TABLE IF NOT EXISTS service_relations (
            project_id TEXT NOT NULL, source_service TEXT NOT NULL, target_service TEXT NOT NULL,
            relation_type TEXT NOT NULL, environment TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS service_baselines (
            project_id TEXT NOT NULL, service_name TEXT NOT NULL, environment TEXT NOT NULL,
            metric_name TEXT NOT NULL, min_value REAL NOT NULL, max_value REAL NOT NULL,
            unit TEXT NOT NULL DEFAULT '', sample_window TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(project_id, service_name, environment, metric_name)
        )""",
        """CREATE TABLE IF NOT EXISTS user_preferences (
            owner_key TEXT PRIMARY KEY, default_environment TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT 'zh-CN', detail_level TEXT NOT NULL DEFAULT 'normal',
            focused_services_json TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_conversation_turns_session ON conversation_turns (owner_key, session_id, turn_index)",
        "CREATE INDEX IF NOT EXISTS idx_uploaded_files_owner ON uploaded_files (owner_key)",
        "CREATE INDEX IF NOT EXISTS idx_uploaded_files_hash ON uploaded_files (file_hash)",
        "CREATE INDEX IF NOT EXISTS idx_experience_project_enabled ON experience_memories (project_id, enabled)",
        "CREATE INDEX IF NOT EXISTS idx_experience_project_status ON experience_memories (project_id, status)",
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline_domain_schema", statements=_table_sql()),
    Migration(
        2,
        "legacy_compatibility_columns",
        column_additions=(
            ("conversations", "memory_summary", "TEXT NOT NULL DEFAULT ''"),
            ("conversations", "memory_summary_turn_index", "INTEGER NOT NULL DEFAULT -1"),
            ("conversations", "context_state_json", "TEXT"),
            ("conversations", "context_state_version", "INTEGER"),
            ("conversations", "context_state_updated_at", "TEXT"),
            ("conversation_turns", "user_context", "TEXT NOT NULL DEFAULT ''"),
            ("conversation_turns", "attachment_refs_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("experience_memories", "status", "TEXT NOT NULL DEFAULT 'active'"),
            ("experience_memories", "owner_key", "TEXT NOT NULL DEFAULT ''"),
            ("experience_memories", "visibility", "TEXT NOT NULL DEFAULT 'project'"),
            ("experience_memories", "approved_by", "TEXT NOT NULL DEFAULT ''"),
            ("experience_memories", "approved_at", "TEXT NOT NULL DEFAULT ''"),
        ),
    ),
    # Additive only: commit/watermark metadata for unified context repository.
    # Does not rewrite context_state_json contents.
    Migration(
        3,
        "unified_context_projection_watermarks",
        # column_additions run before statements so the partial unique index
        # can reference the newly added commit_id column.
        column_additions=(
            ("conversation_turns", "commit_id", "TEXT"),
            ("conversations", "context_projection_version", "INTEGER NOT NULL DEFAULT 0"),
            ("conversations", "context_last_applied_turn_id", "INTEGER"),
            ("conversations", "context_last_applied_turn_index", "INTEGER NOT NULL DEFAULT -1"),
            ("conversations", "context_projection_status", "TEXT NOT NULL DEFAULT 'missing'"),
        ),
        statements=(
            # Partial unique index: non-empty commit_id is idempotent per owner/session.
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_turns_commit_id "
            "ON conversation_turns (owner_key, session_id, commit_id) "
            "WHERE commit_id IS NOT NULL AND commit_id != ''",
        ),
    ),
)

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "conversations": (
        "owner_key", "session_id", "title", "created_at", "updated_at",
        "memory_summary", "memory_summary_turn_index", "context_state_json",
        "context_state_version", "context_state_updated_at",
        "context_projection_version", "context_last_applied_turn_id",
        "context_last_applied_turn_index", "context_projection_status",
    ),
    "conversation_turns": (
        "id", "owner_key", "session_id", "turn_index", "user_message",
        "user_context", "attachment_refs_json", "assistant_answer", "route",
        "case_id", "events_json", "created_at", "commit_id",
    ),
    "uploaded_files": (
        "id", "owner_key", "original_name", "stored_name", "storage_backend",
        "storage_key", "size_bytes", "mime_type", "file_hash", "status",
        "indexed_chunks", "indexing_error", "auto_indexed", "created_at", "updated_at",
    ),
    "experience_memories": (
        "experience_id", "project_id", "owner_key", "visibility", "approved_by",
        "approved_at", "environment", "service_name", "symptoms", "root_cause",
        "resolution", "evidence_summary", "source_type", "source_session_id",
        "source_feedback_id", "source_event_ids_json", "confidence", "hit_count",
        "success_count", "enabled", "status", "milvus_pk", "created_at", "updated_at",
    ),
    "services": ("project_id", "service_name", "environment", "owner_team", "owner_user", "description", "enabled", "updated_at"),
    "service_relations": ("project_id", "source_service", "target_service", "relation_type", "environment", "updated_at"),
    "service_baselines": ("project_id", "service_name", "environment", "metric_name", "min_value", "max_value", "unit", "sample_window", "updated_at"),
    "user_preferences": ("owner_key", "default_environment", "language", "detail_level", "focused_services_json", "notes", "updated_at"),
}


class DatabaseMigrationService:
    def __init__(self, db_path: str | Path | None = None, backup_dir: str | Path | None = None):
        self.db_path = Path(db_path or config.memory_db_path)
        self.backup_dir = Path(backup_dir or "volumes/backups/2026-07-18")

    @property
    def latest_version(self) -> int:
        return MIGRATIONS[-1].version

    def status(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {"db_path": str(self.db_path), "exists": False, "current_version": 0, "latest_version": self.latest_version, "pending": [m.version for m in MIGRATIONS], "missing": list(REQUIRED_COLUMNS), "checksums_ok": False, "schema_ok": False}
        with sqlite3.connect(str(self.db_path)) as connection:
            current = self._current_version(connection)
            missing = self._missing_schema(connection)
            checksums_ok = self._checksums_ok(connection)
        return {"db_path": str(self.db_path), "exists": True, "current_version": current, "latest_version": self.latest_version, "pending": [m.version for m in MIGRATIONS if m.version > current], "missing": missing, "checksums_ok": checksums_ok, "schema_ok": not missing and checksums_ok and current >= self.latest_version}

    def verify(self) -> dict[str, Any]:
        result = self.status()
        result["verified"] = bool(result.get("schema_ok"))
        return result

    def up(self) -> dict[str, Any]:
        current_status = self.status()
        if current_status.get("schema_ok"):
            return current_status
        self._check_backup_and_space()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path), timeout=30.0) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_migration_table(connection)
                current = self._current_version(connection)
                for migration in MIGRATIONS:
                    if migration.version <= current:
                        continue
                    # Prefer column additions first so later statements may
                    # reference newly added columns (e.g. partial indexes).
                    for table, column, definition in migration.column_additions:
                        self._add_column_if_missing(
                            connection,
                            table=table,
                            column=column,
                            definition=definition,
                        )
                    for statement in migration.statements:
                        connection.execute(statement)
                    missing = self._missing_schema(connection)
                    if migration.version == self.latest_version and missing:
                        raise RuntimeError(
                            "migration cannot safely synthesize required schema: "
                            + ", ".join(missing)
                        )
                    connection.execute(
                        "INSERT INTO schema_migrations(version, name, applied_at, checksum) VALUES (?, ?, ?, ?)",
                        (migration.version, migration.name, datetime.now(UTC).isoformat(), migration.checksum),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.status()

    @staticmethod
    def _ensure_migration_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                applied_at TEXT NOT NULL, checksum TEXT NOT NULL
            )"""
        )

    @staticmethod
    def _add_column_if_missing(
        connection: sqlite3.Connection,
        *,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def require_current(self) -> None:
        result = self.verify()
        if not result["verified"]:
            raise RuntimeError(
                f"database schema is not current: version={result.get('current_version')} "
                f"latest={result.get('latest_version')} missing={result.get('missing')}"
            )

    @staticmethod
    def _current_version(connection: sqlite3.Connection) -> int:
        try:
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0] or 0)

    @staticmethod
    def _checksums_ok(connection: sqlite3.Connection) -> bool:
        try:
            rows = connection.execute(
                "SELECT version, checksum FROM schema_migrations"
            ).fetchall()
        except sqlite3.OperationalError:
            return False
        expected = {migration.version: migration.checksum for migration in MIGRATIONS}
        return all(version in expected and checksum == expected[version] for version, checksum in rows)

    def _missing_schema(self, connection: sqlite3.Connection) -> list[str]:
        missing: list[str] = []
        for table, columns in REQUIRED_COLUMNS.items():
            try:
                rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            except sqlite3.DatabaseError:
                rows = []
            existing = {str(row[1]) for row in rows}
            if not rows:
                missing.append(table)
                continue
            missing.extend(f"{table}.{column}" for column in columns if column not in existing)
        return missing

    def _check_backup_and_space(self) -> None:
        self.verify_backup()
        backup = self.backup_dir / self.db_path.name
        usage = shutil.disk_usage(self.db_path.parent if self.db_path.parent.exists() else Path("."))
        required = max(1 << 20, int(backup.stat().st_size * 1.2))
        if usage.free < required:
            raise RuntimeError("insufficient free disk space for migration")

    def verify_backup(self) -> dict[str, Any]:
        """Validate the operator backup without changing either database.

        Existing databases require a fresh, healthy backup with matching
        canonical row counts. A zero-byte SQLite image is accepted only for a
        database that does not exist yet, where there is no source data to lose.
        """

        backup = self.backup_dir / self.db_path.name
        if not backup.is_file():
            raise RuntimeError(f"verified backup is required: {backup}")
        if self.db_path.exists() and backup.stat().st_size <= 0:
            raise RuntimeError(f"verified backup is empty: {backup}")
        try:
            if backup.resolve() == self.db_path.resolve():
                raise RuntimeError("backup path must differ from source database")
        except FileNotFoundError:
            pass

        backup_health = self._read_only_health(backup)
        if backup_health["quick_check"] != "ok":
            raise RuntimeError("backup quick_check failed")
        if not self.db_path.exists():
            if backup_health["tables"]:
                raise RuntimeError("fresh database backup must be an empty SQLite image")
            return {
                "backup": str(backup),
                "source_exists": False,
                "quick_check": "ok",
                "row_counts_match": True,
            }

        source_health = self._read_only_health(self.db_path)
        if source_health["quick_check"] != "ok":
            raise RuntimeError("source database quick_check failed")
        if backup.stat().st_mtime_ns < self.db_path.stat().st_mtime_ns:
            raise RuntimeError("backup is older than source database")

        source_tables = set(source_health["tables"])
        backup_tables = set(backup_health["tables"])
        if source_tables != backup_tables:
            raise RuntimeError("backup schema table set does not match source")
        critical_tables = source_tables & {"conversations", "conversation_turns"}
        mismatched = [
            table
            for table in sorted(critical_tables)
            if source_health["row_counts"].get(table)
            != backup_health["row_counts"].get(table)
        ]
        if mismatched:
            raise RuntimeError(
                "backup canonical row counts do not match source: " + ", ".join(mismatched)
            )
        return {
            "backup": str(backup),
            "source_exists": True,
            "quick_check": "ok",
            "row_counts_match": True,
        }

    @staticmethod
    def _read_only_health(path: Path) -> dict[str, Any]:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
                connection.execute("PRAGMA query_only=ON")
                quick = connection.execute("PRAGMA quick_check").fetchone()
                tables = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    ).fetchall()
                ]
                row_counts = {
                    table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                    for table in tables
                }
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"backup/source is not a healthy SQLite database: {path}") from exc
        return {
            "quick_check": str(quick[0] if quick else "unknown"),
            "tables": tables,
            "row_counts": row_counts,
        }


database_migration_service = DatabaseMigrationService()
