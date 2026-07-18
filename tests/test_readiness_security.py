from app.api.health import readiness_issues
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
