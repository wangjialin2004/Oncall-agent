"""Run an offline RAGAS evaluation over local RAG cases.

Examples:
    python scripts/evaluate_rag_ragas.py --skip-generation
    python scripts/evaluate_rag_ragas.py --cases evals/rag_cases.jsonl --top-k 5
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import os
import sys
import types
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.ragas_pipeline import build_ragas_row, default_output_path, load_cases  # noqa: E402,I001


class EvaluationPreflightError(RuntimeError):
    """Raised when evaluation dependencies are unavailable before a run starts."""


class DashScopeCompatibleChatOpenAI(ChatOpenAI):
    """ChatOpenAI variant that keeps RAGAS prompts compatible with DashScope."""

    @staticmethod
    def _stringify_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content)

        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
                continue
            parts.append(str(item))
        return "\n".join(part for part in parts if part)

    def _normalize_messages_for_dashscope(self, messages: list[Any]) -> list[Any]:
        normalized_messages = []
        for message in messages:
            normalized_message = copy.copy(message)
            normalized_message.content = self._stringify_content(message.content)
            normalized_messages.append(normalized_message)
        return normalized_messages

    def _generate(self, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
        return super()._generate(self._normalize_messages_for_dashscope(messages), *args, **kwargs)

    async def _agenerate(self, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
        return await super()._agenerate(self._normalize_messages_for_dashscope(messages), *args, **kwargs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the local RAG pipeline with RAGAS.")
    parser.add_argument("--cases", default="evals/rag_cases.jsonl", help="JSONL evaluation cases.")
    parser.add_argument("--top-k", type=int, default=3, help="Number of retrieved chunks per case.")
    parser.add_argument("--output", default=None, help="CSV output path.")
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Only run retrieval and write contexts; skip LLM generation and RAGAS scoring.",
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
    """Extract chunk-level trace fields without changing RAGAS scoring inputs."""

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


def configure_ragas_openai_env(settings: Any | None = None) -> dict[str, str]:
    """Resolve OpenAI-compatible settings for RAGAS from env or app config."""

    if settings is None:
        from app.config import config as settings

    api_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or getattr(settings, "dashscope_api_key", "")
    )
    base_url = (
        os.getenv("OPENAI_BASE_URL")
        or os.getenv("DASHSCOPE_API_BASE")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    model = (
        os.getenv("RAGAS_MODEL")
        or os.getenv("DASHSCOPE_MODEL")
        or getattr(settings, "dashscope_model", "qwen-max")
    )
    embedding_model = (
        os.getenv("RAGAS_EMBEDDING_MODEL")
        or os.getenv("DASHSCOPE_EMBEDDING_MODEL")
        or getattr(settings, "dashscope_embedding_model", "text-embedding-v4")
    )

    if api_key:
        os.environ.setdefault("OPENAI_API_KEY", api_key)
    if base_url:
        os.environ.setdefault("OPENAI_BASE_URL", base_url)

    return {
        "api_key": str(api_key),
        "base_url": str(base_url),
        "model": str(model),
        "embedding_model": str(embedding_model),
    }


def create_ragas_llm(ragas_settings: dict[str, str]) -> Any:
    """Create the LangChain-backed LLM wrapper expected by RAGAS metrics."""

    _install_ragas_vertexai_import_shim()
    from ragas.llms.base import LangchainLLMWrapper

    chat_model = DashScopeCompatibleChatOpenAI(
        model=ragas_settings["model"],
        api_key=ragas_settings["api_key"],
        base_url=ragas_settings["base_url"],
        temperature=0,
        extra_body={"enable_thinking": False},
    )
    return LangchainLLMWrapper(chat_model)


def create_ragas_embeddings(ragas_settings: dict[str, str]) -> Any:
    """Create the LangChain-backed embeddings wrapper expected by RAGAS metrics."""

    _install_ragas_vertexai_import_shim()
    from langchain_openai import OpenAIEmbeddings
    from ragas.embeddings.base import LangchainEmbeddingsWrapper

    embeddings = OpenAIEmbeddings(
        model=ragas_settings["embedding_model"],
        api_key=ragas_settings["api_key"],
        base_url=ragas_settings["base_url"],
        check_embedding_ctx_length=False,
    )
    return LangchainEmbeddingsWrapper(embeddings)


async def _generate_answer(question: str, *, session_id: str) -> str:
    """Generate an answer via the knowledge-base Q&A expert (RAG path).

    The standalone RAG agent service was removed together with its duplicate
    conversation stack; the knowledge expert is now the canonical RAG Q&A path.
    """
    from app.agent.experts.knowledge import knowledge_expert

    parts: list[str] = []
    async for event in knowledge_expert.run(
        message=question, session_id=session_id, trace_id=session_id
    ):
        if event.get("type") == "content":
            parts.append(str(event.get("data") or ""))
    return "".join(parts)


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
                    case.question, session_id=f"ragas-eval-{index}"
                )

            rows.append(
                build_ragas_row(
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


def add_ragas_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _install_ragas_vertexai_import_shim()
    ragas_settings = configure_ragas_openai_env()
    try:
        from datasets import Dataset
        from openai import OpenAI
        from ragas import evaluate
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
    except ImportError as exc:
        raise RuntimeError(
            "RAGAS dependencies are not installed. Install dev dependencies first, "
            "for example: uv sync --extra dev"
        ) from exc

    dataset = Dataset.from_list(
        [
            {
                "user_input": row["user_input"],
                "response": row["response"],
                "retrieved_contexts": row["retrieved_contexts"],
                "reference": row["reference"],
            }
            for row in rows
        ]
    )
    client = (
        OpenAI(api_key=ragas_settings["api_key"], base_url=ragas_settings["base_url"])
        if ragas_settings["api_key"]
        else None
    )
    llm = create_ragas_llm(ragas_settings) if client else None
    embeddings = create_ragas_embeddings(ragas_settings) if client else None
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings,
    )
    score_rows = result.to_pandas().to_dict(orient="records")
    return [{**row, **score_row} for row, score_row in zip(rows, score_rows, strict=True)]


def _install_ragas_vertexai_import_shim() -> None:
    """Keep RAGAS importable with langchain-community versions that removed this module."""

    module_name = "langchain_community.chat_models.vertexai"
    if module_name in sys.modules:
        return

    module = types.ModuleType(module_name)

    class ChatVertexAI:  # pragma: no cover - only used if RAGAS explicitly instantiates VertexAI
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ChatVertexAI is not configured for this evaluation pipeline")

    module.ChatVertexAI = ChatVertexAI
    sys.modules[module_name] = module


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
    if not args.skip_generation:
        write_csv(rows, output_path)
        try:
            rows = add_ragas_scores(rows)
        except Exception as exc:
            for row in rows:
                row["ragas_error"] = f"{type(exc).__name__}: {exc}"
            write_csv(rows, output_path)
            raise
    write_csv(rows, output_path)
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
