import json
from datetime import datetime

import pytest

from app.evaluation.ragas_pipeline import (
    RagEvalCase,
    build_ragas_row,
    default_output_path,
    load_cases,
)
from scripts.evaluate_rag_ragas import (
    DashScopeCompatibleChatOpenAI,
    EvaluationPreflightError,
    build_retrieval_trace,
    configure_ragas_openai_env,
    create_ragas_embeddings,
    create_ragas_llm,
    ensure_retrieval_dependencies_available,
)


def test_load_cases_reads_jsonl(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "question": "CPU 使用率过高如何排查？",
                        "ground_truth": "检查高 CPU 进程、负载、日志和近期发布。",
                        "expected_sources": ["cpu_high_usage.md"],
                    },
                    ensure_ascii=False,
                ),
                "",
                json.dumps(
                    {
                        "question": "磁盘空间不足怎么办？",
                        "ground_truth": "定位大文件，清理无用日志，必要时扩容。",
                        "expected_sources": ["disk_high_usage.md"],
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )

    cases = load_cases(cases_path)

    assert cases == [
        RagEvalCase(
            question="CPU 使用率过高如何排查？",
            ground_truth="检查高 CPU 进程、负载、日志和近期发布。",
            expected_sources=["cpu_high_usage.md"],
        ),
        RagEvalCase(
            question="磁盘空间不足怎么办？",
            ground_truth="定位大文件，清理无用日志，必要时扩容。",
            expected_sources=["disk_high_usage.md"],
        ),
    ]


def test_load_cases_rejects_missing_required_fields(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps({"question": "缺少标准答案"}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ground_truth"):
        load_cases(cases_path)


def test_build_ragas_row_preserves_contexts_and_sources():
    case = RagEvalCase(
        question="服务不可用如何排查？",
        ground_truth="检查端口、健康检查、日志和依赖服务。",
        expected_sources=["service_unavailable.md"],
    )

    row = build_ragas_row(
        case=case,
        response="先检查健康检查和端口，再查看错误日志。",
        retrieved_contexts=["服务不可用排查步骤", "检查端口是否监听"],
        retrieved_sources=["service_unavailable.md", "runbook.md"],
    )

    assert row == {
        "user_input": "服务不可用如何排查？",
        "response": "先检查健康检查和端口，再查看错误日志。",
        "retrieved_contexts": ["服务不可用排查步骤", "检查端口是否监听"],
        "reference": "检查端口、健康检查、日志和依赖服务。",
        "expected_sources": ["service_unavailable.md"],
        "retrieved_sources": ["service_unavailable.md", "runbook.md"],
    }


def test_build_ragas_row_can_include_chunk_trace_fields():
    case = RagEvalCase(
        question="How should high CPU be handled?",
        ground_truth="Check high CPU processes and recent releases.",
        expected_sources=["cpu_high_usage.md"],
    )

    row = build_ragas_row(
        case=case,
        response="Check processes first.",
        retrieved_contexts=["CPU runbook chunk"],
        retrieved_sources=["cpu_high_usage.md"],
        retrieved_chunk_ids=["abc123:0"],
        retrieved_chunk_indices=[0],
        retrieved_scores=[0.12],
        retrieved_ranks=[1],
        retrieved_heading_paths=["CPU > Triage"],
        retrieved_content_lengths=[512],
    )

    assert row["retrieved_contexts"] == ["CPU runbook chunk"]
    assert row["retrieved_sources"] == ["cpu_high_usage.md"]
    assert row["retrieved_chunk_ids"] == ["abc123:0"]
    assert row["retrieved_chunk_indices"] == [0]
    assert row["retrieved_scores"] == [0.12]
    assert row["retrieved_ranks"] == [1]
    assert row["retrieved_heading_paths"] == ["CPU > Triage"]
    assert row["retrieved_content_lengths"] == [512]


def test_build_retrieval_trace_extracts_chunk_metadata():
    class FakeSearchResult:
        id = "milvus-id-1"
        score = 0.12
        rank = 1
        metadata = {
            "chunk_id": "abc123:0",
            "chunk_index": 0,
            "heading_path": "CPU > Triage",
            "content_length": 512,
        }

    trace = build_retrieval_trace([FakeSearchResult()])

    assert trace == {
        "retrieved_chunk_ids": ["abc123:0"],
        "retrieved_chunk_indices": [0],
        "retrieved_scores": [0.12],
        "retrieved_ranks": [1],
        "retrieved_heading_paths": ["CPU > Triage"],
        "retrieved_content_lengths": [512],
    }


def test_default_output_path_uses_timestamp():
    now = datetime(2026, 6, 9, 22, 30, 5)

    path = default_output_path(now=now)

    assert path.as_posix().endswith("evals/results/ragas_20260609_223005.csv")


def test_preflight_reports_clear_error_when_milvus_is_unavailable():
    class BrokenMilvusManager:
        def connect(self):
            raise RuntimeError("connection refused")

    with pytest.raises(EvaluationPreflightError, match="Milvus unavailable.*connection refused"):
        ensure_retrieval_dependencies_available(BrokenMilvusManager())


def test_preflight_reports_clear_error_when_milvus_health_check_fails():
    class UnhealthyMilvusManager:
        def connect(self):
            return None

        def health_check(self):
            return False

    with pytest.raises(EvaluationPreflightError, match="Milvus unavailable.*health_check"):
        ensure_retrieval_dependencies_available(UnhealthyMilvusManager())


def test_configure_ragas_openai_env_uses_dashscope_config(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_BASE", raising=False)
    monkeypatch.delenv("DASHSCOPE_MODEL", raising=False)
    monkeypatch.delenv("DASHSCOPE_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("RAGAS_MODEL", raising=False)
    monkeypatch.delenv("RAGAS_EMBEDDING_MODEL", raising=False)

    class FakeConfig:
        dashscope_api_key = "dashscope-key"
        dashscope_model = "qwen-max"
        dashscope_embedding_model = "text-embedding-v4"

    settings = configure_ragas_openai_env(FakeConfig())

    assert settings == {
        "api_key": "dashscope-key",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "embedding_model": "text-embedding-v4",
    }
    assert "OPENAI_API_KEY" not in settings
    assert settings["api_key"] == "dashscope-key"
    assert settings["base_url"].endswith("/compatible-mode/v1")


def test_create_ragas_llm_wraps_langchain_chat_model():
    llm = create_ragas_llm(
        {
            "api_key": "test-key",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-max",
            "embedding_model": "text-embedding-v4",
        }
    )

    assert llm.__class__.__name__ == "LangchainLLMWrapper"
    assert hasattr(llm.langchain_llm, "agenerate_prompt")
    assert llm.langchain_llm.extra_body == {"enable_thinking": False}


def test_create_ragas_embeddings_wraps_langchain_embeddings():
    embeddings = create_ragas_embeddings(
        {
            "api_key": "test-key",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-max",
            "embedding_model": "text-embedding-v4",
        }
    )

    assert embeddings.__class__.__name__ == "LangchainEmbeddingsWrapper"
    assert hasattr(embeddings.embeddings, "embed_query")
    assert embeddings.embeddings.check_embedding_ctx_length is False


def test_dashscope_compatible_chat_model_flattens_content_parts():
    from langchain_core.messages import HumanMessage, SystemMessage

    model = DashScopeCompatibleChatOpenAI(
        model="qwen-max",
        api_key="test-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    messages = model._normalize_messages_for_dashscope(
        [
            SystemMessage(content=[{"type": "text", "text": "system prompt"}]),
            HumanMessage(
                content=[
                    {"type": "text", "text": "first"},
                    {"type": "input_text", "text": "second"},
                    "third",
                ]
            ),
        ]
    )

    assert messages[0].content == "system prompt"
    assert messages[1].content == "first\nsecond\nthird"
