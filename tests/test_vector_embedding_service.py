"""Unit tests for provider-neutral embedding service (no model download)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import vector_embedding_service as ves


@pytest.fixture(autouse=True)
def _reset_embedding_singleton():
    ves.reset_vector_embedding_service()
    yield
    ves.reset_vector_embedding_service()


def test_import_does_not_require_api_key():
    # Importing the module / proxy must not raise even without DASHSCOPE_API_KEY.
    assert hasattr(ves.vector_embedding_service, "embed_query")
    assert hasattr(ves.vector_embedding_service, "embed_documents")


def test_normalize_provider_aliases():
    assert ves._normalize_provider("bge3") == "local_bge_m3"
    assert ves._normalize_provider("BGE-M3") == "local_bge_m3"
    assert ves._normalize_provider("dashscope") == "dashscope"


def test_new_local_bge_service_is_lazy():
    settings = SimpleNamespace(
        embedding_provider="local_bge_m3",
        embedding_model="BAAI/bge-m3",
        embedding_model_path="",
        embedding_dim=1024,
        embedding_device="cpu",
        embedding_batch_size=4,
        embedding_normalize=True,
        embedding_use_fp16=False,
    )
    service = ves.new_embedding_service(settings=settings)
    assert isinstance(service, ves.LocalBgeM3Embeddings)
    assert service.dimensions == 1024
    # Construction must not load FlagEmbedding / torch.
    assert service._model is None


def test_dashscope_requires_api_key():
    settings = SimpleNamespace(
        embedding_provider="dashscope",
        embedding_model="text-embedding-v4",
        embedding_dim=1024,
        dashscope_api_key="",
        dashscope_embedding_model="text-embedding-v4",
        embedding_dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        ves.new_embedding_service(settings=settings)


def test_local_embed_uses_flagembedding_mock(monkeypatch):
    class FakeModel:
        def encode(self, texts, **kwargs):
            return {
                "dense_vecs": [[0.3, 0.4, 0.0] for _ in texts],
            }

    def fake_load(self):
        self._model = FakeModel()
        return self._model

    monkeypatch.setattr(ves.LocalBgeM3Embeddings, "_load_model", fake_load)
    service = ves.LocalBgeM3Embeddings(
        model_name="fake",
        dimensions=3,
        normalize=True,
        use_fp16=False,
    )
    docs = service.embed_documents(["a", "b"])
    assert len(docs) == 2
    # L2-normalized [0.3, 0.4, 0] → [0.6, 0.8, 0]
    assert docs[0][0] == pytest.approx(0.6)
    assert docs[0][1] == pytest.approx(0.8)
    query = service.embed_query("hello")
    assert query[0] == pytest.approx(0.6)


def test_lazy_proxy_delegates(monkeypatch):
    class FakeService:
        dimensions = 8

        def embed_documents(self, texts):
            return [[1.0] * 8 for _ in texts]

        def embed_query(self, text):
            return [1.0] * 8

    monkeypatch.setattr(ves, "get_vector_embedding_service", lambda **kwargs: FakeService())
    assert ves.vector_embedding_service.dimensions == 8
    assert ves.vector_embedding_service.embed_query("x") == [1.0] * 8
    assert ves.vector_embedding_service.embed_documents(["a"]) == [[1.0] * 8]


def test_unknown_provider_raises():
    settings = SimpleNamespace(
        embedding_provider="nope",
        embedding_model="x",
        embedding_dim=1024,
        embedding_model_path="",
        embedding_device="",
        embedding_batch_size=12,
        embedding_normalize=True,
        embedding_use_fp16=True,
        dashscope_api_key="",
        dashscope_embedding_model="",
        embedding_dashscope_base_url="",
    )
    with pytest.raises(ValueError, match="未知 EMBEDDING_PROVIDER"):
        ves.new_embedding_service(settings=settings)
