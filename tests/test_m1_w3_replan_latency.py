"""M1 W3: mid-loop replan, dynamic max_steps, parallel tool calls."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agent.agent_loop import GuardedToolExecutor
from app.agent.harness.loop import HarnessService
from app.agent.harness.planner import HarnessPlan, LightweightPlanner, rule_replan
from app.agent.harness.state import HarnessLimits
from app.core.llm_client import LLMResponse, LLMStreamChunk, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.services.router_service import RouteDecision


class FakeRouter:
    def __init__(self, route: str = "metric", aux_routes: tuple[str, ...] = ()) -> None:
        self.route = route
        self.aux_routes = aux_routes

    async def _resolve_route(self, message: str) -> RouteDecision:
        return RouteDecision(
            route=self.route,
            reason="fake_focus",
            confidence=0.8,
            aux_routes=self.aux_routes,
        )


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


def _failing_probe() -> RuntimeTool:
    async def handler(arguments: dict) -> dict:
        raise RuntimeError("prometheus unavailable")

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


def _ok_probe(name: str = "query_prometheus_alerts", delay: float = 0.0) -> RuntimeTool:
    async def handler(arguments: dict) -> dict:
        if delay:
            await asyncio.sleep(delay)
        return {"success": True, "tool": name, "service": arguments.get("service")}

    return RuntimeTool(
        name=name,
        description=f"Probe tool {name}",
        parameters={
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": [],
        },
        handler=handler,
    )


def test_rule_replan_promotes_gaps_and_failed_tools():
    base = HarnessPlan(
        todos=["理解请求", "查指标"],
        required_evidence=["指标曲线或告警"],
        focus_route="metric",
        available_tools=["query_prometheus_alerts"],
    )
    revised = rule_replan(
        base,
        trigger="primary_tool_failure",
        gaps=["缺少指标证据"],
        failed_tools=["query_prometheus_alerts"],
        aux_routes=["log"],
    )
    assert any("缺口" in t or "失败" in t for t in revised.todos)
    assert "证据缺口说明" in revised.required_evidence
    assert any("log" in t for t in revised.todos)
    assert any("日志" in e or "错误" in e for e in revised.required_evidence)


def test_planner_replan_wrapper():
    planner = LightweightPlanner()
    plan = planner.create(
        message="cpu high",
        route_decision=RouteDecision(route="metric", reason="t", confidence=0.9),
        tools=[_ok_probe()],
        history_turns=0,
    )
    revised = planner.replan(
        plan, trigger="post_re_evidence_gaps", gaps=["缺证据"], failed_tools=[]
    )
    assert revised.todos[0].startswith("优先补齐") or "缺口" in revised.todos[0]


def test_effective_max_steps_knowledge(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_dynamic_max_steps", True)
    # W4 route profile further clamps knowledge to 2; isolate W3 behavior here.
    monkeypatch.setattr(app_config, "harness_route_timeout_profile", False)
    service = HarnessService(
        limits=HarnessLimits(max_steps=6, token_budget=5000, timeout_seconds=10)
    )
    assert service._effective_max_steps("knowledge") == 3
    assert service._effective_max_steps("metric") == 6
    monkeypatch.setattr(app_config, "harness_dynamic_max_steps", False)
    assert service._effective_max_steps("knowledge") == 6


@pytest.mark.asyncio
async def test_replan_triggers_after_tool_failure(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_replan_enabled", True)
    monkeypatch.setattr(app_config, "harness_replan_max_times", 1)
    monkeypatch.setattr(app_config, "harness_re_evidence_enabled", False)
    monkeypatch.setattr(app_config, "harness_force_expert_delegation", False)
    monkeypatch.setattr(app_config, "harness_dynamic_max_steps", False)
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

    probe = _failing_probe()
    fake_llm = FakeLLM(
        [
            # step1: call failing tool
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="query_prometheus_alerts",
                        arguments={"service": "checkout-api"},
                    )
                ],
                usage={"total_tokens": 4},
            ),
            # step2 after replan: close with gap notice
            LLMResponse(
                content="无法取证：Prometheus 不可用，证据缺口需人工核对。",
                raw={},
                usage={"total_tokens": 5},
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
            "checkout-api CPU high",
            session_id="trace-replan-fail",
            owner_key="user-1",
        )
    ]
    replan_events = [e for e in events if e.get("stage") == "replan"]
    assert len(replan_events) == 1
    assert replan_events[0]["payload"]["trigger"] == "primary_tool_failure"
    complete = next(e for e in events if e.get("stage") == "complete")
    assert complete["payload"].get("replan_times_used") == 1


@pytest.mark.asyncio
async def test_replan_disabled_skips(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_replan_enabled", False)
    monkeypatch.setattr(app_config, "harness_re_evidence_enabled", False)
    monkeypatch.setattr(app_config, "harness_force_expert_delegation", False)
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

    probe = _failing_probe()
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="query_prometheus_alerts",
                        arguments={"service": "x"},
                    )
                ],
                usage={"total_tokens": 3},
            ),
            LLMResponse(
                content="still no evidence, declaring gap.",
                raw={},
                usage={"total_tokens": 3},
            ),
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[probe],
        limits=HarnessLimits(max_steps=3, token_budget=8000, timeout_seconds=10),
    )
    events = [
        event
        async for event in service.stream(
            "cpu high",
            session_id="trace-replan-off",
            owner_key="user-1",
        )
    ]
    assert not any(e.get("stage") == "replan" for e in events)


@pytest.mark.asyncio
async def test_post_re_evidence_replan(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_replan_enabled", True)
    monkeypatch.setattr(app_config, "harness_replan_max_times", 1)
    monkeypatch.setattr(app_config, "harness_re_evidence_enabled", True)
    monkeypatch.setattr(app_config, "harness_re_evidence_max_rounds", 1)
    monkeypatch.setattr(app_config, "harness_force_expert_delegation", False)
    monkeypatch.setattr(app_config, "harness_corrective_verify_enabled", True)
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

    # Tool always available but model first answers without tools; re-evidence
    # also answers without tools → gaps remain → replan once → then tool.
    probe = _ok_probe()
    fake_llm = FakeLLM(
        [
            # 1 first close no tools
            LLMResponse(
                content="I guess CPU is high without tools.",
                raw={},
                usage={"total_tokens": 4},
            ),
            # 2 re-evidence: still no tools
            LLMResponse(
                content="Still guessing without tools.",
                raw={},
                usage={"total_tokens": 4},
            ),
            # 3 post-replan tool call
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(
                        id="c-replan",
                        name="query_prometheus_alerts",
                        arguments={"service": "checkout-api"},
                    )
                ],
                usage={"total_tokens": 5},
            ),
            # 4 final after tools
            LLMResponse(
                content="checkout-api CPU elevated based on alerts tool.",
                raw={},
                usage={"total_tokens": 5},
            ),
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[probe],
        limits=HarnessLimits(max_steps=4, token_budget=12000, timeout_seconds=15),
    )
    events = [
        event
        async for event in service.stream(
            "checkout-api CPU high please diagnose",
            session_id="trace-replan-after-re",
            owner_key="user-1",
        )
    ]
    assert any(e.get("stage") == "re_evidence" for e in events)
    replan_events = [e for e in events if e.get("stage") == "replan"]
    assert len(replan_events) == 1
    assert replan_events[0]["payload"]["trigger"] == "post_re_evidence_gaps"
    assert any(
        e.get("type") == "tool_event" and e.get("tool") == "query_prometheus_alerts"
        for e in events
    )


@pytest.mark.asyncio
async def test_parallel_tool_calls_faster_than_serial(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_parallel_tool_calls", True)
    monkeypatch.setattr(app_config, "harness_tool_max_retries", 0)

    tools = [
        _ok_probe("query_prometheus_alerts", delay=0.15),
        _ok_probe("retrieve_knowledge", delay=0.15),
    ]
    calls = [
        ToolCall(id="a", name="query_prometheus_alerts", arguments={"service": "s"}),
        ToolCall(id="b", name="retrieve_knowledge", arguments={"service": "s"}),
    ]
    executor = GuardedToolExecutor(timeout_seconds=5, max_retries=0)
    started = time.perf_counter()
    results = await executor.execute(calls, tools)
    elapsed = time.perf_counter() - started
    assert len(results) == 2
    assert all(r.success for r in results)
    # Serial would be ~0.30s; parallel should be closer to 0.15s.
    assert elapsed < 0.28

    monkeypatch.setattr(app_config, "harness_parallel_tool_calls", False)
    started = time.perf_counter()
    await executor.execute(calls, tools)
    serial_elapsed = time.perf_counter() - started
    assert serial_elapsed >= 0.28
