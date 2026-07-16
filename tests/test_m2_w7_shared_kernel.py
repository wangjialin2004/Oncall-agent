"""M2 W7 regression: shared sub-harness kernel + change policy + merge dedup."""

from __future__ import annotations

import json

import pytest

from app.agent.experts.base import ToolCallingExpert
from app.agent.harness.sub_harness import SubHarnessConfig, merge_delegate_results, run_sub_harness
from app.core.llm_client import LLMResponse, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.tools.change_tool import CHANGE_SOURCE_AVAILABLE, _query_recent_changes, change_source_policy


class _ScriptedClient:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def complete(self, messages, **kwargs):  # noqa: ANN001
        del messages, kwargs
        if not self._responses:
            return LLMResponse(content="done", raw={})
        self.calls += 1
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_run_sub_harness_close_after_tools_skips_final_summary():
    async def tool_handler(_args: dict) -> dict:
        return {"status": "ok", "value": 1}

    tool = RuntimeTool(
        name="probe_metric",
        description="probe",
        parameters={"type": "object", "properties": {}},
        handler=tool_handler,
    )
    client = _ScriptedClient(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="probe_metric", arguments={})],
                raw={},
            ),
            # Would be used only if close_after_tools were false
            LLMResponse(content="should-not-run", raw={}),
        ]
    )
    cfg = SubHarnessConfig(
        agent_label="metric",
        display_name="指标专家",
        system_prompt="you are metric",
        tools=[tool],
        max_steps=2,
        close_after_tools=True,
    )
    events = []
    async for ev in run_sub_harness(
        cfg,
        message="check cpu",
        session_id="s",
        trace_id="t",
        llm_client=client,
    ):
        events.append(ev)

    stages = [e.get("stage") for e in events if e.get("type") == "agent_event"]
    assert "evidence_return" in stages
    assert "complete" in stages
    assert client.calls == 1
    assert any(e.get("type") == "tool_event" for e in events)
    complete = next(e for e in events if e.get("type") == "agent_event" and e.get("stage") == "complete")
    assert (complete.get("payload") or {}).get("shared_kernel") is True


@pytest.mark.asyncio
async def test_tool_calling_expert_uses_shared_kernel(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_shared_kernel_delegation", True)

    class ProbeExpert(ToolCallingExpert):
        agent_label = "probe"
        display_name = "探针"
        system_prompt = "probe"
        max_tool_rounds = 1

        async def get_tools(self):
            async def handler(_a: dict) -> dict:
                return {"ok": True}

            return [
                RuntimeTool(
                    name="t1",
                    description="t",
                    parameters={"type": "object", "properties": {}},
                    handler=handler,
                )
            ]

        async def _new_llm_client(self):
            return _ScriptedClient(
                [
                    LLMResponse(
                        content="",
                        tool_calls=[ToolCall(id="x", name="t1", arguments={})],
                        raw={},
                    )
                ]
            )

    expert = ProbeExpert()
    events = []
    async for ev in expert.run(
        message="hi",
        session_id="s",
        trace_id="t",
        close_after_tools=True,
    ):
        events.append(ev)

    complete = next(
        e for e in events if e.get("type") == "agent_event" and e.get("stage") == "complete"
    )
    assert (complete.get("payload") or {}).get("shared_kernel") is True
    assert any(e.get("type") == "tool_event" for e in events)


def test_merge_delegate_results_dedupes_experts_and_tools():
    results = [
        {
            "expert": "metric",
            "status": "completed",
            "answer": "a1",
            "events": [
                {"type": "tool_event", "tool": "query_prometheus_alerts", "status": "completed"},
                {"type": "tool_event", "tool": "query_prometheus_alerts", "status": "completed"},
            ],
        },
        {
            "expert": "metric",
            "status": "failed",
            "answer": "a2",
            "events": [],
        },
        {
            "expert": "log",
            "status": "completed",
            "answer": "logs",
            "events": [
                {"type": "tool_event", "tool": "search_logs", "status": "completed"},
            ],
        },
    ]
    merged = merge_delegate_results(results)
    assert merged["status"] == "completed"
    experts = {r["expert"] for r in merged["results"]}
    assert experts == {"metric", "log"}
    # metric keeps completed over failed
    metric = next(r for r in merged["results"] if r["expert"] == "metric")
    assert metric["status"] == "completed"
    assert "query_prometheus_alerts" in merged["folded_tools"]
    assert "search_logs" in merged["folded_tools"]
    # duplicate tool success folded once per expert:tool
    assert merged["folded_tools"].count("query_prometheus_alerts") == 1


def test_change_source_option_b_unavailable(monkeypatch):
    from app.config import config as app_config

    assert CHANGE_SOURCE_AVAILABLE is False
    monkeypatch.setattr(app_config, "change_source_policy", "unavailable")
    assert change_source_policy() == "unavailable"
    payload = json.loads(_query_recent_changes(service="checkout-api"))
    assert payload["success"] is False
    assert payload["source_available"] is False
    assert payload["gap"] == "missing_change_datasource"
    assert payload["policy"] == "unavailable"
    assert payload["changes"] == []


def test_change_source_invalid_policy_falls_back(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "change_source_policy", "live-please")
    assert change_source_policy() == "unavailable"
