import importlib
import json
import sys
import types

import pytest


def test_rebuild_vector_collection_script_requires_yes():
    module = importlib.import_module("scripts.rebuild_vector_collection")

    with pytest.raises(SystemExit) as exc_info:
        module.main([])

    assert exc_info.value.code == 2


def test_rebuild_vector_collection_script_calls_rebuild_service(monkeypatch, capsys):
    module = importlib.import_module("scripts.rebuild_vector_collection")
    calls = []

    class FakeService:
        def rebuild_collection_and_reindex(self, directory_path, confirm):
            calls.append((directory_path, confirm))
            return types.SimpleNamespace(to_dict=lambda: {"success": True, "directory_path": directory_path})

    fake_service_module = types.ModuleType("app.services.vector_index_service")
    fake_service_module.vector_index_service = FakeService()
    monkeypatch.setitem(sys.modules, "app.services.vector_index_service", fake_service_module)

    exit_code = module.main(["--yes", "--directory", "aiops-docs"])

    assert exit_code == 0
    assert calls == [("aiops-docs", True)]
    assert json.loads(capsys.readouterr().out) == {
        "success": True,
        "directory_path": "aiops-docs",
    }
