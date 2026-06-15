import pytest

from app.api import aiops as aiops_api

SESSION_HEADERS = {"X-Session-Owner": "owner-a"}


class _FakeAIOpsService:
    async def diagnose(self, session_id="default"):
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
            "stage": "diagnosis_complete",
            "message": "done",
            "diagnosis": {"status": "completed", "case_id": "case-1", "report": "# Report"},
        }


@pytest.mark.asyncio
async def test_aiops_stream_passes_normalized_events(monkeypatch, api_client):
    monkeypatch.setattr(aiops_api, "aiops_service", _FakeAIOpsService(), raising=False)

    response = await api_client.post(
        "/api/aiops",
        headers=SESSION_HEADERS,
        json={"session_id": "s1"},
    )

    assert response.status_code == 200
    body = response.text
    assert "agent_event" in body
    assert "triage" in body
    assert "diagnosis_complete" in body
