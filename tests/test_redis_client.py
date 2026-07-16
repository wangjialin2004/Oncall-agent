import pytest

from app.services import redis_client
from app.tools.redis_health import _check_redis_health


def test_build_redis_from_settings_uses_configured_protocol(monkeypatch) -> None:
    captured = {}

    class FakeRedis:
        @staticmethod
        def from_url(url: str, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return object()

    monkeypatch.setattr(redis_client, "_AsyncRedis", FakeRedis)
    monkeypatch.setattr(redis_client.config, "redis_url", "redis://localhost:6379/0", raising=False)
    monkeypatch.setattr(redis_client.config, "redis_socket_timeout", 3.0, raising=False)
    monkeypatch.setattr(redis_client.config, "redis_protocol", 2, raising=False)

    redis_client.build_redis_from_settings()

    assert captured["url"] == "redis://localhost:6379/0"
    assert captured["kwargs"]["socket_timeout"] == 3.0
    assert captured["kwargs"]["socket_connect_timeout"] == 3.0
    assert captured["kwargs"]["decode_responses"] is True
    assert captured["kwargs"]["protocol"] == 2


@pytest.mark.asyncio
async def test_check_redis_health_reports_success_without_password(monkeypatch) -> None:
    class FakeClient:
        async def ping(self):
            return True

        async def aclose(self):
            return None

    monkeypatch.setattr("app.tools.redis_health.is_redis_available", lambda: True)
    monkeypatch.setattr("app.tools.redis_health.build_redis_from_settings", lambda: FakeClient())
    monkeypatch.setattr(redis_client.config, "redis_enabled", True, raising=False)
    monkeypatch.setattr(redis_client.config, "redis_url", "redis://:secret@localhost:6379/0", raising=False)
    monkeypatch.setattr(redis_client.config, "redis_namespace", "super_biz_agent", raising=False)
    monkeypatch.setattr(redis_client.config, "redis_protocol", 2, raising=False)

    result = await _check_redis_health()

    assert '"success": true' in result
    assert '"status": "connected"' in result
    assert "secret" not in result
    assert "redis://localhost:6379/0" in result
