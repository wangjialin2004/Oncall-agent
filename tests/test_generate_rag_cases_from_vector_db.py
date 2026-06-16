from scripts.generate_rag_cases_from_vector_db import (
    build_cases_from_chunks,
    normalize_text,
    question_from_content,
    source_name_from_metadata,
)


def test_normalize_text_removes_markdown_noise_and_collapses_whitespace():
    text = "# CPU Runbook\n\n- **Check**: `top`\n- Use logs\n"

    assert normalize_text(text) == "CPU Runbook Check: top Use logs"


def test_source_name_from_metadata_prefers_file_name():
    metadata = {"file_name": "cpu_high_usage.md", "_source": "/tmp/other.md"}

    assert source_name_from_metadata(metadata) == "cpu_high_usage.md"


def test_build_cases_from_chunks_repeats_chunks_until_limit():
    chunks = [
        {
            "id": "row-1",
            "content": "# CPU\nCheck process CPU usage and recent releases.",
            "metadata": {
                "file_name": "cpu_high_usage.md",
                "chunk_id": "abc:0",
                "chunk_index": 0,
                "heading_path": "CPU",
            },
        },
        {
            "id": "row-2",
            "content": "# Memory\nCheck memory trend, heap dump, and OOM logs.",
            "metadata": {
                "file_name": "memory_high_usage.md",
                "chunk_id": "def:0",
                "chunk_index": 0,
                "heading_path": "Memory",
            },
        },
    ]

    cases = build_cases_from_chunks(chunks, limit=5)

    assert len(cases) == 5
    assert cases[0]["expected_sources"] == ["cpu_high_usage.md"]
    assert cases[0]["expected_chunk_ids"] == ["abc:0"]
    assert cases[1]["expected_sources"] == ["memory_high_usage.md"]
    assert cases[2]["expected_chunk_ids"] == ["abc:0"]
    assert all(case["question"] for case in cases)
    assert all(case["ground_truth"] for case in cases)


def test_question_from_content_uses_chunk_body_focus():
    content = """
## 紧急处理措施
### 立即操作（5分钟内）
1. 快速清理大日志文件
2. 清理临时文件
3. 如果无法快速清理，立即扩容磁盘
"""

    question = question_from_content(content, topic="磁盘空间不足", variant=3)

    assert "磁盘空间不足" in question
    assert "紧急处理措施" in question
    assert "立即操作" in question or "快速清理" in question
