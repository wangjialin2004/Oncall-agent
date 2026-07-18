#!/usr/bin/env python3
"""Inspect or explicitly upgrade the long-term-memory SQLite schema."""

from __future__ import annotations

import argparse
import json
import sys

from app.services.database_migration_service import DatabaseMigrationService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "verify", "up"))
    parser.add_argument("--db", default=None, help="SQLite path (defaults to MEMORY_DB_PATH)")
    parser.add_argument("--backup-dir", default=None)
    args = parser.parse_args()
    service = DatabaseMigrationService(db_path=args.db, backup_dir=args.backup_dir)
    try:
        if args.command == "status":
            result = service.status()
        elif args.command == "verify":
            result = service.verify()
        else:
            result = service.up()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if args.command != "verify" or result.get("verified") else 1
    except Exception as exc:
        print(json.dumps({"status": "blocked", "mutated": False, "reason": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
