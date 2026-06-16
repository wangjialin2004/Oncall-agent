import pytest

from app.services.aiops_service import AIOpsService


class _FakeMemoryService:
    def __init__(self):
        self.completed = None

    def create_case(self, session_id, user_input):
        return "case-1"

    def update_case_plan(self, case_id, plan):
        pass

    def complete_case(self, case_id, executed_steps, final_report):
        self.completed = {
            "case_id": case_id,
            "executed_steps": executed_steps,
            "final_report": final_report,
        }

    def fail_case(self, case_id, error_message):
        raise AssertionError(error_message)


@pytest.mark.asyncio
async def test_aiops_execute_emits_complete_event_with_events(monkeypatch, tmp_path):
    from langgraph.checkpoint.memory import MemorySaver

    async def fake_triage(state):
        return {
            "incident": {"incident_type": "slow_response", "service_name": "checkout-api"},
            "events": [{"type": "agent_event", "agent": "triage", "summary": "structured"}],
        }

    async def fake_planner(state):
        return {
            "plan": [{"step_id": "plan-1", "description": "check metrics"}],
            "events": state.get("events", []) + [
                {"type": "agent_event", "agent": "planner", "summary": "planned"}
            ],
        }

    async def fake_executor(state):
        return {
            "plan": [],
            "past_steps": [{"step_id": "plan-1", "status": "completed", "result": "latency high"}],
            "evidence": [{"evidence_id": "ev-1", "status": "completed", "summary": "latency high"}],
            "events": state.get("events", []) + [
                {"type": "tool_event", "agent": "evidence_collector", "evidence_id": "ev-1"}
            ],
        }

    async def fake_diagnosis(state):
        return {
            "diagnosis": {"status": "root_cause_ready"},
            "iteration": 1,
            "events": state.get("events", []) + [
                {"type": "decision_event", "agent": "diagnosis", "status": "root_cause_ready"}
            ],
        }

    async def fake_reporter(state):
        return {
            "response": "# Report",
            "events": state.get("events", []) + [
                {"type": "agent_event", "agent": "report", "summary": "reported"}
            ],
        }

    monkeypatch.setattr("app.services.aiops_service.triage", fake_triage)
    monkeypatch.setattr("app.services.aiops_service.planner", fake_planner)
    monkeypatch.setattr("app.services.aiops_service.executor", fake_executor)
    monkeypatch.setattr("app.services.aiops_service.diagnosis", fake_diagnosis)
    monkeypatch.setattr("app.services.aiops_service.reporter", fake_reporter)

    memory = _FakeMemoryService()
    service = AIOpsService(memory_service=memory, checkpointer=MemorySaver())

    events = [event async for event in service.execute("checkout-api slow", session_id="s1")]

    complete = events[-1]
    assert complete["type"] == "complete"
    assert complete["case_id"] == "case-1"
    assert complete["response"] == "# Report"
    assert any(event.get("agent") == "triage" for event in complete["events"])
    assert memory.completed["final_report"] == "# Report"
