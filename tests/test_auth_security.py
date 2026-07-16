from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import config
from app.main import app
from app.services.auth_service import auth_service


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "auth_users", "pilot:pilot-pass")
    monkeypatch.setattr(config, "auth_token_secret", "unit-test-secret")
    monkeypatch.setattr(config, "auth_token_ttl_seconds", 3600)
    monkeypatch.setattr(config, "cors_allow_origins", "http://localhost:5173")
    return TestClient(app)


def test_login_rejects_unknown_user(client):
    response = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert response.status_code == 401


def test_login_rejects_wrong_password(client):
    response = client.post("/api/auth/login", json={"username": "pilot", "password": "wrong"})
    assert response.status_code == 401


def test_login_accepts_configured_user_and_token_has_exp(client):
    response = client.post("/api/auth/login", json={"username": "pilot", "password": "pilot-pass"})
    assert response.status_code == 200
    token = response.json()["data"]["token"]
    assert auth_service.verify_access_token(token) == "pilot"
    payload_part = token.split(".", 2)[1]
    padding = "=" * (-len(payload_part) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_part + padding))
    assert "exp" in payload
    assert payload["exp"] > int(time.time())


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(config, "auth_token_secret", "unit-test-secret")
    monkeypatch.setattr(config, "auth_token_ttl_seconds", 1)
    token = auth_service.create_access_token("pilot")

    class _Frozen:
        @staticmethod
        def time():
            return 10**10

    monkeypatch.setattr("app.services.auth_service.time", _Frozen)
    with pytest.raises(ValueError, match="expired"):
        auth_service.verify_access_token(token)


def test_memory_write_requires_auth(client):
    response = client.post(
        "/api/memory/experiences",
        json={
            "symptoms": "cpu high",
            "root_cause": "busy loop",
            "resolution": "restart",
        },
    )
    assert response.status_code == 401


def test_memory_write_accepts_valid_token(client, monkeypatch):
    from app.api import memory as memory_api

    monkeypatch.setattr(
        memory_api.experience_memory_service,
        "create_manual",
        lambda **kwargs: "exp-test-1",
    )
    login = client.post("/api/auth/login", json={"username": "pilot", "password": "pilot-pass"})
    token = login.json()["data"]["token"]
    response = client.post(
        "/api/memory/experiences",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "symptoms": "cpu high",
            "root_cause": "busy loop",
            "resolution": "restart",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["experience_id"] == "exp-test-1"


def test_cors_origins_whitelist_localhost():
    origins = config.cors_origins_list
    assert origins != ["*"] or config.cors_allow_origins == "*"
    assert any("5173" in o for o in origins) or origins == ["*"]


def _assert_unauthorized_body(response, expected_detail: str) -> None:
    assert response.status_code == 401
    body = response.json()
    # FastAPI wraps our structured HTTPException detail under "detail".
    payload = body.get("detail") if isinstance(body.get("detail"), dict) else body
    assert payload["code"] == 401
    assert payload["message"] == "unauthorized"
    assert payload["detail"] == expected_detail


def test_auth_me_requires_token(client):
    response = client.get("/api/auth/me")
    _assert_unauthorized_body(response, "token_missing")


def test_auth_me_rejects_invalid_token(client):
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer v1.not-a-real.token"},
    )
    _assert_unauthorized_body(response, "token_invalid")


def test_auth_me_accepts_valid_token(client):
    login = client.post("/api/auth/login", json={"username": "pilot", "password": "pilot-pass"})
    token = login.json()["data"]["token"]
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["username"] == "pilot"
    assert data["owner_key"] == auth_service.owner_key_for_user("pilot")


def test_auth_me_rejects_expired_token(client, monkeypatch):
    monkeypatch.setattr(config, "auth_token_secret", "unit-test-secret")
    monkeypatch.setattr(config, "auth_token_ttl_seconds", 1)
    token = auth_service.create_access_token("pilot")

    class _Frozen:
        @staticmethod
        def time():
            return 10**10

    monkeypatch.setattr("app.services.auth_service.time", _Frozen)
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    _assert_unauthorized_body(response, "token_expired")


def test_conversations_list_uses_canonical_401_body(client):
    response = client.get("/api/conversations")
    _assert_unauthorized_body(response, "token_missing")


def test_secret_rotation_invalidates_existing_token(client, monkeypatch):
    login = client.post("/api/auth/login", json={"username": "pilot", "password": "pilot-pass"})
    token = login.json()["data"]["token"]
    # Rotate the signing secret after the token was issued.
    monkeypatch.setattr(config, "auth_token_secret", "rotated-secret")
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    _assert_unauthorized_body(response, "token_invalid")
