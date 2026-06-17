from importlib import import_module

import pytest

from app.core.llm_client import LLMResponse

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
    assert "优先验证历史根因" in context


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


@pytest.mark.asyncio
async def test_generate_plan_steps_uses_custom_llm_client_json():
    class FakeLLMClient:
        def __init__(self):
            self.messages = None
            self.temperature = None

        async def complete(self, messages, *, temperature):
            self.messages = messages
            self.temperature = temperature
            return LLMResponse(
                content='{"steps":["Collect service metrics","Search recent error logs"]}',
                raw={},
            )

    llm_client = FakeLLMClient()

    steps = await planner_module.generate_plan_steps(
        input_text="checkout-api is slow",
        tools_description="query_metrics: fetch metrics\nquery_logs: fetch logs",
        experience_context="Historical case: latency caused by DB saturation",
        diagnosis_feedback="",
        llm_client=llm_client,
    )

    assert steps == ["Collect service metrics", "Search recent error logs"]
    assert llm_client.temperature == 0
    assert llm_client.messages[0].role == "system"
    assert "query_metrics" in llm_client.messages[0].content
    assert llm_client.messages[1].content == "checkout-api is slow"
