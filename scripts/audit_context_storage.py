#!/usr/bin/env python3
"""Read-only audit of conversation turns + context projection storage.

This script never mutates SQLite or Redis. SQLite opens with URI ``mode=ro``;
Redis is limited to PING / SCAN / PTTL / STRLEN / GET. Output is aggregate-only
and never includes owner_key, session_id, message text, attachment content, or
full Redis keys.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Allow ``python scripts/audit_context_storage.py`` from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _json_len(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return 0


def _safe_json_loads(raw: str | None) -> tuple[Any | None, str | None]:
    if raw is None or raw == "":
        return None, None
    try:
        return json.loads(raw), None
    except (TypeError, ValueError) as exc:
        return None, str(exc)


def _file_fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "size_bytes": 0, "mtime_ns": 0, "sha256": ""}
    data = path.read_bytes()
    return {
        "exists": True,
        "size_bytes": len(data),
        "mtime_ns": path.stat().st_mtime_ns,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    # Belt-and-suspenders: refuse accidental writes even if URI is ignored.
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def audit_sqlite(db_path: Path) -> dict[str, Any]:
    """Aggregate SQLite conversation/projection health without mutating the file."""

    before = _file_fingerprint(db_path)
    if not before["exists"]:
        return {
            "status": "missing",
            "mutated": False,
            "db_path": str(db_path),
            "before": before,
            "after": before,
            "totals": {},
            "migration_classes": {},
            "integrity": {},
            "warnings": ["database_missing"],
        }

    result: dict[str, Any] = {
        "status": "ok",
        "mutated": False,
        "db_path": str(db_path),
        "before": before,
        "physical": {},
        "totals": {},
        "snapshot_blocks": {},
        "watermark": {},
        "migration_classes": {},
        "integrity": {},
        "warnings": [],
    }

    connection = _open_readonly(db_path)
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        result["physical"] = {
            "quick_check": str(quick[0] if quick else "unknown"),
            "size_bytes": before["size_bytes"],
            "page_size": page_size,
            "page_count": page_count,
            "freelist_pages": freelist,
            "freelist_bytes": freelist * page_size,
            "freelist_page_ratio": round(freelist / page_count, 4) if page_count else 0.0,
        }

        if not _table_exists(connection, "conversations"):
            result["status"] = "blocked"
            result["warnings"].append("conversations_table_missing")
            return result

        conv_cols = _columns(connection, "conversations")
        turn_cols = (
            _columns(connection, "conversation_turns")
            if _table_exists(connection, "conversation_turns")
            else set()
        )

        conversations = connection.execute(
            "SELECT owner_key, session_id, updated_at, "
            "context_state_json, context_state_version, context_state_updated_at "
            "FROM conversations"
        ).fetchall()
        turns = []
        if _table_exists(connection, "conversation_turns"):
            turns = connection.execute(
                "SELECT id, owner_key, session_id, turn_index, user_message, user_context, "
                "attachment_refs_json, assistant_answer, events_json, created_at "
                "FROM conversation_turns"
            ).fetchall()

        turn_by_session: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in turns:
            key = (str(row["owner_key"]), str(row["session_id"]))
            turn_by_session.setdefault(key, []).append(row)

        snapshot_chars: list[int] = []
        block_chars: Counter[str] = Counter()
        schema_versions: Counter[str] = Counter()
        exact_recent_match_entries = 0
        exact_recent_match_chars = 0
        recent_turn_entries = 0
        recent_turn_chars = 0
        patch_tail_entries = 0
        patch_tail_with_value = 0
        identity_scope_mismatch = 0
        top_scope_mismatch = 0
        corrupt_json = 0
        valid_json = 0
        empty_owner_or_session = 0
        invalid_events_json = 0
        invalid_attachment_refs_json = 0
        rolling_summaries = 0
        user_message_chars = 0
        assistant_answer_chars = 0
        events_json_chars = 0
        user_context_chars = 0
        attachment_refs_chars = 0

        # Watermark relations (legacy last_turn_index means next index).
        aligned = 0
        snapshot_ahead = 0
        snapshot_behind = 0
        ahead_deltas: Counter[int] = Counter()
        behind_deltas: Counter[int] = Counter()
        snapshot_updated_before_conversation = 0

        # Exclusive migration classes from plan §2.4.
        class_aligned = 0
        class_behind = 0
        class_snapshot_only_zero = 0
        class_snapshot_only_ahead = 0
        class_turn_only = 0
        class_empty = 0

        snapshot_sessions = 0
        conversation_sessions = set()

        for row in conversations:
            owner = str(row["owner_key"] or "")
            session = str(row["session_id"] or "")
            key = (owner, session)
            conversation_sessions.add(key)
            if not owner or not session:
                empty_owner_or_session += 1

            if "memory_summary" in conv_cols:
                # Rolling summary presence is optional; count non-empty when available.
                pass

            payload_raw = row["context_state_json"] if "context_state_json" in row.keys() else None
            has_snapshot = bool(payload_raw)
            if has_snapshot:
                snapshot_sessions += 1
                snapshot_chars.append(len(str(payload_raw)))
                payload, err = _safe_json_loads(str(payload_raw))
                if err is not None or not isinstance(payload, dict):
                    corrupt_json += 1
                else:
                    valid_json += 1
                    schema_versions[str(payload.get("schema_version", "missing"))] += 1
                    if (
                        str(payload.get("owner_key") or "") != owner
                        or str(payload.get("session_id") or "") != session
                    ):
                        top_scope_mismatch += 1
                    identity = payload.get("identity") or {}
                    if isinstance(identity, dict):
                        id_owner = str(identity.get("owner_key") or "")
                        id_session = str(identity.get("session_id") or "")
                        if id_owner != owner or id_session != session:
                            identity_scope_mismatch += 1
                        block_chars["identity"] += _json_len(identity)
                    for block_name in (
                        "intent",
                        "working",
                        "evidence",
                        "conversation",
                        "tool",
                        "output",
                    ):
                        block_chars[block_name] += _json_len(payload.get(block_name))
                    patch_tail = payload.get("patch_tail") or []
                    if isinstance(patch_tail, list):
                        patch_tail_entries += len(patch_tail)
                        patch_tail_with_value += sum(
                            1 for item in patch_tail if isinstance(item, dict) and "value" in item
                        )
                        block_chars["patch_tail"] += _json_len(patch_tail)

                    conversation = payload.get("conversation") or {}
                    recent = (
                        conversation.get("recent_turns") if isinstance(conversation, dict) else []
                    )
                    if isinstance(recent, list):
                        recent_turn_entries += len(recent)
                        for item in recent:
                            text = ""
                            if isinstance(item, dict):
                                text = str(item.get("text") or item.get("content") or "")
                            recent_turn_chars += len(text)
                            role = str(item.get("role") or "") if isinstance(item, dict) else ""
                            for turn in turn_by_session.get(key, []):
                                if (
                                    role == "user"
                                    and text
                                    and text == str(turn["user_message"] or "")
                                ):
                                    exact_recent_match_entries += 1
                                    exact_recent_match_chars += len(text)
                                    break
                                if (
                                    role == "assistant"
                                    and text
                                    and text == str(turn["assistant_answer"] or "")
                                ):
                                    exact_recent_match_entries += 1
                                    exact_recent_match_chars += len(text)
                                    break

                    # Legacy last_turn_index is "next index".
                    last_turn_index = 0
                    if isinstance(conversation, dict):
                        try:
                            last_turn_index = int(conversation.get("last_turn_index") or 0)
                        except (TypeError, ValueError):
                            last_turn_index = 0
                    max_turn = max(
                        (int(t["turn_index"]) for t in turn_by_session.get(key, [])), default=-1
                    )
                    expected_next = max_turn + 1
                    delta = last_turn_index - expected_next
                    if not turn_by_session.get(key):
                        if last_turn_index == 0:
                            class_snapshot_only_zero += 1
                        else:
                            class_snapshot_only_ahead += 1
                            snapshot_ahead += 1
                            ahead_deltas[delta] += 1
                    else:
                        if delta == 0:
                            aligned += 1
                            class_aligned += 1
                        elif delta > 0:
                            snapshot_ahead += 1
                            ahead_deltas[delta] += 1
                            # Snapshot+turn with ahead should still be classified as aligned/behind mutually exclusive
                            # Plan: ahead all belong to snapshot-only. If turns exist and snapshot ahead, count as aligned? No —
                            # plan exclusive classes only list snapshot+turn aligned/behind. Treat as behind? Keep separate note.
                            class_aligned += 0
                            # Count under snapshot+turn behind/aligned only for delta<=0; positive with turns is rare.
                            # Report as watermark anomaly under integrity instead.
                        else:
                            snapshot_behind += 1
                            behind_deltas[delta] += 1
                            class_behind += 1

            session_turns = turn_by_session.get(key, [])
            if session_turns and not has_snapshot:
                class_turn_only += 1
            if not session_turns and not has_snapshot:
                class_empty += 1

            if has_snapshot and row["context_state_updated_at"] and row["updated_at"]:
                if str(row["updated_at"]) > str(row["context_state_updated_at"]):
                    snapshot_updated_before_conversation += 1

        # Turns present without conversation row (orphan).
        orphan_turn_sessions = 0
        for key, session_turns in turn_by_session.items():
            if key not in conversation_sessions:
                orphan_turn_sessions += 1
            for turn in session_turns:
                if not str(turn["owner_key"] or "") or not str(turn["session_id"] or ""):
                    empty_owner_or_session += 1
                user_message_chars += len(str(turn["user_message"] or ""))
                assistant_answer_chars += len(str(turn["assistant_answer"] or ""))
                events_raw = str(turn["events_json"] or "")
                events_json_chars += len(events_raw)
                payload, err = _safe_json_loads(events_raw if events_raw else None)
                if err is not None:
                    invalid_events_json += 1
                user_context_chars += len(str(turn["user_context"] or ""))
                refs_raw = str(turn["attachment_refs_json"] or "[]")
                attachment_refs_chars += len(refs_raw)
                _, refs_err = _safe_json_loads(refs_raw)
                if refs_err is not None:
                    invalid_attachment_refs_json += 1

        # Duplicate (owner, session, turn_index)
        duplicate_turn_index = 0
        if turns:
            seen: set[tuple[str, str, int]] = set()
            for turn in turns:
                key = (str(turn["owner_key"]), str(turn["session_id"]), int(turn["turn_index"]))
                if key in seen:
                    duplicate_turn_index += 1
                else:
                    seen.add(key)

        # Optional rolling summary count if column present.
        if "memory_summary" in conv_cols:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM conversations WHERE COALESCE(memory_summary, '') != ''"
            ).fetchone()
            rolling_summaries = int(row["n"] if row else 0)

        snapshot_only = snapshot_sessions - sum(
            1
            for key in turn_by_session
            if any(
                True
                for c in conversations
                if (str(c["owner_key"]), str(c["session_id"])) == key and c["context_state_json"]
            )
        )
        # Recompute snapshot-only from exclusive classes already tracked.
        snapshot_only = class_snapshot_only_zero + class_snapshot_only_ahead
        turn_only = class_turn_only

        result["totals"] = {
            "conversations": len(conversations),
            "conversation_turns": len(turns),
            "structured_snapshots": snapshot_sessions,
            "rolling_summaries": rolling_summaries,
            "snapshot_json_total_chars": sum(snapshot_chars),
            "snapshot_json_chars_min": min(snapshot_chars) if snapshot_chars else 0,
            "snapshot_json_chars_avg": int(statistics.mean(snapshot_chars))
            if snapshot_chars
            else 0,
            "snapshot_json_chars_median": int(statistics.median(snapshot_chars))
            if snapshot_chars
            else 0,
            "snapshot_json_chars_max": max(snapshot_chars) if snapshot_chars else 0,
            "user_message_chars": user_message_chars,
            "assistant_answer_chars": assistant_answer_chars,
            "events_json_chars": events_json_chars,
            "user_context_chars": user_context_chars,
            "attachment_refs_json_chars": attachment_refs_chars,
            "recent_turn_entries": recent_turn_entries,
            "recent_turn_chars": recent_turn_chars,
            "exact_recent_match_entries": exact_recent_match_entries,
            "exact_recent_match_chars": exact_recent_match_chars,
            "patch_tail_entries": patch_tail_entries,
            "patch_tail_entries_with_value": patch_tail_with_value,
            "snapshot_updated_before_conversation": snapshot_updated_before_conversation,
            "snapshot_only_sessions": snapshot_only,
            "turn_only_sessions": turn_only,
        }
        result["snapshot_blocks"] = dict(block_chars)
        result["watermark"] = {
            "aligned": aligned,
            "snapshot_ahead": snapshot_ahead,
            "snapshot_behind": snapshot_behind,
            "ahead_delta_counts": {str(k): v for k, v in sorted(ahead_deltas.items())},
            "behind_delta_counts": {str(k): v for k, v in sorted(behind_deltas.items())},
        }
        exclusive = {
            "snapshot_turn_aligned": class_aligned,
            "snapshot_turn_behind": class_behind,
            "snapshot_only_zero_watermark": class_snapshot_only_zero,
            "snapshot_only_ahead": class_snapshot_only_ahead,
            "turn_only": class_turn_only,
            "empty_conversation": class_empty,
        }
        exclusive["total"] = sum(exclusive.values())
        result["migration_classes"] = exclusive
        result["integrity"] = {
            "snapshot_json_valid": valid_json,
            "snapshot_json_corrupt": corrupt_json,
            "schema_versions": dict(schema_versions),
            "duplicate_owner_session_turn_index": duplicate_turn_index,
            "orphan_turn_sessions": orphan_turn_sessions,
            "empty_owner_or_session_rows": empty_owner_or_session,
            "invalid_events_json": invalid_events_json,
            "invalid_attachment_refs_json": invalid_attachment_refs_json,
            "top_level_scope_mismatch": top_scope_mismatch,
            "identity_scope_mismatch": identity_scope_mismatch,
            "conversation_columns": sorted(conv_cols),
            "turn_columns": sorted(turn_cols),
        }
        if corrupt_json or duplicate_turn_index or orphan_turn_sessions:
            result["status"] = "degraded"
            if corrupt_json:
                result["warnings"].append("corrupt_snapshot_json")
            if duplicate_turn_index:
                result["warnings"].append("duplicate_turn_index")
            if orphan_turn_sessions:
                result["warnings"].append("orphan_turn_sessions")
    finally:
        connection.close()

    after = _file_fingerprint(db_path)
    result["after"] = after
    if after != before:
        result["status"] = "blocked"
        result["mutated"] = True
        result["warnings"].append("file_fingerprint_changed")
    return result


async def audit_redis(
    *,
    enabled: bool,
    namespace: str,
    url: str,
    socket_timeout: float,
    max_keys: int = 10_000,
) -> dict[str, Any]:
    """Aggregate Redis context key health without printing keys or payloads."""

    result: dict[str, Any] = {
        "status": "skipped",
        "mutated": False,
        "enabled": enabled,
        "namespace_present": bool(namespace),
        "ping_ok": False,
        "context_key_count": 0,
        "ttl_buckets": {},
        "bytes_total": 0,
        "bytes_min": 0,
        "bytes_avg": 0,
        "bytes_max": 0,
        "schema_versions": {},
        "corrupt_json": 0,
        "warnings": [],
    }
    if not enabled:
        result["status"] = "disabled"
        return result

    try:
        from redis.asyncio import Redis
    except Exception as exc:  # pragma: no cover - optional dep
        result["status"] = "blocked"
        result["warnings"].append(f"redis_package_unavailable:{type(exc).__name__}")
        return result

    client = Redis.from_url(
        url,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_timeout,
        decode_responses=True,
        retry_on_timeout=False,
    )
    connected = False
    operation_timeout = max(0.1, float(socket_timeout))
    try:
        try:
            pong = await asyncio.wait_for(client.ping(), timeout=operation_timeout)
        except Exception as exc:  # noqa: BLE001 — audit must report blocked
            result["status"] = "blocked"
            result["warnings"].append(f"ping_failed:{type(exc).__name__}")
            return result
        if not pong:
            result["status"] = "blocked"
            result["warnings"].append("ping_false")
            return result
        connected = True
        result["ping_ok"] = True

        pattern = f"{namespace}:context:*"
        sizes: list[int] = []
        ttl_buckets: Counter[str] = Counter()
        schema_versions: Counter[str] = Counter()
        corrupt = 0
        scanned = 0
        cursor: int | str = 0
        while True:
            cursor, keys = await asyncio.wait_for(
                client.scan(cursor=cursor, match=pattern, count=200),
                timeout=operation_timeout,
            )
            for key in keys:
                scanned += 1
                if scanned > max_keys:
                    result["warnings"].append("scan_truncated")
                    break
                # Never emit key material. Only TTL/size/schema aggregates.
                try:
                    ttl = await asyncio.wait_for(client.pttl(key), timeout=operation_timeout)
                except Exception:
                    ttl = -2
                if ttl is None or ttl < 0:
                    ttl_buckets["no_expire_or_missing"] += 1
                elif ttl < 60_000:
                    ttl_buckets["lt_1m"] += 1
                elif ttl < 3_600_000:
                    ttl_buckets["lt_1h"] += 1
                elif ttl < 86_400_000:
                    ttl_buckets["lt_1d"] += 1
                else:
                    ttl_buckets["gte_1d"] += 1
                try:
                    size = int(
                        await asyncio.wait_for(client.strlen(key), timeout=operation_timeout) or 0
                    )
                except Exception:
                    size = 0
                sizes.append(size)
                try:
                    raw = await asyncio.wait_for(client.get(key), timeout=operation_timeout)
                except Exception:
                    raw = None
                if raw:
                    payload, err = _safe_json_loads(raw)
                    if err is not None or not isinstance(payload, dict):
                        corrupt += 1
                    else:
                        schema_versions[str(payload.get("schema_version", "missing"))] += 1
            if scanned > max_keys or int(cursor) == 0:
                break

        result["context_key_count"] = len(sizes)
        result["ttl_buckets"] = dict(ttl_buckets)
        result["bytes_total"] = sum(sizes)
        result["bytes_min"] = min(sizes) if sizes else 0
        result["bytes_avg"] = int(statistics.mean(sizes)) if sizes else 0
        result["bytes_max"] = max(sizes) if sizes else 0
        result["schema_versions"] = dict(schema_versions)
        result["corrupt_json"] = corrupt
        result["status"] = "ok" if not corrupt else "degraded"
    except Exception as exc:  # noqa: BLE001 — bounded audit failure
        result["status"] = "blocked"
        result["warnings"].append(f"scan_failed:{type(exc).__name__}")
    finally:
        # A cancelled connect can leave redis-py's pool close waiting on the
        # same unreachable socket. There is no established connection to close
        # after a failed PING, so only close a client that actually connected.
        if connected:
            try:
                await asyncio.wait_for(
                    client.aclose(close_connection_pool=True),
                    timeout=operation_timeout,
                )
            except Exception:
                pass
    return result


def run_audit(
    *,
    db_path: Path,
    include_redis: bool,
    redis_enabled: bool | None = None,
    redis_namespace: str | None = None,
    redis_url: str | None = None,
    redis_socket_timeout: float | None = None,
) -> dict[str, Any]:
    from app.config import config

    sqlite_report = audit_sqlite(db_path)
    report: dict[str, Any] = {
        "mutated": bool(sqlite_report.get("mutated")),
        "sqlite": sqlite_report,
        "redis": {"status": "skipped", "mutated": False},
    }
    if include_redis:
        report["redis"] = asyncio.run(
            audit_redis(
                enabled=bool(
                    redis_enabled
                    if redis_enabled is not None
                    else getattr(config, "redis_enabled", False)
                ),
                namespace=str(
                    redis_namespace
                    if redis_namespace is not None
                    else getattr(config, "redis_namespace", "super_biz_agent")
                ),
                url=str(
                    redis_url
                    if redis_url is not None
                    else getattr(config, "redis_url", "redis://localhost:6379/0")
                ),
                socket_timeout=float(
                    redis_socket_timeout
                    if redis_socket_timeout is not None
                    else getattr(config, "redis_socket_timeout", 5.0) or 5.0
                ),
            )
        )
        report["mutated"] = report["mutated"] or bool(report["redis"].get("mutated"))

    statuses = {sqlite_report.get("status"), report["redis"].get("status")}
    if "blocked" in statuses:
        report["status"] = "blocked"
    elif "degraded" in statuses:
        report["status"] = "degraded"
    elif "missing" in statuses:
        report["status"] = "missing"
    else:
        report["status"] = "ok"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="SQLite path (defaults to MEMORY_DB_PATH)")
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=True,
        help="Force read-only mode (default true; kept for explicit operator intent)",
    )
    parser.add_argument("--include-redis", action="store_true", help="Also audit Redis aggregates")
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args()

    from app.config import config

    db_path = Path(args.db or config.memory_db_path)
    report = run_audit(db_path=db_path, include_redis=bool(args.include_redis))
    print(
        json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2 if args.format == "json" else None
        )
    )
    if report.get("mutated"):
        return 3
    status = report.get("status")
    if status == "blocked":
        return 2
    if status in {"degraded", "missing"}:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
