import pytest

from app.services.router_service import RouteDecision, RouterService


class _FakeAIOpsService:
    async def execute(self, message, session_id):
        yield {"type": "agent_event", "agent": "triage", "summary": "structured"}
        yield {
            "type": "complete",
            "case_id": "case-1",
            "response": "# Report",
            "events": [{"type": "agent_event", "agent": "report", "summary": "done"}],
        }


class _FakeRagAgentService:
    async def query(self, message, session_id):
        return "rag answer"


@pytest.mark.asyncio
async def test_router_answer_includes_oncall_events(monkeypatch):
    service = RouterService(
        semantic_router=lambda message: RouteDecision(route="aiops", reason="test_aiops")
    )
    monkeypatch.setattr("app.services.router_service.aiops_service", _FakeAIOpsService())
    monkeypatch.setattr("app.services.router_service.rag_agent_service", _FakeRagAgentService())

    response = await service.answer("checkout-api slow", session_id="s1")

    assert response["success"] is True
    assert response["route"] == "aiops"
    assert response["route_reason"] == "test_aiops"
    assert response["case_id"] == "case-1"
    assert response["answer"] == "# Report"
    assert response["events"] == [
        {"type": "agent_event", "agent": "triage", "summary": "structured"},
        {"type": "agent_event", "agent": "report", "summary": "done"},
    ]


@pytest.mark.asyncio
async def test_router_answer_keeps_rag_response_without_events(monkeypatch):
    service = RouterService(
        semantic_router=lambda message: RouteDecision(route="rag", reason="test_rag")
    )
    monkeypatch.setattr("app.services.router_service.rag_agent_service", _FakeRagAgentService())

    response = await service.answer("how to handle slow response", session_id="s1")

    assert response["route"] == "rag"
    assert "events" not in response
