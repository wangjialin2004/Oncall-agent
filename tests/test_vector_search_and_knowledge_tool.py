import importlib

import pytest
from langchain_core.documents import Document
from pymilvus import DataType, FunctionType


class _FakeEntity:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class _FakeHit:
    def __init__(self, values, distance):
        self.entity = _FakeEntity(values)
        self.distance = distance


class _FakeCollection:
    def __init__(self):
        self.search_call = None
        self.hybrid_search_call = None

    def search(self, **kwargs):
        self.search_call = kwargs
        return [
            [
                _FakeHit(
                    {
                        "id": "doc-bm25",
                        "content": "BM25 matched HighCPUUsage",
                        "metadata": {"source": "alerts.md"},
                    },
                    1.25,
                )
            ]
        ]

    def hybrid_search(self, **kwargs):
        self.hybrid_search_call = kwargs
        return [
            [
                _FakeHit(
                    {
                        "id": "doc-hybrid",
                        "content": "Hybrid matched HighCPUUsage",
                        "metadata": {"source": "runbook.md"},
                    },
                    0.88,
                )
            ]
        ]


def test_search_result_exposes_standard_fields(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-api-key")
    module = importlib.import_module("app.services.vector_search_service")

    result = module.SearchResult(
        id="doc-1",
        content="CPU troubleshooting",
        score=0.25,
        source="cpu_high_usage.md",
        metadata={"h1": "CPU"},
        retrieval_type="dense",
        rank=1,
    )

    assert result.to_dict() == {
        "id": "doc-1",
        "content": "CPU troubleshooting",
        "score": 0.25,
        "source": "cpu_high_usage.md",
        "metadata": {"h1": "CPU"},
        "retrieval_type": "dense",
        "rank": 1,
    }


def test_vector_search_dispatches_to_configured_hybrid_mode(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-api-key")
    module = importlib.import_module("app.services.vector_search_service")
    service = module.VectorSearchService()
    expected = [
        module.SearchResult(
            id="doc-1",
            content="hybrid result",
            score=0.9,
            source="source.md",
            metadata={},
            retrieval_type="hybrid",
            rank=1,
        )
    ]
    calls = []

    def fake_hybrid(query, top_k):
        calls.append((query, top_k))
        return expected

    monkeypatch.setattr(module.config, "rag_retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(service, "search_hybrid_documents", fake_hybrid, raising=False)
    monkeypatch.setattr(
        service,
        "search_similar_documents",
        lambda query, top_k: (_ for _ in ()).throw(AssertionError("dense path not expected")),
    )

    results = service.search("HighCPUUsage", top_k=5)

    assert calls == [("HighCPUUsage", 5)]
    assert results == expected


def test_vector_search_rejects_blank_query(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-api-key")
    module = importlib.import_module("app.services.vector_search_service")
    service = module.VectorSearchService()

    with pytest.raises(ValueError, match="查询文本不能为空"):
        service.search("   ")


def test_milvus_hybrid_schema_includes_bm25_sparse_fields(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-api-key")
    module = importlib.import_module("app.core.milvus_client")
    manager = module.MilvusClientManager()

    monkeypatch.setattr(module.config, "rag_retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(module.config, "rag_dense_vector_field", "dense_vector", raising=False)
    monkeypatch.setattr(module.config, "rag_sparse_vector_field", "sparse_vector", raising=False)

    schema = manager._build_collection_schema()
    fields = {field.name: field for field in schema.fields}

    assert fields["dense_vector"].dtype == DataType.FLOAT_VECTOR
    assert fields["dense_vector"].params["dim"] == manager.VECTOR_DIM
    assert fields["sparse_vector"].dtype == DataType.SPARSE_FLOAT_VECTOR
    assert fields["content"].params["enable_analyzer"] is True
    assert schema.functions[0].type == FunctionType.BM25
    assert schema.functions[0].input_field_names == ["content"]
    assert schema.functions[0].output_field_names == ["sparse_vector"]


def test_bm25_search_uses_sparse_field_and_parses_results(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-api-key")
    module = importlib.import_module("app.services.vector_search_service")
    service = module.VectorSearchService()
    collection = _FakeCollection()

    monkeypatch.setattr(module.config, "rag_sparse_vector_field", "sparse_vector")
    monkeypatch.setattr(module.milvus_manager, "get_collection", lambda: collection)

    results = service.search_bm25_documents("HighCPUUsage", top_k=2)

    assert collection.search_call["data"] == ["HighCPUUsage"]
    assert collection.search_call["anns_field"] == "sparse_vector"
    assert collection.search_call["param"]["metric_type"] == "BM25"
    assert results[0].id == "doc-bm25"
    assert results[0].source == "alerts.md"
    assert results[0].retrieval_type == "bm25"


def test_hybrid_search_builds_dense_and_sparse_requests(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-api-key")
    module = importlib.import_module("app.services.vector_search_service")
    service = module.VectorSearchService()
    collection = _FakeCollection()

    monkeypatch.setattr(module.config, "rag_dense_vector_field", "dense_vector")
    monkeypatch.setattr(module.config, "rag_sparse_vector_field", "sparse_vector")
    monkeypatch.setattr(module.config, "rag_dense_weight", 0.6)
    monkeypatch.setattr(module.config, "rag_bm25_weight", 0.4)
    monkeypatch.setattr(module.milvus_manager, "get_collection", lambda: collection)
    monkeypatch.setattr(service, "_embed_query", lambda query: [0.1, 0.2])

    results = service.search_hybrid_documents("HighCPUUsage", top_k=2)

    dense_request, sparse_request = collection.hybrid_search_call["reqs"]
    rerank = collection.hybrid_search_call["rerank"]
    assert dense_request.anns_field == "dense_vector"
    assert dense_request.data == [[0.1, 0.2]]
    assert sparse_request.anns_field == "sparse_vector"
    assert sparse_request.data == ["HighCPUUsage"]
    assert rerank.dict()["params"]["weights"] == [0.6, 0.4]
    assert results[0].id == "doc-hybrid"
    assert results[0].source == "runbook.md"
    assert results[0].retrieval_type == "hybrid"


def test_knowledge_tool_formats_vector_search_results(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-api-key")
    search_module = importlib.import_module("app.services.vector_search_service")
    knowledge_module = importlib.import_module("app.tools.knowledge_tool")

    results = [
        search_module.SearchResult(
            id="doc-1",
            content="Check CPU saturation and runaway processes.",
            score=0.12,
            source="cpu_high_usage.md",
            metadata={"h1": "CPU", "h2": "Troubleshooting"},
            retrieval_type="dense",
            rank=1,
        )
    ]

    calls = []

    def fake_search(query, top_k):
        calls.append((query, top_k))
        return results

    monkeypatch.setattr(knowledge_module.vector_search_service, "search", fake_search)

    context, docs = knowledge_module.retrieve_knowledge.func("high cpu")

    assert calls == [("high cpu", knowledge_module.config.rag_top_k)]
    assert "参考资料 1" in context
    assert "检索方式: dense" in context
    assert docs == [
        Document(
            page_content="Check CPU saturation and runaway processes.",
            metadata={
                "id": "doc-1",
                "score": 0.12,
                "source": "cpu_high_usage.md",
                "retrieval_type": "dense",
                "rank": 1,
                "h1": "CPU",
                "h2": "Troubleshooting",
            },
        )
    ]
