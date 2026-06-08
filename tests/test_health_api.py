import json

import pytest

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


@pytest.mark.asyncio
async def test_health_check_returns_healthy_http_envelope(monkeypatch):
    monkeypatch.setattr(
        health,
        "build_health_data",
        lambda: {"service": "test", "version": "1.0", "status": "healthy"},
    )

    response = await health.health_check()
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body == {
        "code": 200,
        "message": "服务运行正常",
        "data": {"service": "test", "version": "1.0", "status": "healthy"},
    }


@pytest.mark.asyncio
async def test_health_check_returns_unhealthy_http_envelope(monkeypatch):
    monkeypatch.setattr(
        health,
        "build_health_data",
        lambda: {
            "service": "test",
            "version": "1.0",
            "status": "unhealthy",
            "error": "数据库不可用",
        },
    )

    response = await health.health_check()
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body == {
        "code": 503,
        "message": "服务不可用",
        "data": {
            "service": "test",
            "version": "1.0",
            "status": "unhealthy",
            "error": "数据库不可用",
        },
    }
