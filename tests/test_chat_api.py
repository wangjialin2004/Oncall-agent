import pytest

SESSION_HEADERS = {"X-Session-Owner": "owner-a"}


@pytest.mark.asyncio
async def test_chat_endpoint_reports_service_exception_as_http_error(
    monkeypatch,
    api_client,
):
    async def fake_query(question, session_id):
        raise RuntimeError("rag unavailable")

    monkeypatch.setattr("app.api.chat.rag_agent_service.query", fake_query)

    response = await api_client.post(
        "/api/chat",
        headers=SESSION_HEADERS,
        json={"Id": "s1", "Question": "diagnose slow response"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "code": 500,
        "message": "error",
        "data": {
            "success": False,
            "answer": None,
            "errorMessage": "rag unavailable",
        },
    }


@pytest.mark.asyncio
async def test_clear_session_reports_service_exception_as_api_response(
    monkeypatch,
    api_client,
):
    def fake_clear_session(session_id):
        raise RuntimeError(f"cannot clear {session_id}")

    monkeypatch.setattr("app.api.chat.rag_agent_service.clear_session", fake_clear_session)

    response = await api_client.post(
        "/api/chat/clear",
        headers=SESSION_HEADERS,
        json={"sessionId": "s1"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "status": "error",
        "message": "cannot clear owner:95256875:s1",
        "data": None,
    }


@pytest.mark.asyncio
async def test_get_session_info_requires_session_owner(api_client):
    response = await api_client.get("/api/chat/session/s1")

    assert response.status_code == 401
    assert response.json()["detail"] == "X-Session-Owner header is required"


@pytest.mark.asyncio
async def test_get_session_info_scopes_same_session_id_by_owner(monkeypatch, api_client):
    seen_session_ids = []

    def fake_get_session_history(session_id):
        seen_session_ids.append(session_id)
        return []

    monkeypatch.setattr(
        "app.api.chat.rag_agent_service.get_session_history",
        fake_get_session_history,
    )

    for owner in ("owner-a", "owner-b"):
        response = await api_client.get(
            "/api/chat/session/s1",
            headers={"X-Session-Owner": owner},
        )
        assert response.status_code == 200

    assert len(seen_session_ids) == 2
    assert seen_session_ids[0].endswith(":s1")
    assert seen_session_ids[1].endswith(":s1")
    assert seen_session_ids[0] != seen_session_ids[1]


@pytest.mark.asyncio
async def test_get_session_info_reports_service_exception_as_api_response(
    monkeypatch,
    api_client,
):
    def fake_get_session_history(session_id):
        raise RuntimeError(f"cannot load {session_id}")

    monkeypatch.setattr(
        "app.api.chat.rag_agent_service.get_session_history",
        fake_get_session_history,
    )

    response = await api_client.get("/api/chat/session/s1", headers=SESSION_HEADERS)

    assert response.status_code == 500
    assert response.json() == {
        "status": "error",
        "message": "cannot load owner:95256875:s1",
        "data": None,
    }
