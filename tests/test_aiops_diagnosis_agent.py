import sys

import pytest

from app.core.llm_client import LLMResponse
from app.agent.aiops.diagnosis import diagnosis as diagnosis_node
from app.agent.aiops.diagnosis import route_after_diagnosis

# The package __init__ exports a ``diagnosis`` function that shadows the
# ``diagnosis`` submodule attribute, so fetch the real module object from
# sys.modules to patch its globals.
diagnosis_module = sys.modules["app.agent.aiops.diagnosis"]


def test_route_after_diagnosis_goes_to_report_when_ready():
    state = {"diagnosis": {"status": "root_cause_ready"}, "iteration": 1, "max_iterations": 2}

    assert route_after_diagnosis(state) == "reporter"


def test_route_after_diagnosis_goes_to_planner_when_evidence_is_insufficient():
    state = {"diagnosis": {"status": "evidence_insufficient"}, "iteration": 1, "max_iterations": 2}

    assert route_after_diagnosis(state) == "planner"


def test_route_after_diagnosis_goes_to_report_at_max_iterations():
    state = {"diagnosis": {"status": "evidence_insufficient"}, "iteration": 2, "max_iterations": 2}

    assert route_after_diagnosis(state) == "reporter"


@pytest.mark.asyncio
async def test_generate_diagnosis_uses_custom_llm_client_json():
    class FakeLLMClient:
        def __init__(self):
            self.messages = None
            self.temperature = None

        async def complete(self, messages, *, temperature):
            self.messages = messages
            self.temperature = temperature
            return LLMResponse(
                content=(
                    '{"status":"root_cause_ready",'
                    '"root_cause_candidates":[{"cause":"DB saturation","confidence":0.82,'
                    '"supporting_evidence_ids":["ev-1"]}],'
                    '"missing_evidence":[],"next_focus":"","confidence":0.82}'
                ),
                raw={},
            )

    llm_client = FakeLLMClient()

    result = await diagnosis_module.generate_diagnosis(
        {
            "incident": {"service_name": "checkout-api"},
            "evidence": [{"evidence_id": "ev-1", "summary": "db p95 high"}],
            "past_steps": ["checked metrics"],
        },
        llm_client=llm_client,
    )

    assert llm_client.temperature == 0
    assert llm_client.messages[0].role == "system"
    assert "Incident:" in llm_client.messages[1].content
    assert result["status"] == "root_cause_ready"
    assert result["root_cause_candidates"][0]["cause"] == "DB saturation"
    assert result["confidence"] == 0.82


@pytest.mark.asyncio
async def test_diagnosis_node_uses_generated_diagnosis(monkeypatch):
    async def fake_generate_diagnosis(state):
        return {
            "status": "root_cause_ready",
            "root_cause_candidates": [{"cause": "DB saturation", "confidence": 0.8}],
            "missing_evidence": [],
            "next_focus": "",
            "confidence": 0.8,
        }

    monkeypatch.setattr(diagnosis_module, "generate_diagnosis", fake_generate_diagnosis)

    update = await diagnosis_node({"events": [], "iteration": 0, "max_iterations": 2})

    assert update["diagnosis"]["status"] == "root_cause_ready"
    assert update["iteration"] == 1
    assert update["events"][-1]["type"] == "decision_event"


@pytest.mark.asyncio
async def test_diagnosis_node_falls_back_to_insufficient_evidence(monkeypatch):
    async def fake_generate_diagnosis(state):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(diagnosis_module, "generate_diagnosis", fake_generate_diagnosis)

    update = await diagnosis_node({"events": [], "iteration": 0, "max_iterations": 2})

    assert update["diagnosis"]["status"] == "evidence_insufficient"
    assert update["diagnosis"]["missing_evidence"] == ["诊断模型不可用。"]
    assert update["events"][-1]["summary"] == "收集指标、日志和相关预案证据"
    assert update["events"][-1]["status"] == "evidence_insufficient"
