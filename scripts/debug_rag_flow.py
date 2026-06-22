"""Inspect Milvus knowledge chunks and run the retrieve_knowledge RAG path."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_QUERY = "CPU使用率过高，如何排查和处理，常见根因、判断方法、只读排查步骤、临时止血建议"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether aiops-docs chunks exist in Milvus and run RAG retrieval."
    )
    parser.add_argument(
        "--docs-dir",
        default="aiops-docs",
        help="Local docs directory expected to be indexed.",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help="Query to run through vector_search_service and retrieve_knowledge.",
    )
    parser.add_argument("--top-k", type=int, default=10, help="Search result count.")
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=200,
        help="Maximum Milvus rows to inspect for source metadata.",
    )
    parser.add_argument(
        "--release-on-exit",
        action="store_true",
        help=(
            "Release the Milvus collection before exiting. Avoid this while the "
            "backend is running because release() unloads the collection server-side."
        ),
    )
    return parser


def source_name(metadata: dict[str, Any]) -> str:
    raw = (
        metadata.get("_source")
        or metadata.get("source")
        or metadata.get("_file_name")
        or metadata.get("file_name")
        or ""
    )
    return Path(str(raw)).name if raw else ""


def source_path(metadata: dict[str, Any]) -> str:
    raw = (
        metadata.get("_source")
        or metadata.get("source")
        or metadata.get("_file_name")
        or metadata.get("file_name")
        or ""
    )
    return str(raw)


def fetch_rows(collection: Any, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    try:
        rows = collection.query(
            expr="",
            output_fields=["id", "content", "metadata"],
            limit=limit,
        )
    except Exception:
        rows = collection.query(
            expr='id != ""',
            output_fields=["id", "content", "metadata"],
            limit=limit,
        )
    return [dict(row) for row in rows]


async def run_tool(query: str) -> tuple[str, int]:
    from app.tools.knowledge_tool import retrieve_knowledge

    raw = await retrieve_knowledge.run({"query": query})
    if isinstance(raw, tuple) and len(raw) == 2:
        context, docs = raw
        return str(context), len(docs or [])
    return str(raw), 0


def print_doc_inventory(docs_dir: Path, rows: list[dict[str, Any]]) -> None:
    local_files = sorted(path.name for path in docs_dir.glob("*") if path.is_file())
    row_sources = Counter()
    matched_by_source = Counter()
    matched_by_filename = Counter()

    local_file_set = set(local_files)
    docs_dir_name = docs_dir.name
    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        name = source_name(metadata)
        path = source_path(metadata)
        row_sources[name or "(missing source)"] += 1
        if docs_dir_name and docs_dir_name in path:
            matched_by_source[name or "(missing source)"] += 1
        if name in local_file_set:
            matched_by_filename[name] += 1

    print("\n[local docs]")
    print(f"docs_dir={docs_dir}")
    print(f"file_count={len(local_files)}")
    for name in local_files:
        print(f"  - {name}")

    print("\n[milvus sampled sources]")
    if not rows:
        print("sampled_rows=0")
        return
    print(f"sampled_rows={len(rows)}")
    for name, count in row_sources.most_common(20):
        print(f"  - {name}: {count}")

    print("\n[aiops-docs match check]")
    print(f"matched_by_source_path={sum(matched_by_source.values())}")
    print(f"matched_by_filename={sum(matched_by_filename.values())}")
    for name in local_files:
        print(
            f"  - {name}: "
            f"source_path={matched_by_source.get(name, 0)}, "
            f"filename={matched_by_filename.get(name, 0)}"
        )


def print_search_results(query: str, top_k: int) -> None:
    from app.services.vector_search_service import vector_search_service

    print("\n[vector_search_service.search]")
    print(f"query={query}")
    results = vector_search_service.search(query, top_k=top_k)
    print(f"result_count={len(results)}")
    for result in results:
        snippet = " ".join((result.content or "").split())[:240]
        print(
            f"  - rank={result.rank} type={result.retrieval_type} "
            f"score={result.score} source={result.source} id={result.id}"
        )
        print(f"    {snippet}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.top_k <= 0:
        parser.error("--top-k must be greater than 0")
    if args.sample_limit < 0:
        parser.error("--sample-limit must be >= 0")

    from app.config import config
    from app.core.milvus_client import milvus_manager

    docs_dir = Path(args.docs_dir).resolve()
    print("[config]")
    print(f"milvus={config.milvus_host}:{config.milvus_port}")
    print(f"collection={milvus_manager.COLLECTION_NAME}")
    print(f"retrieval_mode={config.rag_retrieval_mode}")
    print(f"rag_top_k={config.rag_top_k}")
    print(f"dense_field={config.rag_dense_vector_field}")
    print(f"sparse_field={config.rag_sparse_vector_field}")

    rows: list[dict[str, Any]] = []
    milvus_manager.connect()
    collection = milvus_manager.get_collection()
    print("\n[milvus collection]")
    print(f"num_entities={collection.num_entities}")
    print(f"schema_fields={[field.name for field in collection.schema.fields]}")
    rows = fetch_rows(collection, args.sample_limit)
    print_doc_inventory(docs_dir, rows)
    print_search_results(args.query, args.top_k)

    print("\n[retrieve_knowledge tool]")
    context, doc_count = asyncio.run(run_tool(args.query))
    print(f"doc_count={doc_count}")
    print("context_preview:")
    print(context[:1200])

    if args.release_on_exit:
        milvus_manager.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
