from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import config
from app.main import app
from app.services.auth_service import auth_service
from app.services.session_scope_service import require_authenticated_principal


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setattr(config, "auth_users", "operator:op-pass,curator:cur-pass")
    monkeypatch.setattr(config, "auth_token_secret", "authorization-matrix-secret")
    monkeypatch.setattr(config, "auth_token_ttl_seconds", 3600)
    monkeypatch.setattr(config, "auth_user_roles", "operator:operator,curator:curator")
    monkeypatch.setattr(config, "auth_user_projects", "operator:p1,curator:p1")
    monkeypatch.setattr(config, "auth_role_enforcement_enabled", False)
    monkeypatch.setattr(config, "harness_eval_hooks_enabled", False)
    monkeypatch.setattr(config, "harness_checkpoint_request_override_enabled", False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client


async def _token(client: AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["data"]["token"]


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/api/assistant", {"json": {"Id": "session-1", "Question": "status"}}),
        ("get", "/api/conversations", {}),
        ("get", "/api/files", {}),
        ("get", "/api/memory/experiences", {}),
        (
            "post",
            "/api/hitl/confirm-suggestion",
            {"json": {"SessionId": "session-1", "ActionId": "action-1"}},
        ),
        ("get", "/api/checkpoint/session-1", {}),
    ],
)
@pytest.mark.asyncio
async def test_business_api_anonymous_matrix_returns_401(client, method, path, kwargs):
    response = await getattr(client, method)(path, **kwargs)
    assert response.status_code == 401, (method, path, response.text)


@pytest.mark.asyncio
async def test_principal_has_stable_owner_project_and_role(client):
    token = await _token(client, "operator", "op-pass")
    principal = await require_authenticated_principal(f"Bearer {token}")
    assert principal.owner_key == auth_service.stable_owner_key_for_user("operator")
    assert len(principal.owner_key) == 64
    assert principal.storage_owner_key == auth_service.owner_key_for_user("operator")
    assert principal.project_id == "p1"
    assert principal.role == "operator"


@pytest.mark.parametrize(
    "payload",
    [
        {"Id": "session-1", "Question": "status", "Simulate": "network_error"},
        {"Id": "session-1", "Question": "status", "PreferParallel": True},
    ],
)
@pytest.mark.asyncio
async def test_eval_hooks_are_rejected_by_default(client, payload):
    token = await _token(client, "operator", "op-pass")
    response = await client.post(
        "/api/assistant",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "eval_hooks_disabled"


@pytest.mark.asyncio
async def test_aggressive_checkpoint_request_override_is_rejected_by_default(client):
    token = await _token(client, "operator", "op-pass")
    response = await client.post(
        "/api/assistant",
        headers={"Authorization": f"Bearer {token}"},
        json={"Id": "session-1", "Question": "status", "CheckpointReplay": True},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "checkpoint_override_disabled"


@pytest.mark.asyncio
async def test_request_cannot_supply_tenant_or_role(client):
    token = await _token(client, "operator", "op-pass")
    response = await client.post(
        "/api/assistant",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "Id": "session-1",
            "Question": "status",
            "owner_key": "attacker",
            "project_id": "other",
            "role": "admin",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_operator_denied_curator_write_when_phase_two_enabled(client, monkeypatch):
    monkeypatch.setattr(config, "auth_role_enforcement_enabled", True)
    token = await _token(client, "operator", "op-pass")
    response = await client.post(
        "/api/memory/experiences",
        headers={"Authorization": f"Bearer {token}"},
        json={"symptoms": "cpu high", "root_cause": "loop", "resolution": "fix"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["detail"] == "insufficient_role"


@pytest.mark.asyncio
async def test_curator_can_use_project_write_when_phase_two_enabled(client, monkeypatch):
    from app.api import memory as memory_api

    monkeypatch.setattr(config, "auth_role_enforcement_enabled", True)
    monkeypatch.setattr(
        memory_api.experience_memory_service,
        "create_manual",
        lambda **kwargs: f"{kwargs['project_id']}:exp-1",
    )
    token = await _token(client, "curator", "cur-pass")
    response = await client.post(
        "/api/memory/experiences",
        headers={"Authorization": f"Bearer {token}"},
        json={"symptoms": "cpu high", "root_cause": "loop", "resolution": "fix"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["experience_id"] == "p1:exp-1"


@pytest.mark.asyncio
async def test_hitl_accepts_only_action_from_owned_session(client, monkeypatch):
    from app.api import hitl as hitl_api

    token = await _token(client, "operator", "op-pass")
    owner_key = auth_service.owner_key_for_user("operator")
    monkeypatch.setattr(
        hitl_api.conversation_service,
        "get_turns",
        lambda owner, session: (
            [
                {
                    "events": [
                        {
                            "type": "decision_event",
                            "stage": "suggested_actions",
                            "actions": [{"id": "review_metrics"}],
                        }
                    ]
                }
            ]
            if (owner, session) == (owner_key, "session-1")
            else []
        ),
    )

    accepted = await client.post(
        "/api/hitl/confirm-suggestion",
        headers={"Authorization": f"Bearer {token}"},
        json={"SessionId": "session-1", "ActionId": "review_metrics"},
    )
    missing = await client.post(
        "/api/hitl/confirm-suggestion",
        headers={"Authorization": f"Bearer {token}"},
        json={"SessionId": "session-1", "ActionId": "restart_service"},
    )

    assert accepted.status_code == 200
    assert accepted.json()["data"]["executed"] is False
    assert missing.status_code == 404
