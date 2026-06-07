from app.api import health


def test_build_health_data_includes_core_dependencies(monkeypatch):
    monkeypatch.setattr(health.milvus_manager, "health_check", lambda: True)
    monkeypatch.setattr(health, "_port_reachable", lambda _url: False)
    monkeypatch.setattr(health.config, "dashscope_api_key", "test-api-key")

    data = health.build_health_data()

    assert data["milvus"]["status"] == "connected"
    assert data["mcp"]["cls"]["status"] == "unreachable"
    assert data["mcp"]["monitor"]["url"] == health.config.mcp_monitor_url
    assert data["llm"]["status"] == "configured"
    assert data["rag"]["collection_name"] == health.milvus_manager.COLLECTION_NAME
    assert data["status"] == "healthy"
