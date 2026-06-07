from fastapi.testclient import TestClient

from app.main import app


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
    response = client.post("/api/assistant", json={"Id": "s1", "Question": "怎么排查慢响应"})

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
