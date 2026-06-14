import pytest
from langchain_core.messages import ToolMessage

from app.agent.aiops.evidence import build_persistent_tool_evidence
from app.services.aiops_service import AIOpsService
from app.services.diagnosis_memory_service import DiagnosisMemoryService


class _FakeState:
    def __init__(self, values):
        self.values = values


class _FakeGraph:
    def __init__(self):
        self.input_state = None

    async def astream(self, input, config, stream_mode):
        self.input_state = input
        yield {"planner": {"plan": ["check alerts"]}}
        yield {"executor": {"plan": [], "past_steps": [("check alerts", "ok")]}}

    def get_state(self, config):
        return _FakeState(
            {
                "response": "# final report",
                "past_steps": [("check alerts", "ok")],
            }
        )


class _FailingGraph:
    def __init__(self):
        self.input_state = None

    async def astream(self, input, config, stream_mode):
        self.input_state = input
        raise RuntimeError("planner unavailable")
        yield


async def test_aiops_service_execute_persists_case_lifecycle(tmp_path):
    memory_service = DiagnosisMemoryService(tmp_path / "diagnosis-memory.sqlite3")
    graph = _FakeGraph()
    service = object.__new__(AIOpsService)
    service.memory_service = memory_service
    service.graph = graph

    events = [event async for event in service.execute("diagnose", session_id="session-1")]

    case_id = graph.input_state["case_id"]
    case = memory_service.get_case(case_id)

    assert events[-1] == {
        "type": "complete",
        "stage": "complete",
        "message": "任务执行完成",
        "case_id": case_id,
        "response": "# final report",
    }
    assert graph.input_state["session_id"] == "session-1"
    assert case["status"] == "completed"
    assert case["session_id"] == "session-1"
    assert case["user_input"] == "diagnose"
    assert case["plan"] == ["check alerts"]
    assert case["executed_steps"] == [["check alerts", "ok"]]
    assert case["final_report"] == "# final report"


async def test_aiops_service_execute_exposes_case_id_on_error(tmp_path):
    memory_service = DiagnosisMemoryService(tmp_path / "diagnosis-memory.sqlite3")
    graph = _FailingGraph()
    service = object.__new__(AIOpsService)
    service.memory_service = memory_service
    service.graph = graph

    events = [event async for event in service.execute("diagnose", session_id="session-1")]

    case_id = graph.input_state["case_id"]
    case = memory_service.get_case(case_id)

    assert events == [
        {
            "type": "error",
            "stage": "error",
            "message": "任务执行出错: planner unavailable",
            "case_id": case_id,
        }
    ]
    assert case["status"] == "failed"


async def test_aiops_service_diagnose_exposes_case_id_in_completion(tmp_path):
    memory_service = DiagnosisMemoryService(tmp_path / "diagnosis-memory.sqlite3")
    graph = _FakeGraph()
    service = object.__new__(AIOpsService)
    service.memory_service = memory_service
    service.graph = graph

    events = [event async for event in service.diagnose(session_id="session-1")]

    case_id = graph.input_state["case_id"]

    assert events[-1]["type"] == "complete"
    assert events[-1]["stage"] == "diagnosis_complete"
    assert events[-1]["diagnosis"] == {
        "status": "completed",
        "case_id": case_id,
        "report": "# final report",
    }


def test_diagnosis_memory_service_persists_case_lifecycle(tmp_path):
    db_path = tmp_path / "diagnosis-memory.sqlite3"
    service = DiagnosisMemoryService(db_path)

    case_id = service.create_case(
        session_id="session-1",
        user_input="diagnose current alerts",
        case_id="case-1",
    )
    service.update_case_plan(case_id, ["check alerts", "inspect logs"])
    service.complete_case(
        case_id,
        executed_steps=[("check alerts", "found HighCPUUsage")],
        final_report="# report",
    )

    case = service.get_case(case_id)

    assert case == {
        "case_id": "case-1",
        "session_id": "session-1",
        "user_input": "diagnose current alerts",
        "status": "completed",
        "plan": ["check alerts", "inspect logs"],
        "executed_steps": [["check alerts", "found HighCPUUsage"]],
        "final_report": "# report",
    }


def test_persistent_tool_evidence_includes_arguments_and_raw_result():
    tool_message = ToolMessage(
        content='{"status":"success","source":"local_logs","evidence_id":"cls-1","duration_ms":5}',
        name="search_app_logs",
        tool_call_id="call-1",
    )

    records = build_persistent_tool_evidence(
        [tool_message],
        [{"id": "call-1", "name": "search_app_logs", "args": {"keyword": "ERROR"}}],
    )

    assert records == [
        {
            "tool_name": "search_app_logs",
            "tool_call_id": "call-1",
            "evidence_id": "cls-1",
            "source": "local_logs",
            "success": True,
            "duration_ms": 5,
            "summary": "status=success",
            "arguments": {"keyword": "ERROR"},
            "raw_result": (
                '{"status":"success","source":"local_logs","evidence_id":"cls-1","duration_ms":5}'
            ),
        }
    ]


def test_diagnosis_memory_service_persists_tool_evidence(tmp_path):
    db_path = tmp_path / "diagnosis-memory.sqlite3"
    service = DiagnosisMemoryService(db_path)
    service.create_case(
        session_id="session-1",
        user_input="diagnose current alerts",
        case_id="case-1",
    )

    service.record_tool_evidence(
        case_id="case-1",
        session_id="session-1",
        evidence_records=[
            {
                "tool_name": "search_app_logs",
                "tool_call_id": "call-1",
                "evidence_id": "cls-1",
                "source": "local_logs",
                "success": True,
                "duration_ms": 5,
                "summary": "status=success",
                "arguments": {"keyword": "ERROR"},
                "raw_result": '{"status":"success"}',
            }
        ],
    )

    evidence = service.list_tool_evidence("case-1")

    assert evidence == [
        {
            "tool_name": "search_app_logs",
            "tool_call_id": "call-1",
            "evidence_id": "cls-1",
            "source": "local_logs",
            "success": True,
            "duration_ms": 5.0,
            "summary": "status=success",
            "arguments": {"keyword": "ERROR"},
            "raw_result": '{"status":"success"}',
        }
    ]


def test_diagnosis_memory_service_persists_feedback(tmp_path):
    db_path = tmp_path / "diagnosis-memory.sqlite3"
    service = DiagnosisMemoryService(db_path)
    service.create_case(
        session_id="session-1",
        user_input="diagnose current alerts",
        case_id="case-1",
    )

    service.record_feedback(
        case_id="case-1",
        session_id="session-1",
        user_accepted=True,
        actual_root_cause="Milvus connection exhausted",
        final_resolution="Restarted Milvus and reduced connection churn",
        comment="诊断结论准确",
    )

    feedback = service.list_feedback("case-1")

    assert feedback == [
        {
            "case_id": "case-1",
            "session_id": "session-1",
            "user_accepted": True,
            "actual_root_cause": "Milvus connection exhausted",
            "final_resolution": "Restarted Milvus and reduced connection churn",
            "comment": "诊断结论准确",
        }
    ]


def test_diagnosis_memory_service_record_feedback_returns_feedback_id(tmp_path):
    db_path = tmp_path / "diagnosis-memory.sqlite3"
    service = DiagnosisMemoryService(db_path)
    service.create_case(session_id="session-1", user_input="diagnose", case_id="case-1")

    feedback_id = service.record_feedback(
        case_id="case-1",
        session_id="session-1",
        user_accepted=True,
        actual_root_cause="Milvus connection exhausted",
        final_resolution="Restarted Milvus",
    )

    assert isinstance(feedback_id, str)
    assert feedback_id.startswith("feedback-")


def test_diagnosis_memory_service_rejects_feedback_for_missing_case(tmp_path):
    db_path = tmp_path / "diagnosis-memory.sqlite3"
    service = DiagnosisMemoryService(db_path)

    with pytest.raises(ValueError, match="Diagnosis case not found"):
        service.record_feedback(
            case_id="missing-case",
            session_id="session-1",
            user_accepted=False,
        )


def test_diagnosis_memory_service_rejects_feedback_lookup_for_missing_case(tmp_path):
    db_path = tmp_path / "diagnosis-memory.sqlite3"
    service = DiagnosisMemoryService(db_path)

    with pytest.raises(ValueError, match="Diagnosis case not found"):
        service.list_feedback("missing-case")
