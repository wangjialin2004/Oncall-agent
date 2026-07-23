#!/usr/bin/env python3
"""Audit / dry-run / apply compact context projection migration.

Default commands are read-only. ``apply`` requires an explicit verified backup
and never fabricates conversation turns for snapshot-only/ahead sessions.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.agent.context.projection import (  # noqa: E402
    PROJECTION_SCHEMA_V2,
    is_compact_projection,
    projection_from_dict,
    projection_to_dict,
)
from app.agent.context.reducer import (  # noqa: E402
    apply_committed_turns,
    rebuild_projection_from_turns,
)
from app.agent.context.state import AgentContextState  # noqa: E402
from app.services.database_migration_service import DatabaseMigrationService  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


def _open(db_path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.execute("PRAGMA query_only=ON")
    else:
        connection = sqlite3.connect(str(db_path), timeout=30.0)
    connection.row_factory = sqlite3.Row
    return connection


def classify_sessions(connection: sqlite3.Connection) -> dict[str, Any]:
    conversations = connection.execute(
        "SELECT owner_key, session_id, context_state_json FROM conversations"
    ).fetchall()
    turns = connection.execute(
        "SELECT owner_key, session_id, turn_index, id FROM conversation_turns"
    ).fetchall()
    turns_by: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in turns:
        turns_by.setdefault((str(row["owner_key"]), str(row["session_id"])), []).append(row)

    classes = {
        "snapshot_turn_aligned": 0,
        "snapshot_turn_behind": 0,
        "snapshot_turn_ahead": 0,
        "snapshot_only_zero_watermark": 0,
        "snapshot_only_ahead": 0,
        "turn_only": 0,
        "empty_conversation": 0,
    }
    planned_actions: dict[str, int] = {
        "compact_in_place": 0,
        "rebuild_from_turns": 0,
        "clear_untrusted_snapshot": 0,
        "noop": 0,
    }

    for row in conversations:
        key = (str(row["owner_key"]), str(row["session_id"]))
        session_turns = turns_by.get(key, [])
        has_snapshot = bool(row["context_state_json"])
        max_turn = max((int(t["turn_index"]) for t in session_turns), default=-1)
        expected_next = max_turn + 1
        last_turn_index = 0
        if has_snapshot:
            try:
                payload = json.loads(str(row["context_state_json"]))
                conversation = payload.get("conversation") or {}
                last_turn_index = int(conversation.get("last_turn_index") or 0)
            except Exception:
                last_turn_index = 0

        if has_snapshot and session_turns:
            if last_turn_index <= expected_next:
                if last_turn_index < expected_next:
                    classes["snapshot_turn_behind"] += 1
                    planned_actions["rebuild_from_turns"] += 1
                else:
                    classes["snapshot_turn_aligned"] += 1
                    planned_actions["compact_in_place"] += 1
            else:
                # Snapshot claims state unsupported by the canonical turn log.
                classes["snapshot_turn_ahead"] += 1
                planned_actions["rebuild_from_turns"] += 1
        elif has_snapshot and not session_turns:
            if last_turn_index == 0:
                classes["snapshot_only_zero_watermark"] += 1
            else:
                classes["snapshot_only_ahead"] += 1
            planned_actions["clear_untrusted_snapshot"] += 1
        elif session_turns and not has_snapshot:
            classes["turn_only"] += 1
            planned_actions["rebuild_from_turns"] += 1
        else:
            classes["empty_conversation"] += 1
            planned_actions["noop"] += 1

    classes["total"] = sum(v for k, v in classes.items() if k != "total")
    return {"classes": classes, "planned_actions": planned_actions}


def dry_run(db_path: Path) -> dict[str, Any]:
    with _open(db_path, read_only=True) as connection:
        classification = classify_sessions(connection)
    return {
        "status": "ok",
        "mutated": False,
        "db_path": str(db_path),
        "mode": "dry-run",
        **classification,
        "policy": {
            "snapshot_only": "clear_to_empty_projection_keep_backup",
            "snapshot_ahead": "do_not_fabricate_turns",
            "behind": "rebuild_from_committed_turns",
            "aligned": "compact_blocks_drop_recent_text_identity_patch_tail",
        },
    }


def apply(db_path: Path, *, backup_dir: Path) -> dict[str, Any]:
    migration = DatabaseMigrationService(db_path=db_path, backup_dir=backup_dir)
    migration.require_current()
    migration.verify_backup()

    changed = 0
    cleared = 0
    rebuilt = 0
    compacted = 0
    timestamp = utc_now()

    with _open(db_path, read_only=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            conversations = connection.execute(
                "SELECT owner_key, session_id, context_state_json FROM conversations"
            ).fetchall()
            for row in conversations:
                owner = str(row["owner_key"])
                session = str(row["session_id"])
                turns = connection.execute(
                    """
                    SELECT id, turn_index, user_message, assistant_answer,
                           attachment_refs_json, route, case_id, events_json, created_at
                    FROM conversation_turns
                    WHERE owner_key = ? AND session_id = ?
                    ORDER BY turn_index ASC
                    """,
                    (owner, session),
                ).fetchall()
                has_snapshot = bool(row["context_state_json"])
                if has_snapshot and not turns:
                    # Audit-first: do not promote untrusted snapshot to evidence.
                    connection.execute(
                        """
                        UPDATE conversations
                        SET context_state_json = NULL,
                            context_state_version = NULL,
                            context_state_updated_at = NULL,
                            context_projection_version = 0,
                            context_last_applied_turn_id = NULL,
                            context_last_applied_turn_index = -1,
                            context_projection_status = 'missing',
                            updated_at = ?
                        WHERE owner_key = ? AND session_id = ?
                        """,
                        (timestamp, owner, session),
                    )
                    cleared += 1
                    changed += 1
                    continue

                state: AgentContextState | None = None
                if has_snapshot and turns:
                    try:
                        payload = json.loads(str(row["context_state_json"]))
                        state = projection_from_dict(payload, owner_key=owner, session_id=session)
                        conversation = payload.get("conversation") or {}
                        claimed_next = int(conversation.get("last_turn_index") or 0)
                        expected_next = int(turns[-1]["turn_index"]) + 1
                        if claimed_next == expected_next:
                            compacted += 1
                        elif claimed_next < expected_next:
                            missing = [
                                turn for turn in turns if int(turn["turn_index"]) >= claimed_next
                            ]
                            apply_committed_turns(state, missing)
                            rebuilt += 1
                        else:
                            state = rebuild_projection_from_turns(
                                owner_key=owner,
                                session_id=session,
                                turns=turns,
                            )
                            rebuilt += 1
                    except Exception:
                        state = None

                if turns and state is None:
                    state = rebuild_projection_from_turns(
                        owner_key=owner,
                        session_id=session,
                        turns=turns,
                    )
                    rebuilt += 1

                if turns and state is not None:
                    compact = projection_to_dict(state)
                    if not is_compact_projection(compact):
                        raise RuntimeError("reducer did not produce a compact projection")
                    last = turns[-1]
                    connection.execute(
                        """
                        UPDATE conversations
                        SET context_state_json = ?,
                            context_state_version = ?,
                            context_state_updated_at = ?,
                            context_projection_version = COALESCE(context_projection_version, 0) + 1,
                            context_last_applied_turn_id = ?,
                            context_last_applied_turn_index = ?,
                            context_projection_status = 'ready',
                            updated_at = ?
                        WHERE owner_key = ? AND session_id = ?
                        """,
                        (
                            json.dumps(compact, ensure_ascii=False),
                            PROJECTION_SCHEMA_V2,
                            timestamp,
                            int(last["id"]),
                            int(last["turn_index"]),
                            timestamp,
                            owner,
                            session,
                        ),
                    )
                    changed += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return {
        "status": "ok",
        "mutated": changed > 0,
        "db_path": str(db_path),
        "mode": "apply",
        "changed_sessions": changed,
        "cleared_untrusted_snapshots": cleared,
        "compacted": compacted,
        "rebuilt_from_turns": rebuilt,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "dry-run", "apply"))
    parser.add_argument("--db", default=None)
    parser.add_argument("--backup-dir", default="volumes/backups/2026-07-18")
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args()

    from app.config import config

    db_path = Path(args.db or config.memory_db_path)
    try:
        if args.command in {"audit", "dry-run"}:
            # Reuse storage auditor totals when available.
            if args.command == "audit":
                from scripts.audit_context_storage import run_audit

                result = run_audit(db_path=db_path, include_redis=False)
            else:
                result = dry_run(db_path)
        else:
            result = apply(db_path, backup_dir=Path(args.backup_dir))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result.get("status") in {"ok", "degraded", "missing"} else 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "mutated": False,
                    "reason": type(exc).__name__,
                    "detail": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
