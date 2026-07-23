"""Explicit SQLite migration/backup helpers for context repository tests."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app.services.database_migration_service import DatabaseMigrationService


def initialize_context_db(db_path: Path) -> Path:
    backup_dir = db_path.parent / f"{db_path.stem}-bootstrap-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / db_path.name).touch()
    DatabaseMigrationService(db_path=db_path, backup_dir=backup_dir).up()
    return db_path


def create_verified_backup(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / db_path.name
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    now_ns = max(db_path.stat().st_mtime_ns, backup_path.stat().st_mtime_ns)
    os.utime(backup_path, ns=(now_ns, now_ns))
    return backup_path


__all__ = ["create_verified_backup", "initialize_context_db"]
