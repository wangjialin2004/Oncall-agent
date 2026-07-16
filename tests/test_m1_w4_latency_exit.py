"""M1 W4: knowledge early close, rehydrate events, latency helpers."""

from __future__ import annotations

import pytest

from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessLimits, HarnessState
from app.core.llm_client import LLMResponse, LLMStreamChunk, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.services.router_service import RouteDecision


class FakeRouter:
    def __init__(self, route: str = "knowledge") -> None:
        self.route = route

    async def _resolve_route(self, message: str) -> RouteDecision:
        return RouteDecision(route=self.route, reason="fake", confidence=0.9)


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
        content = response.content or ""
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


def _knowledge_tool() -> RuntimeTool:
    async def handler(arguments: dict) -> dict:
        return {
            "success": True,
            "documents": ["cpu high runbook steps"],
            "query": arguments.get("query") or "",
        }

    return RuntimeTool(
        name="retrieve_knowledge",
        description="Retrieve knowledge docs.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": [],
        },
        handler=handler,
    )


def test_effective_max_steps_with_route_profile(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_dynamic_max_steps", True)
    monkeypatch.setattr(app_config, "harness_route_timeout_profile", True)
    service = HarnessService(
        limits=HarnessLimits(max_steps=6, token_budget=5000, timeout_seconds=10)
    )
    assert service._effective_max_steps("knowledge") == 2
    assert service._effective_max_steps("metric") == 6

    monkeypatch.setattr(app_config, "harness_route_timeout_profile", False)
    assert service._effective_max_steps("knowledge") == 3


def test_should_knowledge_early_close(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_knowledge_early_close", True)
    service = HarnessService(
        limits=HarnessLimits(max_steps=4, token_budget=5000, timeout_seconds=10)
    )
    state = HarnessState(trace_id="t", session_id="t", route="knowledge")
    assert service._should_knowledge_early_close(state=state) is False
    state.timeline_events.append(
        {
            "type": "tool_event",
            "tool": "retrieve_knowledge",
            "status": "completed",
        }
    )
    assert service._should_knowledge_early_close(state=state) is True
    state.route = "metric"
    assert service._should_knowledge_early_close(state=state) is False


@pytest.mark.asyncio
async def test_knowledge_early_close_emits_stage(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_knowledge_early_close", True)
    monkeypatch.setattr(app_config, "harness_re_evidence_enabled", False)
    monkeypatch.setattr(app_config, "harness_replan_enabled", False)
    monkeypatch.setattr(app_config, "harness_force_expert_delegation", False)
    monkeypatch.setattr(app_config, "harness_dynamic_max_steps", True)
    monkeypatch.setattr(app_config, "harness_route_timeout_profile", True)
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

    tool = _knowledge_tool()
    fake_llm = FakeLLM(
        [
            # step1: call knowledge tool
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(
                        id="k1",
                        name="retrieve_knowledge",
                        arguments={"query": "cpu high"},
                    )
                ],
                usage={"total_tokens": 4},
            ),
            # early close final answer (no more tools)
            LLMResponse(
                content="按知识库：CPU 过高应检查 top 进程与限流。",
                raw={},
                usage={"total_tokens": 5},
            ),
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="knowledge"),
        llm_client=fake_llm,
        tools=[tool],
        limits=HarnessLimits(max_steps=4, token_budget=10000, timeout_seconds=15),
    )
    events = [
        event
        async for event in service.stream(
            "CPU使用率过高一般怎么处理？",
            session_id="trace-knowledge-early",
            owner_key="user-1",
        )
    ]
    assert any(e.get("stage") == "knowledge_early_close" for e in events)
    assert any(
        e.get("type") == "tool_event" and e.get("tool") == "retrieve_knowledge"
        for e in events
    )
    # Only one tool-calling model turn + one closing turn (no extra tool loops).
    tool_call_turns = [
        c
        for c in fake_llm.calls
        if (c.get("kwargs") or {}).get("tools")
        and (c.get("kwargs") or {}).get("stream")
    ]
    assert len(tool_call_turns) == 1


@pytest.mark.asyncio
async def test_context_rehydrate_failed_when_fresh_and_ref(monkeypatch):
    from app.config import config as app_config
    from app.agent.context.integration import StatefulContext
    from app.agent.context.state import AgentContextState

    monkeypatch.setattr(app_config, "harness_context_db_snapshot_enabled", True)

    class _Snap:
        def get_latest_context_snapshot(self, **kwargs):
            return None

    monkeypatch.setattr(
        "app.services.context_snapshot_service.context_snapshot_service",
        _Snap(),
        raising=False,
    )

    service = HarnessService(
        limits=HarnessLimits(max_steps=2, token_budget=5000, timeout_seconds=10)
    )
    state = HarnessState(trace_id="t", session_id="t", owner_key="u1")
    ctx = StatefulContext(
        state=AgentContextState(owner_key="u1", session_id="t"),
        view="",
        recent_messages=[],
        source="fresh",
    )
    events = [
        event
        async for event in service._emit_context_rehydrate_status(
            state=state,
            stateful_ctx=ctx,
            resume_context_ref="ref-abc",
            owner_key="u1",
            session_id="t",
        )
    ]
    assert len(events) == 1
    assert events[0]["stage"] == "context_rehydrate_failed"
    assert events[0]["payload"]["context_snapshot_ref"] == "ref-abc"


@pytest.mark.asyncio
async def test_context_rehydrate_ok_from_store_source():
    from app.agent.context.integration import StatefulContext
    from app.agent.context.state import AgentContextState

    service = HarnessService(
        limits=HarnessLimits(max_steps=2, token_budget=5000, timeout_seconds=10)
    )
    state = HarnessState(trace_id="t2", session_id="t2", owner_key="u1")
    ctx = StatefulContext(
        state=AgentContextState(owner_key="u1", session_id="t2"),
        view="board",
        recent_messages=[],
        source="redis",
    )
    events = [
        event
        async for event in service._emit_context_rehydrate_status(
            state=state,
            stateful_ctx=ctx,
            resume_context_ref="ref-ok",
            owner_key="u1",
            session_id="t2",
        )
    ]
    assert len(events) == 1
    assert events[0]["stage"] == "context_rehydrate"
    assert events[0]["payload"]["source"] == "redis"
