import pytest


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
        json={"sessionId": "s1"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "status": "error",
        "message": "cannot clear s1",
        "data": None,
    }


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

    response = await api_client.get("/api/chat/session/s1")

    assert response.status_code == 500
    assert response.json() == {
        "status": "error",
        "message": "cannot load s1",
        "data": None,
    }
