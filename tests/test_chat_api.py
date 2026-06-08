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
