import httpx
import pytest

from backend.api import agent as agent_api
from backend.main import app


class FakeGateway:
    async def stream(self, *, message, session_id, mode):
        yield {
            "type": "route_selected",
            "route": "oncall",
            "reason": "explicit_mode",
            "mode": mode,
        }
        yield {
            "type": "agent_event",
            "agent": "triage",
            "stage": "triage",
            "status": "completed",
            "summary": "Incident structured",
            "payload": {},
        }
        yield {
            "type": "complete",
            "route": "oncall",
            "answer": "# Report",
            "case_id": "case-1",
            "events": [],
        }


@pytest.mark.asyncio
async def test_agent_stream_endpoint_returns_sse_events(monkeypatch):
    monkeypatch.setattr(agent_api, "agent_gateway_service", FakeGateway())
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/agent/stream",
            json={
                "session_id": "s1",
                "message": "checkout-api slow",
                "mode": "oncall",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "route_selected" in response.text
    assert "agent_event" in response.text
    assert "case-1" in response.text


@pytest.mark.asyncio
async def test_agent_stream_endpoint_rejects_empty_message():
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/agent/stream",
            json={"session_id": "s1", "message": "", "mode": "auto"},
        )

    assert response.status_code == 422
