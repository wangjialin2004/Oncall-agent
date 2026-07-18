from __future__ import annotations

import pytest

from app.config import config
from app.core.request_context import RequestContext
from app.services.rag_scope import RagScopeError, build_scope_filter, scope_fields
from scripts.migrate_rag_scope import _row_scope


def _context(owner: str = "owner-a", project: str = "project-a") -> RequestContext:
    return RequestContext(
        owner_key=owner,
        storage_owner_key="legacy",
        project_id=project,
        role="operator",
        session_id="session-1",
        trace_id="trace-1",
    )


def test_scope_filter_contains_only_system_project_and_current_owner() -> None:
    expression = build_scope_filter(_context())
    assert expression is not None
    assert 'scope_type == "system"' in expression
    assert 'scope_id == "project-a"' in expression
    assert 'scope_id in ["owner-a", "legacy"]' in expression
    assert "owner-b" not in expression


def test_scope_filter_escapes_expression_literals() -> None:
    expression = build_scope_filter(_context(owner='owner\\"-a'))
    assert expression is not None
    assert 'owner\\\\\\"-a' in expression


def test_scope_is_fail_closed_without_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "rag_tenant_scope_enabled", True)
    with pytest.raises(RagScopeError):
        build_scope_filter(None)


def test_scope_can_be_disabled_only_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "rag_tenant_scope_enabled", False)
    assert build_scope_filter(None) is None


def test_scope_fields_reject_unknown_or_invalid_values() -> None:
    assert scope_fields() == {"scope_type": "system", "scope_id": "system"}
    assert scope_fields(scope_type="user", scope_id="owner-a") == {
        "scope_type": "user",
        "scope_id": "owner-a",
    }
    with pytest.raises(RagScopeError):
        scope_fields(scope_type="project", scope_id="")
    with pytest.raises(RagScopeError):
        scope_fields(scope_type="unknown", scope_id="x")


def test_legacy_scope_reads_json_metadata_without_guessing() -> None:
    assert _row_scope({"id": "1", "metadata": '{"scope_type":"project","scope_id":"p1"}'}) == (
        "project",
        "p1",
    )
    assert _row_scope({"id": "2", "metadata": '{"owner":"user-a"}'}) is None


def test_milvus_schema_declares_scope_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.milvus_client import MilvusClientManager

    monkeypatch.setattr(config, "rag_retrieval_mode", "dense")
    schema = MilvusClientManager()._build_collection_schema()
    assert {field.name for field in schema.fields} >= {"scope_type", "scope_id"}


def test_dense_search_passes_authenticated_scope_to_milvus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import vector_search_service as module

    class FakeCollection:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def search(self, **kwargs: object) -> list[object]:
            self.kwargs = kwargs
            return []

    collection = FakeCollection()
    service = module.VectorSearchService()
    monkeypatch.setattr(config, "rag_tenant_scope_enabled", True)
    monkeypatch.setattr(service, "_embed_query", lambda _query: [0.1, 0.2])
    monkeypatch.setattr(module.milvus_manager, "get_collection", lambda: collection)

    assert service.search_similar_documents("cpu", context=_context()) == []
    expression = str(collection.kwargs["expr"])
    assert 'scope_id == "project-a"' in expression
    assert 'scope_id in ["owner-a", "legacy"]' in expression


def test_search_without_context_fails_before_embedding_or_milvus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import vector_search_service as module

    service = module.VectorSearchService()
    monkeypatch.setattr(config, "rag_tenant_scope_enabled", True)
    monkeypatch.setattr(
        service,
        "_embed_query",
        lambda _query: pytest.fail("embedding should not run without tenant scope"),
    )
    monkeypatch.setattr(
        module.milvus_manager,
        "get_collection",
        lambda: pytest.fail("Milvus should not run without tenant scope"),
    )

    with pytest.raises(RuntimeError, match="request context is required"):
        service.search_similar_documents("cpu")
