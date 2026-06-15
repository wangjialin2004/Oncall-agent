import sys

import pytest

from app.agent.aiops.reporter import build_fallback_report
from app.agent.aiops.reporter import reporter as reporter_node

# The package __init__ exports a ``reporter`` function that shadows the
# ``reporter`` submodule attribute, so fetch the real module object from
# sys.modules to patch its globals.
reporter_module = sys.modules["app.agent.aiops.reporter"]


def test_build_fallback_report_includes_required_sections():
    report = build_fallback_report(
        {
            "incident": {"incident_type": "slow_response", "service_name": "checkout-api"},
            "evidence": [
                {
                    "evidence_id": "ev-1",
                    "summary": "P95 latency rose above 3s",
                    "status": "completed",
                }
            ],
            "diagnosis": {
                "status": "root_cause_ready",
                "root_cause_candidates": [{"cause": "DB saturation", "confidence": 0.75}],
            },
        }
    )

    assert "# OnCall Diagnosis Report" in report
    assert "checkout-api" in report
    assert "P95 latency rose above 3s" in report
    assert "DB saturation" in report
    assert "Recommended Actions" in report


@pytest.mark.asyncio
async def test_reporter_node_uses_generated_report(monkeypatch):
    async def fake_generate_report(state):
        return "# Report\nGenerated"

    monkeypatch.setattr(reporter_module, "generate_report", fake_generate_report)

    update = await reporter_node(
        {
            "incident": {"incident_type": "error_rate"},
            "evidence": [],
            "diagnosis": {},
            "events": [],
        }
    )

    assert update["response"] == "# Report\nGenerated"
    assert update["events"][-1]["agent"] == "report"
    assert update["events"][-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_reporter_node_falls_back_when_generation_fails(monkeypatch):
    async def fake_generate_report(state):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(reporter_module, "generate_report", fake_generate_report)

    update = await reporter_node(
        {
            "incident": {"incident_type": "disk", "service_name": "api"},
            "evidence": [],
            "diagnosis": {},
            "events": [],
        }
    )

    assert "# OnCall Diagnosis Report" in update["response"]
    assert update["events"][-1]["status"] == "degraded"
