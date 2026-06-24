"""Rebuild the Milvus vector collection and reindex documents.

This script:
1. Drops the existing collection (deleting all vector data)
2. Recreates it with the current rag_retrieval_mode (supports dense / bm25 / hybrid)
3. Reindexes all documents from the uploads directory
4. Verifies the new collection has the expected schema fields

Examples:
    python scripts/rebuild_vector_collection.py --yes
    python scripts/rebuild_vector_collection.py --yes --directory ./aiops-docs
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drop/recreate the Milvus collection, reindex documents, and verify schema."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation — this deletes all existing vector data.",
    )
    parser.add_argument(
        "--directory",
        default=None,
        help="Directory to reindex. Defaults to the application's upload directory.",
    )
    parser.add_argument(
        "--skip-reindex",
        action="store_true",
        help="Only rebuild the collection; skip document reindexing.",
    )
    return parser


def _collection_schema_summary(collection: Any) -> dict[str, Any]:
    """Return a human-readable summary of the current collection schema."""
    fields = []
    functions = []
    for field in collection.schema.fields:
        info: dict[str, Any] = {"name": field.name, "dtype": str(field.dtype)}
        if hasattr(field, "params") and field.params:
            info["params"] = dict(field.params)
        fields.append(info)
    if hasattr(collection.schema, "functions") and collection.schema.functions:
        for func in collection.schema.functions:
            func_type = getattr(func, "function_type", None) or getattr(func, "type", None)
            functions.append({
                "name": func.name,
                "type": getattr(func_type, "name", str(func_type)),
                "input_fields": list(func.input_field_names) if hasattr(func, "input_field_names") else [],
                "output_fields": list(func.output_field_names) if hasattr(func, "output_field_names") else [],
            })
    return {
        "collection_name": collection.name,
        "num_entities": collection.num_entities,
        "fields": fields,
        "functions": functions,
    }


def _is_bm25_function_type(func_type: Any) -> bool:
    if func_type is None:
        return False

    if getattr(func_type, "name", "").lower() == "bm25":
        return True

    func_type_text = str(func_type).lower()
    if func_type_text == "bm25" or func_type_text.endswith(".bm25"):
        return True

    try:
        from pymilvus import FunctionType

        return func_type == FunctionType.BM25 or int(func_type) == int(FunctionType.BM25)
    except Exception:
        return False


def _verify_sparse_vector_support(collection: Any, sparse_field_name: str) -> bool:
    """Check that the collection has sparse vector field and BM25 function."""
    field_names = {field.name for field in collection.schema.fields}
    has_sparse_field = sparse_field_name in field_names

    funcs = getattr(collection.schema, "functions", []) or []
    has_bm25_func = any(
        _is_bm25_function_type(
            getattr(f, "function_type", None) or getattr(f, "type", None)
        )
        for f in funcs
    )

    return has_sparse_field and has_bm25_func


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("Rebuilding deletes all existing vector data; pass --yes to confirm")

    from app.config import config
    from app.core.milvus_client import milvus_manager
    from app.services.vector_index_service import vector_index_service
    from app.services.vector_store_manager import vector_store_manager

    # ── 0. Print current configuration ──────────────────────────────
    print("=" * 60)
    print("Collection Rebuild & Reindex")
    print("=" * 60)
    print(f"  Milvus:          {config.milvus_host}:{config.milvus_port}")
    print(f"  Collection:      {milvus_manager.COLLECTION_NAME}")
    print(f"  Retrieval mode:  {config.rag_retrieval_mode}")
    if config.rag_retrieval_mode == "hybrid":
        print(f"  Hybrid ranker:   {config.rag_hybrid_ranker}")
        if config.rag_hybrid_ranker == "weighted":
            print(f"    dense_weight:  {config.rag_dense_weight}")
            print(f"    bm25_weight:   {config.rag_bm25_weight}")
        else:
            print(f"    rrf_k:         {config.rag_rrf_k}")
    print(f"  Dense field:     {config.rag_dense_vector_field}")
    if config.rag_retrieval_mode in {"bm25", "hybrid"}:
        print(f"  Sparse field:    {config.rag_sparse_vector_field}")
    print(f"  Top-K:           {config.rag_top_k}")
    print(f"  Reindex after:   {not args.skip_reindex}")
    print("=" * 60)

    # ── 1. Connect and capture pre-rebuild state ────────────────────
    milvus_manager.connect(validate_schema=False)
    old_collection = milvus_manager.get_collection()
    old_summary = _collection_schema_summary(old_collection)
    print("\n[Before rebuild]")
    print(json.dumps(old_summary, ensure_ascii=False, indent=2))

    # ── 2. Rebuild collection ───────────────────────────────────────
    print("\n[Rebuilding collection...]")
    milvus_manager.rebuild_collection(confirm=True)
    vector_store_manager.reinitialize()
    print("Collection rebuild complete.")

    # ── 3. Verify new schema ────────────────────────────────────────
    new_collection = milvus_manager.get_collection()
    new_summary = _collection_schema_summary(new_collection)
    print("\n[After rebuild]")
    print(json.dumps(new_summary, ensure_ascii=False, indent=2))

    if config.rag_retrieval_mode in {"bm25", "hybrid"}:
        ok = _verify_sparse_vector_support(new_collection, config.rag_sparse_vector_field)
        if ok:
            print(f"\n✅ Sparse vector field '{config.rag_sparse_vector_field}' + BM25 Function confirmed.")
        else:
            print(f"\n❌ Sparse vector support NOT detected! BM25/hybrid search will not work correctly.")
            return 1

    # ── 4. Reindex documents ────────────────────────────────────────
    if args.skip_reindex:
        print("\n[Skipping reindex per --skip-reindex]")
    else:
        print("\n[Reindexing documents...]")
        result = vector_index_service.index_directory(directory_path=args.directory)
        print("\n[Reindex result]")
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

        if not result.success:
            print(f"\n⚠️  Reindex completed with {result.fail_count} failures.")
            return 1

        # ── 5. Confirm entities were inserted ───────────────────────
        new_collection = milvus_manager.get_collection()
        print(f"\n✅ Done — collection has {new_collection.num_entities} entities.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
