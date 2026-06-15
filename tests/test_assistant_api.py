import pytest

SESSION_HEADERS = {"X-Session-Owner": "owner-a"}


@pytest.mark.asyncio
async def test_assistant_endpoint_returns_router_result(monkeypatch, api_client):
    async def fake_answer(question, session_id):
        return {
            "success": True,
            "route": "rag",
            "answer": f"{session_id}:{question}",
            "errorMessage": None,
        }

    monkeypatch.setattr("app.api.assistant.router_service.answer", fake_answer)

    response = await api_client.post(
        "/api/assistant",
        headers=SESSION_HEADERS,
        json={"Id": "s1", "Question": "怎么排查慢响应"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "data": {
            "success": True,
            "route": "rag",
            "answer": "owner:95256875:s1:怎么排查慢响应",
            "errorMessage": None,
        },
    }


@pytest.mark.asyncio
async def test_assistant_endpoint_requires_session_owner(monkeypatch, api_client):
    async def fake_answer(question, session_id):
        return {
            "success": True,
            "route": "rag",
            "answer": "should not be reached",
            "errorMessage": None,
        }

    monkeypatch.setattr("app.api.assistant.router_service.answer", fake_answer)

    response = await api_client.post(
        "/api/assistant",
        json={"Id": "s1", "Question": "diagnose slow response"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "X-Session-Owner header is required"


@pytest.mark.asyncio
async def test_assistant_endpoint_scopes_same_session_id_by_owner(monkeypatch, api_client):
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

    for owner in ("owner-a", "owner-b"):
        response = await api_client.post(
            "/api/assistant",
            headers={"X-Session-Owner": owner},
            json={"Id": "s1", "Question": "diagnose slow response"},
        )
        assert response.status_code == 200

    assert len(seen_session_ids) == 2
    assert seen_session_ids[0].endswith(":s1")
    assert seen_session_ids[1].endswith(":s1")
    assert seen_session_ids[0] != seen_session_ids[1]


@pytest.mark.asyncio
async def test_assistant_endpoint_reports_router_exception_as_http_error(
    monkeypatch,
    api_client,
):
    async def fake_answer(question, session_id):
        raise RuntimeError("router unavailable")

    monkeypatch.setattr("app.api.assistant.router_service.answer", fake_answer)

    response = await api_client.post(
        "/api/assistant",
        headers=SESSION_HEADERS,
        json={"Id": "s1", "Question": "diagnose slow response"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "code": 500,
        "message": "error",
        "data": {
            "success": False,
            "route": "error",
            "answer": None,
            "errorMessage": "router unavailable",
        },
    }
