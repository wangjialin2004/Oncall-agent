import pytest

from backend.services.agent_gateway import AgentGatewayService
from backend.services.agent_router import AgentRoute


class FakeRouter:
    def __init__(self, route):
        self.route = route

    def resolve_route(self, *, message, mode):
        return self.route


class FakeRagService:
    async def query_stream(self, message, session_id):
        yield {"type": "content", "data": "hello "}
        yield {"type": "content", "data": "world"}
        yield {"type": "complete", "data": {"answer": "hello world"}}


class FakeOnCallService:
    async def execute(self, message, session_id):
        yield {
            "type": "agent_event",
            "agent": "triage",
            "stage": "triage",
            "status": "completed",
            "summary": "Incident structured",
            "payload": {},
        }
        yield {
            "type": "tool_event",
            "agent": "evidence_collector",
            "tool": "query_metrics_alerts",
            "status": "completed",
            "evidence_id": "ev-1",
            "summary": "Collected metrics",
            "payload": {"duration_ms": 12},
        }
        yield {
            "type": "complete",
            "case_id": "case-1",
            "response": "# Report",
            "events": [],
        }


@pytest.mark.asyncio
async def test_rag_stream_starts_with_route_selected_and_finishes_complete():
    service = AgentGatewayService(
        router=FakeRouter(AgentRoute(route="rag", reason="explicit_mode")),
        rag_service=FakeRagService(),
        oncall_service=FakeOnCallService(),
    )

    events = [
        event
        async for event in service.stream(message="explain docs", session_id="s1", mode="rag")
    ]

    assert events[0] == {
        "type": "route_selected",
        "route": "rag",
        "reason": "explicit_mode",
        "mode": "rag",
    }
    assert events[1] == {"type": "content", "data": "hello "}
    assert events[2] == {"type": "content", "data": "world"}
    assert events[-1] == {
        "type": "complete",
        "route": "rag",
        "answer": "hello world",
        "case_id": "",
        "events": [],
    }


@pytest.mark.asyncio
async def test_oncall_stream_forwards_timeline_events_and_report():
    service = AgentGatewayService(
        router=FakeRouter(AgentRoute(route="oncall", reason="explicit_mode")),
        rag_service=FakeRagService(),
        oncall_service=FakeOnCallService(),
    )

    events = [
        event
        async for event in service.stream(message="checkout-api slow", session_id="s1", mode="oncall")
    ]

    assert events[0]["type"] == "route_selected"
    assert events[0]["route"] == "oncall"
    assert events[1]["type"] == "agent_event"
    assert events[2]["type"] == "tool_event"
    assert events[3] == {
        "type": "report",
        "route": "oncall",
        "case_id": "case-1",
        "report": "# Report",
    }
    assert events[4] == {
        "type": "complete",
        "route": "oncall",
        "answer": "# Report",
        "case_id": "case-1",
        "events": [],
    }
