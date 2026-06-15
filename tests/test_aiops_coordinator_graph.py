from app.services.aiops_service import (
    NODE_DIAGNOSIS,
    NODE_EXECUTOR,
    NODE_PLANNER,
    NODE_REPORTER,
    NODE_TRIAGE,
    AIOpsService,
)


class _FakeMemoryService:
    def create_case(self, session_id, user_input):
        return "case-1"

    def update_case_plan(self, case_id, plan):
        self.updated_plan = plan

    def complete_case(self, case_id, executed_steps, final_report):
        self.completed = {
            "case_id": case_id,
            "executed_steps": executed_steps,
            "final_report": final_report,
        }

    def fail_case(self, case_id, error_message):
        self.failed = {"case_id": case_id, "error_message": error_message}


def test_initial_oncall_state_contains_loop_and_event_fields():
    service = AIOpsService(memory_service=_FakeMemoryService(), checkpointer=None)

    state = service._build_initial_state(
        user_input="checkout-api slow",
        session_id="s1",
        case_id="case-1",
    )

    assert state["input"] == "checkout-api slow"
    assert state["session_id"] == "s1"
    assert state["case_id"] == "case-1"
    assert state["plan"] == []
    assert state["past_steps"] == []
    assert state["evidence"] == []
    assert state["iteration"] == 0
    assert state["max_iterations"] == 2
    assert state["events"] == []


def test_node_constants_describe_coordinator_graph():
    assert NODE_TRIAGE == "triage"
    assert NODE_PLANNER == "planner"
    assert NODE_EXECUTOR == "executor"
    assert NODE_DIAGNOSIS == "diagnosis"
    assert NODE_REPORTER == "reporter"
