from fastapi.testclient import TestClient

from app.main import app

SESSION_HEADERS = {"X-Session-Owner": "owner-a"}


def test_chat_endpoint_requires_session_owner():
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"Id": "s1", "Question": "diagnose slow response"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "X-Session-Owner header is required"


def test_get_session_info_requires_session_owner():
    client = TestClient(app)

    response = client.get("/api/chat/session/s1")

    assert response.status_code == 401
    assert response.json()["detail"] == "X-Session-Owner header is required"


def test_get_session_info_scopes_same_session_id_by_owner(monkeypatch):
    seen_session_ids = []

    def fake_get_session_history(session_id):
        seen_session_ids.append(session_id)
        return []

    monkeypatch.setattr(
        "app.api.chat.rag_agent_service.get_session_history",
        fake_get_session_history,
    )

    client = TestClient(app)
    for owner in ("owner-a", "owner-b"):
        response = client.get(
            "/api/chat/session/s1",
            headers={"X-Session-Owner": owner},
        )
        assert response.status_code == 200

    assert len(seen_session_ids) == 2
    assert seen_session_ids[0].endswith(":s1")
    assert seen_session_ids[1].endswith(":s1")
    assert seen_session_ids[0] != seen_session_ids[1]


def test_clear_session_uses_scoped_session_id(monkeypatch):
    seen_session_ids = []

    def fake_clear_session(session_id):
        seen_session_ids.append(session_id)
        return True

    monkeypatch.setattr("app.api.chat.rag_agent_service.clear_session", fake_clear_session)

    client = TestClient(app)
    response = client.post(
        "/api/chat/clear",
        headers=SESSION_HEADERS,
        json={"sessionId": "s1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert seen_session_ids == ["owner:95256875:s1"]
