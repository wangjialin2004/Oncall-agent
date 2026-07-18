from app.api.health import _public_health_data, readiness_issues
from app.config import config


def _healthy_dependencies():
    return {
        "milvus": {"status": "connected"},
        "llm": {"status": "configured"},
    }


def test_non_debug_rejects_default_auth_settings(monkeypatch):
    monkeypatch.setattr(config, "debug", False)
    monkeypatch.setattr(config, "auth_token_secret", "dev-auth-token-secret")
    monkeypatch.setattr(config, "auth_users", "admin:admin")

    issues = readiness_issues(_healthy_dependencies())

    assert "default_auth_secret" in issues
    assert "default_admin_credentials" in issues


def test_non_debug_accepts_injected_auth_settings(monkeypatch):
    monkeypatch.setattr(config, "debug", False)
    monkeypatch.setattr(config, "auth_token_secret", "unit-test-strong-secret")
    monkeypatch.setattr(config, "auth_users", "pilot:unit-test-password")

    assert readiness_issues(_healthy_dependencies()) == []


def test_readiness_reports_dependency_contract(monkeypatch):
    monkeypatch.setattr(config, "debug", True)
    issues = readiness_issues({"milvus": {"status": "disconnected"}, "llm": {"status": "missing"}})

    assert issues == ["milvus_unavailable", "llm_not_configured"]


def test_readiness_reports_enabled_redis_degradation(monkeypatch):
    monkeypatch.setattr(config, "debug", True)
    monkeypatch.setattr(config, "redis_enabled", True)
    monkeypatch.setattr(config, "harness_checkpoint_enabled", True)

    issues = readiness_issues(
        {
            "milvus": {"status": "connected"},
            "llm": {"status": "configured"},
            "redis": {"status": "degraded"},
        }
    )

    assert issues == ["redis_degraded"]


def test_readiness_requires_mcp_only_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "debug", True)
    monkeypatch.setattr(config, "redis_enabled", False)
    monkeypatch.setattr(config, "harness_checkpoint_enabled", False)
    monkeypatch.setattr(config, "harness_mcp_enabled", True)

    issues = readiness_issues(
        {
            "milvus": {"status": "connected"},
            "llm": {"status": "configured"},
            "mcp": {
                "cls": {"status": "unreachable"},
                "monitor": {"status": "reachable"},
            },
        }
    )

    assert issues == ["mcp_unavailable"]


def test_public_health_payload_redacts_topology_and_errors():
    public = _public_health_data(
        {
            "service": "agent",
            "version": "1",
            "milvus": {"status": "error", "message": "secret host:19530"},
            "llm": {"status": "configured", "model": "private-model"},
            "redis": {"status": "degraded", "reason": "ConnectionError"},
            "mcp": {
                "cls": {"status": "reachable", "url": "http://internal:8003/mcp"},
                "monitor": {"status": "unreachable", "url": "http://internal:8004/mcp"},
            },
        },
        ["mcp_unavailable"],
    )

    rendered = str(public)
    assert "internal:8003" not in rendered
    assert "secret host" not in rendered
    assert "private-model" not in rendered
    assert public["mcp"]["monitor"] == {"status": "unreachable"}
