import pytest

from app.api import memory as memory_api


class _FakeExperienceMemoryService:
    def __init__(self):
        self.enabled_updates = []
        self.rebuild_project_ids = []

    def list_memories(
        self,
        *,
        project_id=None,
        enabled=None,
        service_name=None,
        min_confidence=None,
        limit=50,
        offset=0,
    ):
        return [
            {
                "experience_id": "exp-1",
                "project_id": project_id or "super_biz_agent",
                "environment": "local",
                "service_name": service_name or "milvus",
                "symptoms": "Milvus timeout",
                "root_cause": "connection pool exhausted",
                "resolution": "reuse client",
                "evidence_summary": "cls-1 timeout",
                "source_case_ids": ["case-1"],
                "source_feedback_ids": ["feedback-1"],
                "confidence": 0.8,
                "hit_count": 2,
                "success_count": 1,
                "enabled": True if enabled is None else enabled,
                "milvus_pk": "exp-1",
                "created_at": "2026-06-14T10:30:00+00:00",
                "updated_at": "2026-06-14T10:30:00+00:00",
            }
        ]

    def get_memory(self, experience_id):
        if experience_id == "missing":
            return None
        return self.list_memories()[0]

    def set_enabled(self, experience_id, *, enabled):
        self.enabled_updates.append({"experience_id": experience_id, "enabled": enabled})
        return experience_id != "missing"

    def rebuild_index(self, *, project_id=None):
        self.rebuild_project_ids.append(project_id)
        return 1


@pytest.mark.asyncio
async def test_list_experience_memories(monkeypatch, api_client):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake_service)

    response = await api_client.get(
        "/api/memory/experiences?project_id=super_biz_agent&enabled=true"
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["experience_id"] == "exp-1"


@pytest.mark.asyncio
async def test_get_experience_memory_detail(monkeypatch, api_client):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake_service)

    response = await api_client.get("/api/memory/experiences/exp-1")

    assert response.status_code == 200
    assert response.json()["data"]["source_case_ids"] == ["case-1"]


@pytest.mark.asyncio
async def test_disable_experience_memory(monkeypatch, api_client):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake_service)

    response = await api_client.patch(
        "/api/memory/experiences/exp-1",
        json={"enabled": False},
    )

    assert response.status_code == 200
    assert fake_service.enabled_updates == [{"experience_id": "exp-1", "enabled": False}]


@pytest.mark.asyncio
async def test_rebuild_experience_memory_index(monkeypatch, api_client):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(memory_api, "experience_memory_service", fake_service)

    response = await api_client.post(
        "/api/memory/experiences/rebuild-index?project_id=super_biz_agent"
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"indexed": 1}
    assert fake_service.rebuild_project_ids == ["super_biz_agent"]
