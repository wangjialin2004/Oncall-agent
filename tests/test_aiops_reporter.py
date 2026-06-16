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
                },
                {
                    "evidence_id": "ev-2",
                    "summary": "Log query failed",
                    "status": "failed",
                },
            ],
            "diagnosis": {
                "status": "root_cause_ready",
                "root_cause_candidates": [{"cause": "DB saturation", "confidence": 0.75}],
            },
        }
    )

    assert "# OnCall 诊断报告" in report
    assert "checkout-api" in report
    assert "诊断状态：根因已就绪" in report
    assert "`ev-1` [已完成]" in report
    assert "`ev-2` [失败]" in report
    assert "P95 latency rose above 3s" in report
    assert "DB saturation" in report
    assert "## 建议操作" in report


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

    assert "# OnCall 诊断报告" in update["response"]
    assert update["events"][-1]["status"] == "degraded"
