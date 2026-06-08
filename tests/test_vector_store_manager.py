import importlib
import sys
import types


def test_delete_by_source_escapes_path_for_milvus_expression(monkeypatch):
    fake_embedding_module = types.ModuleType("app.services.vector_embedding_service")
    fake_embedding_module.vector_embedding_service = object()
    monkeypatch.setitem(sys.modules, "app.services.vector_embedding_service", fake_embedding_module)
    original_module = sys.modules.pop("app.services.vector_store_manager", None)

    try:
        module = importlib.import_module("app.services.vector_store_manager")
        manager = module.VectorStoreManager()
        expressions = []

        class FakeDeleteResult:
            delete_count = 1

        class FakeCollection:
            def delete(self, expr):
                expressions.append(expr)
                return FakeDeleteResult()

        monkeypatch.setattr(module.milvus_manager, "get_collection", lambda: FakeCollection())

        deleted_count = manager.delete_by_source('C:/uploads/runbook "prod" \\ cpu.md')

        assert deleted_count == 1
        assert expressions == [
            'metadata["_source"] == "C:/uploads/runbook \\"prod\\" \\\\ cpu.md"'
        ]
    finally:
        sys.modules.pop("app.services.vector_store_manager", None)
        if original_module is not None:
            sys.modules["app.services.vector_store_manager"] = original_module
