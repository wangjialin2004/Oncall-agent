"""Generate local RAG evaluation cases from chunks stored in Milvus.

The generated JSONL keeps the existing evaluation contract:
question, ground_truth, and expected_sources. Extra chunk trace fields are
included for debugging, but the current loader can ignore them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


QUESTION_TEMPLATES = [
    "{topic}中，{focus}应该如何处理？",
    "遇到{topic}时，{focus}需要检查哪些内容？",
    "{topic}场景下，{focus}的关键步骤是什么？",
    "关于{topic}，{focus}有哪些判断依据？",
    "{topic}处理中，{focus}应采取哪些措施？",
    "如何根据{focus}分析{topic}问题？",
    "{topic}的{focus}部分有哪些注意点？",
    "{topic}发生后，{focus}应该怎么验证？",
    "{topic}相关的{focus}需要查询什么信息？",
    "{topic}处置中，{focus}的后续动作是什么？",
]

TOPIC_BY_SOURCE = {
    "cpu_high_usage.md": "CPU 使用率过高",
    "memory_high_usage.md": "内存使用率过高",
    "disk_high_usage.md": "磁盘空间不足",
    "service_unavailable.md": "服务不可用",
    "slow_response.md": "接口响应变慢",
}


def normalize_text(text: str) -> str:
    """Convert markdown-ish chunk text to compact plain text."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"`([^`]*)`", r"\1", normalized)
    normalized = re.sub(r"\*\*([^*]+)\*\*", r"\1", normalized)
    normalized = re.sub(r"!\[[^\]]*]\([^)]+\)", "", normalized)
    normalized = re.sub(r"\[[^\]]*]\([^)]+\)", "", normalized)
    normalized = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"^[ \t]*[-*+][ \t]+", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"^[ \t]*\d+[.)][ \t]+", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"[>|]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def source_name_from_metadata(metadata: dict[str, Any]) -> str:
    """Return a stable source name from chunk metadata."""

    source = (
        metadata.get("file_name")
        or metadata.get("_file_name")
        or metadata.get("source")
        or metadata.get("_source")
        or "unknown"
    )
    return Path(str(source)).name


def topic_from_source(source_name: str) -> str:
    if source_name in TOPIC_BY_SOURCE:
        return TOPIC_BY_SOURCE[source_name]
    stem = Path(source_name).stem.replace("_", " ").replace("-", " ").strip()
    return stem or "知识库内容"


def focus_from_chunk(content: str, metadata: dict[str, Any]) -> str:
    heading_path = str(metadata.get("heading_path") or "").strip()
    if heading_path:
        return heading_path.split(">")[-1].strip()

    for line in content.splitlines():
        match = re.match(r"^[ \t]*#{1,6}[ \t]+(.+)$", line)
        if match:
            return normalize_text(match.group(1))[:30]
    return "该知识片段"


def content_focuses(content: str, *, limit: int = 6) -> list[str]:
    """Extract question focuses from the chunk body itself."""

    focuses: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        heading_match = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading_match:
            focuses.append(normalize_text(heading_match.group(1)))
            continue

        bold_match = re.match(r"^\*\*([^*：:]+)[：:]?\*\*", stripped)
        if bold_match:
            focuses.append(normalize_text(bold_match.group(1)))
            continue

        label_match = re.match(r"^-?\s*\*\*([^*]+)\*\*\s*[：:]?", stripped)
        if label_match:
            focuses.append(normalize_text(label_match.group(1)))
            continue

        list_match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.+)$", stripped)
        if list_match:
            candidate = normalize_text(list_match.group(1))
            if 4 <= len(candidate) <= 32:
                focuses.append(candidate)

    unique_focuses: list[str] = []
    seen: set[str] = set()
    for focus in focuses:
        focus = focus.strip(" ：:，,。")
        if not focus or focus in seen:
            continue
        seen.add(focus)
        unique_focuses.append(focus)
        if len(unique_focuses) >= limit:
            break
    return unique_focuses


def question_from_content(content: str, *, topic: str, variant: int) -> str:
    """Generate a question anchored to this chunk's body content."""

    focuses = content_focuses(content)
    if focuses:
        primary = focuses[0]
        if len(focuses) > 1:
            secondary = focuses[1 + (variant % (len(focuses) - 1))]
            focus = primary if secondary == primary else f"{primary}中的{secondary}"
        else:    
            focus = primary
    else:
        words = normalize_text(content).split()
        focus = "".join(words[:12])[:32] or "该知识片段"

    template = QUESTION_TEMPLATES[variant % len(QUESTION_TEMPLATES)]
    return template.format(topic=topic, focus=focus)


def reference_from_content(content: str, max_chars: int) -> str:
    """Use the chunk itself as the reference answer, trimmed to a useful size."""

    normalized = normalize_text(content)
    if len(normalized) <= max_chars:
        return normalized

    cut = normalized[:max_chars]
    sentence_end = max(cut.rfind("。"), cut.rfind("；"), cut.rfind("."), cut.rfind(";"))
    if sentence_end >= max_chars // 2:
        return cut[: sentence_end + 1].strip()
    return cut.rstrip(" ，,、") + "..."


def build_case_from_chunk(chunk: dict[str, Any], case_index: int, max_reference_chars: int) -> dict[str, Any]:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    content = str(chunk.get("content") or "")
    source_name = source_name_from_metadata(metadata)
    topic = topic_from_source(source_name)

    return {
        "question": question_from_content(content, topic=topic, variant=case_index),
        "ground_truth": reference_from_content(content, max_reference_chars),
        "expected_sources": [source_name],
        "expected_chunk_ids": [str(metadata.get("chunk_id") or chunk.get("id") or "")],
        "expected_chunk_indices": [metadata.get("chunk_index")],
        "expected_heading_paths": [str(metadata.get("heading_path") or "")],
    }


def build_cases_from_chunks(
    chunks: list[dict[str, Any]],
    *,
    limit: int,
    max_reference_chars: int = 700,
) -> list[dict[str, Any]]:
    """Build exactly limit cases, cycling chunks when the DB has fewer chunks."""

    usable_chunks = [chunk for chunk in chunks if normalize_text(str(chunk.get("content") or ""))]
    if not usable_chunks:
        raise ValueError("No usable chunks found in vector database")

    sorted_chunks = sorted(
        usable_chunks,
        key=lambda chunk: (
            source_name_from_metadata(chunk.get("metadata") or {}),
            (chunk.get("metadata") or {}).get("chunk_index") or 0,
            str((chunk.get("metadata") or {}).get("chunk_id") or chunk.get("id") or ""),
        ),
    )
    return [
        build_case_from_chunk(
            sorted_chunks[index % len(sorted_chunks)],
            case_index=index,
            max_reference_chars=max_reference_chars,
        )
        for index in range(limit)
    ]


def fetch_chunks_from_milvus(chunk_limit: int) -> list[dict[str, Any]]:
    from app.core.milvus_client import milvus_manager

    milvus_manager.connect()
    try:
        collection = milvus_manager.get_collection()
        rows = collection.query(
            expr="",
            output_fields=["id", "content", "metadata"],
            limit=chunk_limit,
        )
        return [dict(row) for row in rows]
    finally:
        milvus_manager.close()


def write_jsonl(cases: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate local RAG evaluation cases from Milvus chunks.")
    parser.add_argument("--output", default="evals/rag_cases_vector_100.jsonl")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--chunk-limit", type=int, default=1000)
    parser.add_argument("--max-reference-chars", type=int, default=700)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit <= 0:
        raise ValueError("--limit must be greater than 0")
    if args.chunk_limit <= 0:
        raise ValueError("--chunk-limit must be greater than 0")

    chunks = fetch_chunks_from_milvus(args.chunk_limit)
    cases = build_cases_from_chunks(
        chunks,
        limit=args.limit,
        max_reference_chars=args.max_reference_chars,
    )
    output_path = Path(args.output)
    write_jsonl(cases, output_path)
    print(f"Wrote {len(cases)} case(s) from {len(chunks)} chunk(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
