"""Run an offline local RAG evaluation over local cases.

Examples:
    python scripts/evaluate_rag_local.py --skip-generation
    python scripts/evaluate_rag_local.py --cases evals/rag_cases.jsonl --top-k 5
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.local_eval_pipeline import build_eval_row, default_output_path, load_cases  # noqa: E402,I001


class EvaluationPreflightError(RuntimeError):
    """Raised when evaluation dependencies are unavailable before a run starts."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the local RAG pipeline without external scoring frameworks.")
    parser.add_argument("--cases", default="evals/rag_cases.jsonl", help="JSONL evaluation cases.")
    parser.add_argument("--top-k", type=int, default=3, help="Number of retrieved chunks per case.")
    parser.add_argument("--output", default=None, help="CSV output path.")
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Only run retrieval and write contexts; skip answer generation.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of cases for smoke runs.")
    return parser.parse_args()


def _source_from_result(result: Any) -> str:
    source = getattr(result, "source", "")
    if source:
        return str(source)
    if isinstance(result, dict):
        return str(result.get("source") or "")
    return ""


def _content_from_result(result: Any) -> str:
    content = getattr(result, "content", "")
    if content:
        return str(content)
    if isinstance(result, dict):
        return str(result.get("content") or "")
    return ""


def _metadata_from_result(result: Any) -> dict[str, Any]:
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict):
        return metadata
    if isinstance(result, dict) and isinstance(result.get("metadata"), dict):
        return result["metadata"]
    return {}


def _value_from_result(result: Any, field: str) -> Any:
    value = getattr(result, field, None)
    if value is not None:
        return value
    if isinstance(result, dict):
        return result.get(field)
    return None


def build_retrieval_trace(results: list[Any]) -> dict[str, list[Any]]:
    """Extract chunk-level trace fields without changing scoring inputs."""

    chunk_ids: list[str] = []
    chunk_indices: list[Any] = []
    scores: list[Any] = []
    ranks: list[Any] = []
    heading_paths: list[str] = []
    content_lengths: list[Any] = []

    for result in results:
        metadata = _metadata_from_result(result)
        result_id = _value_from_result(result, "id")
        chunk_ids.append(str(metadata.get("chunk_id") or result_id or ""))
        chunk_indices.append(metadata.get("chunk_index"))
        scores.append(_value_from_result(result, "score"))
        ranks.append(_value_from_result(result, "rank"))
        heading_paths.append(str(metadata.get("heading_path") or ""))
        content_lengths.append(metadata.get("content_length"))

    return {
        "retrieved_chunk_ids": chunk_ids,
        "retrieved_chunk_indices": chunk_indices,
        "retrieved_scores": scores,
        "retrieved_ranks": ranks,
        "retrieved_heading_paths": heading_paths,
        "retrieved_content_lengths": content_lengths,
    }


def ensure_retrieval_dependencies_available(milvus_manager: Any) -> None:
    """Fail fast when the vector retrieval backend is unavailable."""

    try:
        milvus_manager.connect()
        health_check = getattr(milvus_manager, "health_check", None)
        if callable(health_check) and not health_check():
            raise RuntimeError("health_check returned False")
    except Exception as exc:
        raise EvaluationPreflightError(
            f"Milvus unavailable for RAG evaluation: {exc}"
        ) from exc


async def _generate_answer(question: str, *, session_id: str) -> str:
    """Generate an answer via the knowledge-base Q&A expert (RAG path)."""

    from app.agent.experts.knowledge import knowledge_expert

    parts: list[str] = []
    async for event in knowledge_expert.run(
        message=question, session_id=session_id, trace_id=session_id
    ):
        if event.get("type") == "content":
            parts.append(str(event.get("data") or ""))
    return "".join(parts)


def _tokenize(text: str) -> set[str]:
    return {item.lower() for item in re.findall(r"[\w\u4e00-\u9fff]+", text) if len(item.strip()) > 1}


def _overlap_score(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens)


def _source_scores(expected_sources: list[str], retrieved_sources: list[str]) -> tuple[float, float]:
    expected = {item for item in expected_sources if item}
    retrieved = {item for item in retrieved_sources if item}
    if not expected:
        return 1.0, 1.0 if not retrieved else 0.0
    if not retrieved:
        return 0.0, 0.0
    matched = {source for source in expected if source in retrieved}
    return len(matched) / len(expected), len(matched) / len(retrieved)


def add_local_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add deterministic local scores so evaluation does not need external frameworks."""

    scored_rows: list[dict[str, Any]] = []
    for row in rows:
        reference = str(row.get("reference") or "")
        response = str(row.get("response") or "")
        contexts = [str(item) for item in row.get("retrieved_contexts") or []]
        expected_sources = [str(item) for item in row.get("expected_sources") or []]
        retrieved_sources = [str(item) for item in row.get("retrieved_sources") or []]
        joined_contexts = "\n".join(contexts)
        source_recall, source_precision = _source_scores(expected_sources, retrieved_sources)

        scored_rows.append(
            {
                **row,
                "local_source_recall": round(source_recall, 4),
                "local_source_precision": round(source_precision, 4),
                "local_reference_context_recall": round(_overlap_score(reference, joined_contexts), 4),
                "local_reference_response_recall": round(_overlap_score(reference, response), 4),
                "local_response_context_support": round(_overlap_score(response, joined_contexts), 4),
            }
        )
    return scored_rows


async def collect_rows(*, cases_path: Path, top_k: int, skip_generation: bool, limit: int | None) -> list[dict[str, Any]]:
    from app.core.milvus_client import milvus_manager
    from app.services.vector_search_service import vector_search_service

    cases = load_cases(cases_path)
    if limit is not None:
        cases = cases[: max(0, limit)]

    generate_answers = not skip_generation
    rows: list[dict[str, Any]] = []

    ensure_retrieval_dependencies_available(milvus_manager)
    try:
        for index, case in enumerate(cases, 1):
            results = vector_search_service.search(case.question, top_k=top_k)
            contexts = [_content_from_result(item) for item in results]
            sources = [_source_from_result(item) for item in results]
            trace = build_retrieval_trace(results)
            response = ""
            if generate_answers:
                response = await _generate_answer(
                    case.question, session_id=f"local-rag-eval-{index}"
                )

            rows.append(
                build_eval_row(
                    case=case,
                    response=response,
                    retrieved_contexts=contexts,
                    retrieved_sources=sources,
                    **trace,
                )
            )
    finally:
        milvus_manager.close()

    return rows


def write_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


async def async_main() -> int:
    args = parse_args()
    output_path = Path(args.output) if args.output else default_output_path()
    rows = await collect_rows(
        cases_path=Path(args.cases),
        top_k=args.top_k,
        skip_generation=args.skip_generation,
        limit=args.limit,
    )
    write_csv(add_local_scores(rows), output_path)
    print(f"Wrote {len(rows)} evaluation row(s) to {output_path}")
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except EvaluationPreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
