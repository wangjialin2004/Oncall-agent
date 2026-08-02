"""M1 Close-the-Loop: force seed-delegation, re-evidence, change boundary."""

from __future__ import annotations

import json

import pytest

from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessLimits
from app.agent.harness.subagent import create_delegate_tool
from app.core.llm_client import LLMResponse, LLMStreamChunk, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.services.router_service import RouteDecision
from app.tools.change_tool import CHANGE_SOURCE_AVAILABLE, _query_recent_changes


class FakeRouter:
    def __init__(self, route: str = "diagnosis") -> None:
        self.route = route

    async def _resolve_route(self, message: str, previous_route: str | None = None, **kwargs) -> RouteDecision:
        return RouteDecision(route=self.route, reason="fake_focus", confidence=0.8)


class FakeLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs})
        return self.responses.pop(0)

    async def stream_complete(self, messages, **kwargs):
        self.calls.append(
            {"messages": list(messages), "kwargs": {**kwargs, "stream": True}}
        )
        response = self.responses.pop(0)
        content = response.content
        midpoint = max(1, len(content) // 2) if content else 1
        for chunk in (content[:midpoint], content[midpoint:]):
            if chunk:
                yield chunk

    async def stream_chat(self, messages, **kwargs):
        self.calls.append(
            {"messages": list(messages), "kwargs": {**kwargs, "stream": True}}
        )
        response = self.responses.pop(0)
        content = response.content or ""
        midpoint = max(1, len(content) // 2) if content else 1
        for chunk in (content[:midpoint], content[midpoint:]):
            if chunk:
                yield LLMStreamChunk(content=chunk)
        yield LLMStreamChunk(response=response)

    async def aclose(self) -> None:
        return None


class FakeDelegateExpert:
    async def run(self, *, message: str, session_id: str, trace_id: str, context: str = ""):
        yield {
            "type": "agent_event",
            "agent": "metric_expert",
            "stage": "start",
            "status": "in_progress",
            "summary": f"delegate trace={trace_id}",
        }
        yield {
            "type": "tool_event",
            "agent": "metric_expert",
            "tool": "query_metric",
            "status": "completed",
            "evidence_id": "metric-evidence-1",
            "summary": "CPU is high",
        }
        yield {"type": "content", "data": f"metric answer:{message}"}


def _probe_tool() -> RuntimeTool:
    async def handler(arguments: dict) -> dict:
        return {
            "success": True,
            "cpu_percent": 87.5,
            "service": str(arguments.get("service") or "checkout-api"),
        }

    return RuntimeTool(
        name="query_prometheus_alerts",
        description="Query alerts for a service.",
        parameters={
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": [],
        },
        handler=handler,
    )


@pytest.mark.asyncio
async def test_force_expert_delegation_seeds_before_model(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_force_expert_delegation", True)
    monkeypatch.setattr(app_config, "harness_delegation_enabled", True)
    monkeypatch.setattr(app_config, "harness_re_evidence_enabled", False)
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

    delegate_tool = create_delegate_tool(
        session_id="trace-force",
        trace_id="trace-force",
        context_getter=lambda: "parent context",
        expert_getter=lambda route: FakeDelegateExpert(),
    )
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="closing with seeded expert evidence",
                raw={},
                usage={"total_tokens": 6},
            )
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[delegate_tool],
        limits=HarnessLimits(max_steps=3, token_budget=5000, timeout_seconds=10),
    )

    events = [
        event
        async for event in service.stream(
            "checkout-api CPU high",
            session_id="trace-force",
            owner_key="user-1",
        )
    ]

    assert any(
        event.get("type") == "agent_event"
        and event.get("stage") == "delegate_dispatch"
        and event.get("payload", {}).get("forced") is True
        for event in events
    )
    assert any(
        event.get("type") == "agent_event" and event.get("stage") == "delegate_start"
        for event in events
    )
    assert any(
        event.get("type") == "tool_event" and event.get("tool") == "delegate_to_expert"
        for event in events
    )
    # Seed happens before the first model decision call.
    assert len(fake_llm.calls) >= 1
    first_call_messages = fake_llm.calls[0]["messages"]
    roles = [getattr(m, "role", None) or m.get("role") for m in first_call_messages]
    assert "tool" in roles or any(
        getattr(m, "tool_calls", None) or (isinstance(m, dict) and m.get("tool_calls"))
        for m in first_call_messages
    )


@pytest.mark.asyncio
async def test_force_expert_delegation_off_does_not_seed(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_force_expert_delegation", False)
    monkeypatch.setattr(app_config, "harness_re_evidence_enabled", False)
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

    delegate_tool = create_delegate_tool(
        session_id="trace-soft",
        trace_id="trace-soft",
        context_getter=lambda: "parent context",
        expert_getter=lambda route: FakeDelegateExpert(),
    )
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="answered without seed",
                raw={},
                usage={"total_tokens": 4},
            )
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[delegate_tool],
        limits=HarnessLimits(max_steps=3, token_budget=5000, timeout_seconds=10),
    )
    events = [
        event
        async for event in service.stream(
            "checkout-api CPU high",
            session_id="trace-soft",
            owner_key="user-1",
        )
    ]
    assert not any(event.get("stage") == "delegate_dispatch" for event in events)
    assert not any(
        event.get("type") == "tool_event" and event.get("tool") == "delegate_to_expert"
        for event in events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replacement_enabled", "expected_visible"),
    [
        (True, "I guess CPU is high but no tool was used."),
        (
            False,
            "I guess CPU is high but no tool was used."
            "checkout-api CPU is 87.5% based on alerts tool.",
        ),
    ],
)
async def test_re_evidence_replaces_prior_visible_answer_by_default(
    monkeypatch, replacement_enabled, expected_visible
):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_re_evidence_enabled", True)
    monkeypatch.setattr(app_config, "harness_re_evidence_max_rounds", 1)
    monkeypatch.setattr(app_config, "harness_corrective_verify_enabled", True)
    monkeypatch.setattr(
        app_config,
        "harness_final_answer_replacement_enabled",
        replacement_enabled,
    )
    monkeypatch.setattr(app_config, "harness_force_expert_delegation", False)
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

    probe = _probe_tool()
    fake_llm = FakeLLM(
        [
            # 1) first close without tools → verify degraded
            LLMResponse(
                content="I guess CPU is high but no tool was used.",
                raw={},
                usage={"total_tokens": 5},
            ),
            # 2) re-evidence: model calls tool
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(
                        id="call-probe",
                        name="query_prometheus_alerts",
                        arguments={"service": "checkout-api"},
                    )
                ],
                usage={"total_tokens": 8},
            ),
            # 3) closing answer after tool
            LLMResponse(
                content="checkout-api CPU is 87.5% based on alerts tool.",
                raw={},
                usage={"total_tokens": 6},
            ),
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[probe],
        limits=HarnessLimits(max_steps=4, token_budget=10000, timeout_seconds=15),
    )
    events = [
        event
        async for event in service.stream(
            "checkout-api CPU high please diagnose",
            session_id="trace-re-evidence",
            owner_key="user-1",
        )
    ]

    re_events = [e for e in events if e.get("stage") == "re_evidence"]
    assert re_events, "expected re_evidence stage when first answer lacks tools"
    assert re_events[0]["payload"]["round"] == 1

    tool_events = [
        e
        for e in events
        if e.get("type") == "tool_event" and e.get("tool") == "query_prometheus_alerts"
    ]
    assert tool_events
    assert any(e.get("status") == "completed" for e in tool_events)

    verify_events = [
        e
        for e in events
        if e.get("stage") == "verify" and e.get("status") != "in_progress"
    ]
    assert len(verify_events) >= 2
    assert verify_events[-1]["payload"]["evidence_count"] >= 1

    visible = "".join(
        str(event.get("data") or "")
        for event in events
        if event.get("type") == "content"
    )
    assert visible == expected_visible

    complete_payload = next(
        event for event in reversed(events) if event.get("type") == "complete"
    )
    assert "checkout-api CPU is 87.5% based on alerts tool." in complete_payload["answer"]
    if replacement_enabled:
        assert complete_payload.get("replace_streamed_answer") is True
    else:
        assert "replace_streamed_answer" not in complete_payload

    complete = next(e for e in events if e.get("stage") == "complete")
    assert complete["payload"].get("re_evidence_rounds_used") == 1


@pytest.mark.asyncio
async def test_re_evidence_disabled_skips_extra_pass(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_re_evidence_enabled", False)
    monkeypatch.setattr(app_config, "harness_replan_enabled", False)
    monkeypatch.setattr(app_config, "harness_force_expert_delegation", False)
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

    probe = _probe_tool()
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="answer without evidence",
                raw={},
                usage={"total_tokens": 4},
            )
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[probe],
        limits=HarnessLimits(max_steps=3, token_budget=5000, timeout_seconds=10),
    )
    events = [
        event
        async for event in service.stream(
            "checkout-api CPU high",
            session_id="trace-re-off",
            owner_key="user-1",
        )
    ]
    assert not any(e.get("stage") == "re_evidence" for e in events)
    assert len(fake_llm.calls) == 1


def test_change_tool_returns_structured_missing_datasource_gap():
    assert CHANGE_SOURCE_AVAILABLE is False
    payload = json.loads(_query_recent_changes(service="checkout-api", time_window="24h"))
    assert payload["success"] is False
    assert payload["source_available"] is False
    assert payload["gap"] == "missing_change_datasource"
    assert payload["changes"] == []
    text = payload["message"]
    assert "未接入" in text or "无变更" in text or "缺少变更" in text
    assert "不要编造" in text or "编造" in text
