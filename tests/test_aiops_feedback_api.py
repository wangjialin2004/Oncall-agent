import pytest

from app.api import aiops as aiops_api


class _FakeDiagnosisMemoryService:
    def __init__(self):
        self.feedback = []
        self.missing_cases = set()
        self.raise_unexpected = False
        self.raise_unexpected_on_list = False

    def record_feedback(
        self,
        case_id,
        session_id,
        user_accepted,
        actual_root_cause="",
        final_resolution="",
        comment="",
    ):
        if self.raise_unexpected:
            raise RuntimeError("sqlite unavailable")
        if case_id in self.missing_cases:
            raise ValueError(f"Diagnosis case not found: {case_id}")

        self.feedback.append(
            {
                "case_id": case_id,
                "session_id": session_id,
                "user_accepted": user_accepted,
                "actual_root_cause": actual_root_cause,
                "final_resolution": final_resolution,
                "comment": comment,
            }
        )
        return f"feedback-{len(self.feedback)}"

    def list_feedback(self, case_id):
        if self.raise_unexpected_on_list:
            raise RuntimeError("sqlite unavailable")
        if case_id in self.missing_cases:
            raise ValueError(f"Diagnosis case not found: {case_id}")

        return [item for item in self.feedback if item["case_id"] == case_id]


class _FakeExperienceMemoryService:
    def __init__(self):
        self.calls = []
        self.raise_on_create = False

    def create_or_merge_from_feedback(
        self,
        *,
        case_id,
        feedback_id,
        project_id,
        environment="",
        service_name="",
    ):
        if self.raise_on_create:
            raise RuntimeError("milvus unavailable")
        self.calls.append(
            {
                "case_id": case_id,
                "feedback_id": feedback_id,
                "project_id": project_id,
                "environment": environment,
                "service_name": service_name,
            }
        )
        return "exp-1"

@pytest.mark.asyncio
async def test_record_diagnosis_feedback_endpoint(monkeypatch, api_client):
    fake_memory = _FakeDiagnosisMemoryService()
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)

    response = await api_client.post(
        "/api/aiops/feedback",
        json={
            "case_id": "case-1",
            "session_id": "session-1",
            "user_accepted": True,
            "actual_root_cause": "Milvus connection exhausted",
            "final_resolution": "Restarted Milvus",
            "comment": "Diagnosis was accurate",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "data": {
            "case_id": "case-1",
            "session_id": "session-1",
            "user_accepted": True,
            "actual_root_cause": "Milvus connection exhausted",
            "final_resolution": "Restarted Milvus",
            "comment": "Diagnosis was accurate",
        },
    }
    assert fake_memory.feedback == [response.json()["data"]]


@pytest.mark.asyncio
async def test_record_diagnosis_feedback_creates_experience_for_accepted_feedback(
    monkeypatch, api_client
):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_experience = _FakeExperienceMemoryService()
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)
    monkeypatch.setattr(aiops_api, "experience_memory_service", fake_experience, raising=False)

    response = await api_client.post(
        "/api/aiops/feedback",
        json={
            "case_id": "case-1",
            "session_id": "session-1",
            "user_accepted": True,
            "actual_root_cause": "Milvus connection exhausted",
            "final_resolution": "Restarted Milvus",
        },
    )

    assert response.status_code == 200
    assert fake_experience.calls == [
        {
            "case_id": "case-1",
            "feedback_id": "feedback-1",
            "project_id": "super_biz_agent",
            "environment": "",
            "service_name": "",
        }
    ]


@pytest.mark.asyncio
async def test_record_diagnosis_feedback_skips_experience_for_rejected_feedback(
    monkeypatch, api_client
):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_experience = _FakeExperienceMemoryService()
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)
    monkeypatch.setattr(aiops_api, "experience_memory_service", fake_experience, raising=False)

    response = await api_client.post(
        "/api/aiops/feedback",
        json={"case_id": "case-1", "session_id": "session-1", "user_accepted": False},
    )

    assert response.status_code == 200
    assert fake_experience.calls == []


@pytest.mark.asyncio
async def test_record_diagnosis_feedback_ignores_experience_memory_errors(
    monkeypatch, api_client
):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_experience = _FakeExperienceMemoryService()
    fake_experience.raise_on_create = True
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)
    monkeypatch.setattr(aiops_api, "experience_memory_service", fake_experience, raising=False)

    response = await api_client.post(
        "/api/aiops/feedback",
        json={"case_id": "case-1", "session_id": "session-1", "user_accepted": True},
    )

    assert response.status_code == 200
    assert fake_memory.feedback[0]["case_id"] == "case-1"


@pytest.mark.asyncio
async def test_record_diagnosis_feedback_endpoint_reports_missing_case(monkeypatch, api_client):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_memory.missing_cases.add("missing-case")
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)

    response = await api_client.post(
        "/api/aiops/feedback",
        json={
            "case_id": "missing-case",
            "session_id": "session-1",
            "user_accepted": False,
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "code": 404,
        "message": "Diagnosis case not found: missing-case",
        "data": None,
    }


@pytest.mark.asyncio
async def test_record_diagnosis_feedback_endpoint_reports_unexpected_error(
    monkeypatch, api_client
):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_memory.raise_unexpected = True
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)

    response = await api_client.post(
        "/api/aiops/feedback",
        json={
            "case_id": "case-1",
            "session_id": "session-1",
            "user_accepted": False,
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "code": 500,
        "message": "error",
        "data": {
            "errorMessage": "sqlite unavailable",
        },
    }


@pytest.mark.asyncio
async def test_list_diagnosis_feedback_endpoint(monkeypatch, api_client):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_memory.record_feedback(
        case_id="case-1",
        session_id="session-1",
        user_accepted=False,
        actual_root_cause="Disk saturation",
        final_resolution="Expanded disk",
        comment="Root cause needed adjustment",
    )
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)

    response = await api_client.get("/api/aiops/cases/case-1/feedback")

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "data": [
            {
                "case_id": "case-1",
                "session_id": "session-1",
                "user_accepted": False,
                "actual_root_cause": "Disk saturation",
                "final_resolution": "Expanded disk",
                "comment": "Root cause needed adjustment",
            }
        ],
    }


@pytest.mark.asyncio
async def test_list_diagnosis_feedback_endpoint_reports_unexpected_error(
    monkeypatch, api_client
):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_memory.raise_unexpected_on_list = True
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)

    response = await api_client.get("/api/aiops/cases/case-1/feedback")

    assert response.status_code == 500
    assert response.json() == {
        "code": 500,
        "message": "error",
        "data": {
            "errorMessage": "sqlite unavailable",
        },
    }


@pytest.mark.asyncio
async def test_list_diagnosis_feedback_endpoint_reports_missing_case(monkeypatch, api_client):
    fake_memory = _FakeDiagnosisMemoryService()
    fake_memory.missing_cases.add("missing-case")
    monkeypatch.setattr(aiops_api, "diagnosis_memory_service", fake_memory, raising=False)

    response = await api_client.get("/api/aiops/cases/missing-case/feedback")

    assert response.status_code == 404
    assert response.json() == {
        "code": 404,
        "message": "Diagnosis case not found: missing-case",
        "data": None,
    }
