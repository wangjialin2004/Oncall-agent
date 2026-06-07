from app.api import health


def test_build_health_data_includes_core_dependencies(monkeypatch):
    monkeypatch.setattr(health.milvus_manager, "health_check", lambda: True)
    monkeypatch.setattr(health, "_port_reachable", lambda _url: False)
    monkeypatch.setattr(health.config, "dashscope_api_key", "test-api-key")
    monkeypatch.setattr(health.config, "rag_retrieval_mode", "hybrid")
    monkeypatch.setattr(health.config, "rag_dense_weight", 0.65)
    monkeypatch.setattr(health.config, "rag_bm25_weight", 0.35)
    monkeypatch.setattr(health.config, "monitor_target_mode", "self")
    monkeypatch.setattr(health.config, "log_provider", "local")

    data = health.build_health_data()

    assert data["milvus"]["status"] == "connected"
    assert data["mcp"]["cls"]["status"] == "unreachable"
    assert data["mcp"]["monitor"]["url"] == health.config.mcp_monitor_url
    assert data["llm"]["status"] == "configured"
    assert data["rag"]["collection_name"] == health.milvus_manager.COLLECTION_NAME
    assert data["rag"]["retrieval_mode"] == "hybrid"
    assert data["rag"]["dense_weight"] == 0.65
    assert data["rag"]["bm25_weight"] == 0.35
    assert data["monitor"]["target_mode"] == "self"
    assert data["logs"]["provider"] == "local"
    assert data["status"] == "healthy"
