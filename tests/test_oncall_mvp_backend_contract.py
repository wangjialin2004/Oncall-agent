import asyncio

import pytest

from app.api import aiops as aiops_api
from app.services import router_service as router_module
from app.services.aiops_service import AIOpsService
from app.services.router_service import RouteDecision, RouterService


class _FakeAIOpsForAssistant:
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
            "type": "complete",
            "case_id": "case-mvp",
            "response": "# Report",
            "events": [
                {
                    "type": "agent_event",
                    "agent": "report",
                    "stage": "report",
                    "status": "completed",
                    "summary": "Report generated",
                    "payload": {},
                }
            ],
        }


class _FakeRag:
    async def query(self, message, session_id):
        return "rag answer"


class _HangingAIOpsForAssistant:
    async def execute(self, message, session_id):
        await asyncio.sleep(1)
        yield {
            "type": "complete",
            "case_id": "case-too-late",
            "response": "too late",
            "events": [],
        }


class _FakeAIOpsForApi:
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
            "diagnosis": {"status": "completed", "case_id": "case-mvp", "report": "# Report"},
            "events": [
                {
                    "type": "agent_event",
                    "agent": "triage",
                    "stage": "triage",
                    "status": "completed",
                    "summary": "Incident structured",
                    "payload": {},
                }
            ],
        }


class _FakeMemory:
    def create_case(self, session_id, user_input):
        return "case-mvp"

    def update_case_plan(self, case_id, plan):
        pass

    def complete_case(self, case_id, executed_steps, final_report):
        self.completed = {
            "case_id": case_id,
            "executed_steps": executed_steps,
            "final_report": final_report,
        }

    def fail_case(self, case_id, error_message):
        self.failed = {"case_id": case_id, "error_message": error_message}


@pytest.mark.asyncio
async def test_assistant_aiops_payload_contains_case_report_and_events(monkeypatch):
    service = RouterService(
        semantic_router=lambda message: RouteDecision(route="aiops", reason="test_aiops")
    )
    monkeypatch.setattr(router_module, "aiops_service", _FakeAIOpsForAssistant())
    monkeypatch.setattr(router_module, "rag_agent_service", _FakeRag())

    response = await service.answer("checkout-api slow", session_id="s1")

    assert response["success"] is True
    assert response["route"] == "aiops"
    assert response["route_reason"] == "test_aiops"
    assert response["case_id"] == "case-mvp"
    assert response["answer"] == "# Report"
    assert response["events"] == [
        {
            "type": "agent_event",
            "agent": "triage",
            "stage": "triage",
            "status": "completed",
            "summary": "Incident structured",
            "payload": {},
        },
        {
            "type": "agent_event",
            "agent": "report",
            "stage": "report",
            "status": "completed",
            "summary": "Report generated",
            "payload": {},
        },
    ]


@pytest.mark.asyncio
async def test_assistant_aiops_timeout_returns_degraded_report_and_events(monkeypatch):
    service = RouterService(
        semantic_router=lambda message: RouteDecision(route="aiops", reason="test_aiops"),
        aiops_timeout_seconds=0.01,
    )
    monkeypatch.setattr(router_module, "aiops_service", _HangingAIOpsForAssistant())
    monkeypatch.setattr(router_module, "rag_agent_service", _FakeRag())

    response = await service.answer("checkout-api slow", session_id="s1")

    assert response["success"] is True
    assert response["route"] == "aiops"
    assert response["route_reason"] == "test_aiops"
    assert response["case_id"].startswith("case-timeout-")
    assert "降级诊断报告" in response["answer"]
    assert response["events"] == [
        {
            "type": "agent_event",
            "agent": "router",
            "stage": "timeout_fallback",
            "status": "degraded",
            "summary": "AIOps 智能体执行超时，已返回降级诊断报告。",
            "payload": {"timeout_seconds": 0.01},
        }
    ]


@pytest.mark.asyncio
async def test_aiops_api_sse_complete_event_preserves_events(monkeypatch, api_client):
    monkeypatch.setattr(aiops_api, "aiops_service", _FakeAIOpsForApi(), raising=False)

    response = await api_client.post(
        "/api/aiops",
        headers={"X-Session-Owner": "owner-a"},
        json={"session_id": "s1"},
    )

    assert response.status_code == 200
    body = response.text
    assert "agent_event" in body
    assert "case-mvp" in body
    assert "Incident structured" in body


def test_diagnose_completion_includes_events_from_execute():
    async def fake_execute(user_input, session_id):
        yield {
            "type": "complete",
            "case_id": "case-mvp",
            "response": "# Report",
            "events": [{"type": "agent_event", "agent": "report", "summary": "done"}],
        }

    service = AIOpsService(memory_service=_FakeMemory(), checkpointer=None)
    service.execute = fake_execute

    async def collect():
        return [event async for event in service.diagnose(session_id="s1")]

    events = asyncio.run(collect())
    complete = events[-1]

    assert complete["type"] == "complete"
    assert complete["diagnosis"]["case_id"] == "case-mvp"
    assert complete["diagnosis"]["report"] == "# Report"
    assert complete["events"] == [{"type": "agent_event", "agent": "report", "summary": "done"}]
