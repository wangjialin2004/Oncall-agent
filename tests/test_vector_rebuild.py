import importlib
import sys
import types

import pytest


def test_milvus_rebuild_collection_requires_explicit_confirmation():
    module = importlib.import_module("app.core.milvus_client")
    manager = module.MilvusClientManager()

    with pytest.raises(ValueError, match="confirm=True"):
        manager.rebuild_collection()


def test_milvus_rebuild_collection_drops_and_recreates_existing_collection(monkeypatch):
    module = importlib.import_module("app.core.milvus_client")
    manager = module.MilvusClientManager()
    manager._client = object()
    calls = []

    class FakeCollection:
        def release(self):
            calls.append("release")

    manager._collection = FakeCollection()

    monkeypatch.setattr(manager, "_collection_exists", lambda: True)
    monkeypatch.setattr(module.utility, "drop_collection", lambda name: calls.append(("drop", name)))
    monkeypatch.setattr(manager, "_create_collection", lambda: calls.append("create"))
    monkeypatch.setattr(manager, "_load_collection", lambda: calls.append("load"))

    manager.rebuild_collection(confirm=True)

    assert calls == ["release", ("drop", manager.COLLECTION_NAME), "create", "load"]


def test_vector_index_service_rebuilds_refreshes_store_and_reindexes(monkeypatch):
    fake_store_module = types.ModuleType("app.services.vector_store_manager")
    fake_store_module.vector_store_manager = types.SimpleNamespace(reinitialize=lambda: None)
    monkeypatch.setitem(sys.modules, "app.services.vector_store_manager", fake_store_module)
    original_module = sys.modules.pop("app.services.vector_index_service", None)

    try:
        module = importlib.import_module("app.services.vector_index_service")
        service = module.VectorIndexService()
        result = module.IndexingResult()
        calls = []

        monkeypatch.setattr(
            module.milvus_manager,
            "rebuild_collection",
            lambda confirm: calls.append(("rebuild", confirm)),
        )
        monkeypatch.setattr(
            module.vector_store_manager,
            "reinitialize",
            lambda: calls.append("reinitialize"),
        )
        monkeypatch.setattr(
            service,
            "index_directory",
            lambda directory_path: calls.append(("index", directory_path)) or result,
        )

        actual = service.rebuild_collection_and_reindex("aiops-docs", confirm=True)

        assert actual is result
        assert calls == [("rebuild", True), "reinitialize", ("index", "aiops-docs")]
    finally:
        sys.modules.pop("app.services.vector_index_service", None)
        if original_module is not None:
            sys.modules["app.services.vector_index_service"] = original_module
