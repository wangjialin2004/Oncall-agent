import sys

import pytest

from app.agent.aiops.triage import build_minimal_incident
from app.agent.aiops.triage import triage as triage_node

# The package __init__ exports a ``triage`` function that shadows the ``triage``
# submodule attribute, so fetch the real module object from sys.modules to patch
# its globals.
triage_module = sys.modules["app.agent.aiops.triage"]


def test_build_minimal_incident_extracts_basic_slow_response_signal():
    incident = build_minimal_incident("checkout-api 最近一直转圈，接口响应很慢")

    assert incident["incident_type"] == "slow_response"
    assert incident["service_name"] == "checkout-api"
    assert incident["time_window"] == "recent"
    assert incident["severity"] == "P2"
    assert "checkout-api 最近一直转圈，接口响应很慢" in incident["symptoms"]
    assert incident["evidence_needs"] == ["metrics", "logs", "knowledge"]
    assert incident["confidence"] == 0.4


@pytest.mark.asyncio
async def test_triage_node_returns_generated_incident_and_event(monkeypatch):
    async def fake_generate_incident(input_text):
        assert input_text == "payment service 500 errors"
        return {
            "incident_type": "error_rate",
            "service_name": "payment",
            "time_window": "last_30_minutes",
            "severity": "P1",
            "symptoms": ["500 errors"],
            "missing_fields": [],
            "evidence_needs": ["metrics", "logs"],
            "confidence": 0.82,
        }

    monkeypatch.setattr(triage_module, "generate_incident", fake_generate_incident)

    update = await triage_node({"input": "payment service 500 errors", "events": []})

    assert update["incident"]["incident_type"] == "error_rate"
    assert update["events"][-1]["agent"] == "triage"
    assert update["events"][-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_triage_node_falls_back_to_minimal_incident(monkeypatch):
    async def fake_generate_incident(input_text):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(triage_module, "generate_incident", fake_generate_incident)

    update = await triage_node({"input": "disk full on api", "events": []})

    assert update["incident"]["incident_type"] == "disk"
    assert update["incident"]["confidence"] == 0.4
    assert update["events"][-1]["status"] == "degraded"
