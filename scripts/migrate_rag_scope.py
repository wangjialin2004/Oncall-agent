#!/usr/bin/env python3
"""Plan or explicitly apply the legacy ``biz`` -> scoped ``biz_v2`` migration.

The default operation is read-only. It never creates, alters, rebuilds, or
drops a collection. Apply mode creates the target only when it is absent and
copies already-scoped rows; rows without trustworthy scope are refused and
reported for manual classification.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.config import config
from app.core.milvus_client import MilvusClientManager


class RagMigrationError(RuntimeError):
    def __init__(self, message: str, *, mutated: bool) -> None:
        super().__init__(message)
        self.mutated = mutated


def _schema_fields(collection: Any) -> list[str]:
    return sorted(str(field.name) for field in collection.schema.fields)


def _row_scope(row: dict[str, Any]) -> tuple[str, str] | None:
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
        except (TypeError, ValueError):
            parsed = None
        metadata = parsed if isinstance(parsed, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    scope_type = str(row.get("scope_type") or metadata.get("scope_type") or "").strip().lower()
    scope_id = str(row.get("scope_id") or metadata.get("scope_id") or "").strip()
    if scope_type not in {"system", "project", "user"} or not scope_id:
        return None
    if scope_type == "system" and scope_id != "system":
        return None
    return scope_type, scope_id


def _legacy_scope_plan(collection: Any, fields: set[str]) -> dict[str, Any]:
    """Inspect legacy rows without mutating or inventing a tenant scope."""

    output_fields = [field for field in ("id", "metadata", "scope_type", "scope_id") if field in fields]
    if "id" not in output_fields:
        return {
            "status": "blocked",
            "reason": "legacy_id_field_missing",
            "rows": 0,
            "scoped": 0,
            "unresolved": 0,
            "sample_unresolved_ids": [],
        }
    rows = collection.query(expr='id != ""', output_fields=output_fields)
    unresolved = [str(row.get("id")) for row in rows if _row_scope(row) is None]
    return {
        "status": "manual_scope_required" if unresolved else "ready",
        "reason": "rows_without_trustworthy_scope" if unresolved else "all_rows_scoped",
        "rows": len(rows),
        "scoped": len(rows) - len(unresolved),
        "unresolved": len(unresolved),
        "sample_unresolved_ids": unresolved[:20],
    }


def _connect_read_only() -> tuple[Any, str]:
    from pymilvus import connections

    alias = "rag_scope_migration"
    connections.connect(
        alias=alias,
        host=config.milvus_host,
        port=str(config.milvus_port),
        timeout=config.milvus_timeout / 1000,
    )
    return connections, alias


def inspect_collections(source_name: str, target_name: str) -> dict[str, Any]:
    """Inspect source/target without creating or mutating either collection."""

    from pymilvus import Collection, utility

    connections, alias = _connect_read_only()
    try:
        source_exists = bool(utility.has_collection(source_name, using=alias))
        target_exists = bool(utility.has_collection(target_name, using=alias))
        result: dict[str, Any] = {
            "source": {"name": source_name, "exists": source_exists},
            "target": {"name": target_name, "exists": target_exists},
            "mutated": False,
        }
        if source_exists:
            source = Collection(source_name, using=alias)
            result["source"].update(
                {"fields": _schema_fields(source), "entities": int(source.num_entities)}
            )
            fields = set(result["source"]["fields"])
            result["source"]["scope_ready"] = {"scope_type", "scope_id"}.issubset(fields)
            if result["source"]["scope_ready"]:
                result["source"]["scope_plan"] = _legacy_scope_plan(source, fields)
            else:
                result["source"]["scope_plan"] = {
                    "status": "manual_scope_required",
                    "reason": "source_schema_missing_scope_fields",
                    "missing_fields": [field for field in ("scope_type", "scope_id") if field not in fields],
                    "rows": int(source.num_entities),
                    "scoped": 0,
                    "unresolved": int(source.num_entities),
                    "sample_unresolved_ids": [],
                }
        if target_exists:
            target = Collection(target_name, using=alias)
            result["target"].update(
                {"fields": _schema_fields(target), "entities": int(target.num_entities)}
            )
        return result
    finally:
        connections.disconnect(alias)


def apply_migration(source_name: str, target_name: str, batch_size: int) -> dict[str, Any]:
    """Create target and copy scoped rows; never delete or alter the source."""

    from pymilvus import Collection, utility

    connections, alias = _connect_read_only()
    manager = MilvusClientManager()
    mutated = False
    try:
        if not utility.has_collection(source_name, using=alias):
            raise RuntimeError(f"source collection {source_name!r} does not exist")
        source = Collection(source_name, using=alias)
        source_fields = set(_schema_fields(source))
        required = {"id", "content", "metadata", config.rag_dense_vector_field, "scope_type", "scope_id"}
        missing = sorted(required - source_fields)
        source_entities = int(source.num_entities)
        if missing and source_entities > 0:
            raise RuntimeError(
                "source collection is not scope-ready; classify/reindex rows before apply: "
                + ", ".join(missing)
            )
        if missing:
            # An empty legacy collection has no rows to classify. It is safe to
            # create the scoped target schema, while non-empty legacy data is
            # deliberately blocked above.
            rows: list[dict[str, Any]] = []
        else:
            output_fields = sorted(required)
            rows = source.query(expr='id != ""', output_fields=output_fields)
            unscoped = [str(row.get("id")) for row in rows if _row_scope(row) is None]
            if unscoped:
                raise RuntimeError(
                    f"{len(unscoped)} rows have no trustworthy scope; first ids: {unscoped[:5]}"
                )

        if utility.has_collection(target_name, using=alias):
            target = Collection(target_name, using=alias)
            if int(target.num_entities) > 0:
                raise RuntimeError("target collection is non-empty; refusing duplicate apply")
        else:
            original_name = config.rag_collection_name
            config.rag_collection_name = target_name
            try:
                schema = manager._build_collection_schema()
                target = Collection(name=target_name, schema=schema, num_shards=manager.DEFAULT_SHARD_NUMBER, using=alias)
                mutated = True
                manager._collection = target
                manager._create_index()
            finally:
                config.rag_collection_name = original_name

        for start in range(0, len(rows), max(1, batch_size)):
            mutated = True
            target.insert(rows[start : start + batch_size])
        target.flush()
        return {
            "source_entities": source_entities,
            "target_entities": int(target.num_entities),
            "source_schema_missing": missing,
            "mutated": mutated,
        }
    except Exception as exc:
        raise RagMigrationError(str(exc), mutated=mutated) from exc
    finally:
        try:
            manager.close()
        finally:
            connections.disconnect(alias)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="biz")
    parser.add_argument("--target", default="biz_v2")
    parser.add_argument("--dry-run", action="store_true", help="Inspect only (default)")
    parser.add_argument("--apply", action="store_true", help="Create target and copy scoped rows")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("choose either --dry-run or --apply")
    try:
        payload = (
            apply_migration(args.source, args.target, args.batch_size)
            if args.apply
            else inspect_collections(args.source, args.target)
        )
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        mutated = bool(getattr(exc, "mutated", False))
        print(
            json.dumps(
                {"status": "blocked", "mutated": mutated, "reason": type(exc).__name__, "detail": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
