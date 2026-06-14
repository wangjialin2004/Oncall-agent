from importlib import import_module

planner_module = import_module("app.agent.aiops.planner")


class _FakeExperienceMemoryService:
    def __init__(self):
        self.calls = []

    def search_relevant_experiences(self, *, query, project_id, top_k):
        self.calls.append({"query": query, "project_id": project_id, "top_k": top_k})
        return [
            {
                "experience_id": "exp-1",
                "similarity": 0.86,
                "confidence": 0.8,
                "symptoms": "API slow with Milvus timeout",
                "root_cause": "Milvus connection pool exhausted",
                "resolution": "Restart Milvus and reuse clients",
                "evidence_summary": "cls-1 timeout; metric-1 latency p95 high",
                "source_case_ids": ["case-1"],
            }
        ]


def test_format_experience_context_requires_verification_first():
    context = planner_module.format_experience_context(
        [
            {
                "experience_id": "exp-1",
                "similarity": 0.86,
                "confidence": 0.8,
                "symptoms": "API slow with Milvus timeout",
                "root_cause": "Milvus connection pool exhausted",
                "resolution": "Restart Milvus",
                "evidence_summary": "cls-1 timeout",
                "source_case_ids": ["case-1"],
            }
        ]
    )

    assert "exp-1" in context
    assert "Milvus connection pool exhausted" in context
    assert "first verify the historical root cause" in context


def test_load_experience_context_searches_by_project(monkeypatch):
    fake_service = _FakeExperienceMemoryService()
    monkeypatch.setattr(planner_module, "experience_memory_service", fake_service)
    monkeypatch.setattr(planner_module.config, "project_id", "super_biz_agent")
    monkeypatch.setattr(planner_module.config, "experience_memory_top_k", 3)

    context = planner_module.load_experience_context("diagnose API slow")

    assert fake_service.calls == [
        {"query": "diagnose API slow", "project_id": "super_biz_agent", "top_k": 3}
    ]
    assert "exp-1" in context
