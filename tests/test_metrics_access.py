import pytest
from httpx import ASGITransport, AsyncClient

from app.config import config
from app.core.metrics import metrics_access_allowed


def _scope(*, client=("127.0.0.1", 1234), headers=()):
    return {"type": "http", "client": client, "headers": list(headers)}


def test_metrics_internal_mode_allows_private_client(monkeypatch):
    monkeypatch.setattr(config, "metrics_access_mode", "internal")
    assert metrics_access_allowed(_scope()) is True
    assert metrics_access_allowed(_scope(client=("8.8.8.8", 1234))) is False


def test_metrics_bearer_mode_requires_dedicated_token(monkeypatch):
    monkeypatch.setattr(config, "metrics_access_mode", "bearer")
    monkeypatch.setattr(config, "metrics_bearer_token", "metrics-test-token")
    assert metrics_access_allowed(_scope()) is False
    assert metrics_access_allowed(
        _scope(headers=((b"authorization", b"Bearer metrics-test-token"),))
    ) is True
    assert metrics_access_allowed(
        _scope(headers=((b"authorization", b"Bearer wrong"),))
    ) is False


def test_metrics_public_mode_requires_debug_degradation_switch(monkeypatch):
    monkeypatch.setattr(config, "metrics_access_mode", "public")
    monkeypatch.setattr(config, "metrics_public_debug_enabled", False)
    monkeypatch.setattr(config, "debug", True)
    assert metrics_access_allowed(_scope()) is False
    monkeypatch.setattr(config, "metrics_public_debug_enabled", True)
    assert metrics_access_allowed(_scope()) is True


@pytest.mark.asyncio
async def test_metrics_mount_enforces_bearer(monkeypatch):
    from app.main import app

    monkeypatch.setattr(config, "metrics_access_mode", "bearer")
    monkeypatch.setattr(config, "metrics_bearer_token", "metrics-test-token")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/metrics/")
        allowed = await client.get(
            "/metrics/", headers={"Authorization": "Bearer metrics-test-token"}
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200
