import pytest


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
        json={"Id": "s1", "Question": "怎么排查慢响应"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "data": {
            "success": True,
            "route": "rag",
            "answer": "s1:怎么排查慢响应",
            "errorMessage": None,
        },
    }


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
