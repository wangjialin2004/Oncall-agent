from fastapi.testclient import TestClient

from app.main import app

SESSION_HEADERS = {"X-Session-Owner": "owner-a"}


def test_assistant_endpoint_returns_router_result(monkeypatch):
    async def fake_answer(question, session_id):
        return {
            "success": True,
            "route": "rag",
            "answer": f"{session_id}:{question}",
            "errorMessage": None,
        }

    monkeypatch.setattr("app.api.assistant.router_service.answer", fake_answer)

    client = TestClient(app)
    response = client.post(
        "/api/assistant",
        headers=SESSION_HEADERS,
        json={"Id": "s1", "Question": "diagnose slow response"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "data": {
            "success": True,
            "route": "rag",
            "answer": "owner:95256875:s1:diagnose slow response",
            "errorMessage": None,
        },
    }


def test_assistant_endpoint_requires_session_owner(monkeypatch):
    async def fake_answer(question, session_id):
        return {
            "success": True,
            "route": "rag",
            "answer": "should not be reached",
            "errorMessage": None,
        }

    monkeypatch.setattr("app.api.assistant.router_service.answer", fake_answer)

    client = TestClient(app)
    response = client.post(
        "/api/assistant",
        json={"Id": "s1", "Question": "diagnose slow response"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "X-Session-Owner header is required"


def test_assistant_endpoint_scopes_same_session_id_by_owner(monkeypatch):
    seen_session_ids = []

    async def fake_answer(question, session_id):
        seen_session_ids.append(session_id)
        return {
            "success": True,
            "route": "rag",
            "answer": session_id,
            "errorMessage": None,
        }

    monkeypatch.setattr("app.api.assistant.router_service.answer", fake_answer)

    client = TestClient(app)
    for owner in ("owner-a", "owner-b"):
        response = client.post(
            "/api/assistant",
            headers={"X-Session-Owner": owner},
            json={"Id": "s1", "Question": "diagnose slow response"},
        )
        assert response.status_code == 200

    assert len(seen_session_ids) == 2
    assert seen_session_ids[0].endswith(":s1")
    assert seen_session_ids[1].endswith(":s1")
    assert seen_session_ids[0] != seen_session_ids[1]
