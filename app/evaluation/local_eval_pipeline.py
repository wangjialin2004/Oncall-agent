"""Small helpers for the offline local RAG evaluation pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RagEvalCase:
    """One offline RAG evaluation case."""

    question: str
    ground_truth: str
    expected_sources: list[str]


def load_cases(path: str | Path) -> list[RagEvalCase]:
    """Load JSONL evaluation cases from disk."""

    cases_path = Path(path)
    cases: list[RagEvalCase] = []
    for line_no, line in enumerate(cases_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue

        raw = json.loads(line)
        missing = [
            field
            for field in ("question", "ground_truth", "expected_sources")
            if field not in raw
        ]
        if missing:
            raise ValueError(f"{cases_path}:{line_no} missing required field(s): {', '.join(missing)}")

        expected_sources = raw["expected_sources"]
        if not isinstance(expected_sources, list) or not all(
            isinstance(item, str) for item in expected_sources
        ):
            raise ValueError(f"{cases_path}:{line_no} expected_sources must be a list of strings")

        cases.append(
            RagEvalCase(
                question=str(raw["question"]),
                ground_truth=str(raw["ground_truth"]),
                expected_sources=expected_sources,
            )
        )

    return cases


def build_eval_row(
    *,
    case: RagEvalCase,
    response: str,
    retrieved_contexts: list[str],
    retrieved_sources: list[str],
    retrieved_chunk_ids: list[str] | None = None,
    retrieved_chunk_indices: list[int | str | None] | None = None,
    retrieved_scores: list[float | None] | None = None,
    retrieved_ranks: list[int | None] | None = None,
    retrieved_heading_paths: list[str] | None = None,
    retrieved_content_lengths: list[int | None] | None = None,
) -> dict[str, Any]:
    """Build one local evaluation row plus source metadata."""

    row = {
        "user_input": case.question,
        "response": response,
        "retrieved_contexts": retrieved_contexts,
        "reference": case.ground_truth,
        "expected_sources": case.expected_sources,
        "retrieved_sources": retrieved_sources,
    }
    optional_trace_fields = {
        "retrieved_chunk_ids": retrieved_chunk_ids,
        "retrieved_chunk_indices": retrieved_chunk_indices,
        "retrieved_scores": retrieved_scores,
        "retrieved_ranks": retrieved_ranks,
        "retrieved_heading_paths": retrieved_heading_paths,
        "retrieved_content_lengths": retrieved_content_lengths,
    }
    row.update({key: value for key, value in optional_trace_fields.items() if value is not None})
    return row


def default_output_path(now: datetime | None = None) -> Path:
    """Return the default timestamped CSV output path."""

    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return Path("evals") / "results" / f"local_rag_{timestamp}.csv"
