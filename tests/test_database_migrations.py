from __future__ import annotations

import sqlite3

import pytest

from app.config import config
from app.services.database_migration_service import DatabaseMigrationService


def test_status_and_verify_are_read_only_for_empty_database(tmp_path) -> None:
    db_path = tmp_path / "memory.db"
    service = DatabaseMigrationService(db_path=db_path, backup_dir=tmp_path / "backups")

    status = service.status()
    assert status["exists"] is False
    assert status["current_version"] == 0
    assert service.verify()["verified"] is False
    assert not db_path.exists()


def test_up_requires_backup_and_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "memory.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / db_path.name).write_bytes(b"verified backup")
    service = DatabaseMigrationService(db_path=db_path, backup_dir=backup_dir)

    first = service.up()
    assert first["schema_ok"] is True
    assert first["checksums_ok"] is True
    assert first["current_version"] == 2
    second = service.up()
    assert second["current_version"] == 2
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='uploaded_files'").fetchone()

    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE schema_migrations SET checksum='tampered'")
    assert service.verify()["verified"] is False


def test_up_failure_rolls_back_without_version_record(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "memory.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / db_path.name).write_bytes(b"verified backup")
    service = DatabaseMigrationService(db_path=db_path, backup_dir=backup_dir)
    monkeypatch.setattr(service, "_table_sql", None, raising=False)

    original = service._ensure_migration_table

    def fail_after_marker(connection):
        original(connection)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(service, "_ensure_migration_table", fail_after_marker)
    with pytest.raises(RuntimeError, match="injected"):
        service.up()
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone() is None


def test_up_migrates_known_legacy_columns(tmp_path) -> None:
    db_path = tmp_path / "memory.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / db_path.name).write_bytes(b"verified backup")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE conversations (owner_key TEXT, session_id TEXT, title TEXT, created_at TEXT, updated_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE conversation_turns (id INTEGER, owner_key TEXT, session_id TEXT, turn_index INTEGER, user_message TEXT, assistant_answer TEXT, route TEXT, case_id TEXT, events_json TEXT, created_at TEXT)"
        )

    service = DatabaseMigrationService(db_path=db_path, backup_dir=backup_dir)
    result = service.up()
    assert result["schema_ok"] is True
    with sqlite3.connect(db_path) as connection:
        conversation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(conversations)")
        }
        turn_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(conversation_turns)")
        }
    assert {"memory_summary", "context_state_json"} <= conversation_columns
    assert {"user_context", "attachment_refs_json"} <= turn_columns


def test_service_enforcement_verifies_version_without_altering(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.conversation_service import ConversationService

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE conversations (owner_key TEXT, session_id TEXT, title TEXT, created_at TEXT, updated_at TEXT)"
        )
    monkeypatch.setattr(config, "db_schema_enforcement_enabled", True)
    service = ConversationService(db_path=db_path)

    with pytest.raises(RuntimeError, match="database schema is not current"):
        service._ensure_database()
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(conversations)")}
    assert "memory_summary" not in columns


def test_service_enforcement_accepts_explicitly_migrated_database(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.conversation_service import ConversationService

    db_path = tmp_path / "memory.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / db_path.name).write_bytes(b"verified backup")
    DatabaseMigrationService(db_path=db_path, backup_dir=backup_dir).up()
    monkeypatch.setattr(config, "db_schema_enforcement_enabled", True)

    service = ConversationService(db_path=db_path)
    service._ensure_database()
    assert service._initialized is True
