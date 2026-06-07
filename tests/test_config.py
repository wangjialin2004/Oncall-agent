import importlib
import sys


def test_settings_treats_release_debug_value_as_false(monkeypatch):
    monkeypatch.setenv("DEBUG", "release")
    for module_name in ["app.config", "app.utils.logger", "app.utils", "app"]:
        sys.modules.pop(module_name, None)

    config_module = importlib.import_module("app.config")

    assert config_module.config.debug is False


def test_rag_hybrid_retrieval_config_defaults(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    for module_name in ["app.config", "app.utils.logger", "app.utils", "app"]:
        sys.modules.pop(module_name, None)

    config_module = importlib.import_module("app.config")

    assert config_module.config.rag_retrieval_mode == "dense"
    assert config_module.config.rag_dense_weight == 0.7
    assert config_module.config.rag_bm25_weight == 0.3
    assert config_module.config.rag_dense_vector_field == "vector"
    assert config_module.config.rag_sparse_vector_field == "sparse_vector"
