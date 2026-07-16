"""M1 W2: evidence class match, recent_turns stamp, timeout checkpoint, idempotent tools."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent.context.state import AgentContextState
from app.agent.harness.loop import HarnessService
from app.agent.harness.planner import HarnessPlan
from app.agent.harness.state import HarnessLimits, HarnessState
from app.agent.harness.verifier import (
    EvidenceVerifier,
    missing_required_evidence_gaps,
)
from app.core.llm_client import ChatMessage, LLMResponse, LLMStreamChunk
from app.services.harness_checkpoint import HarnessCheckpointStore
from app.services.router_service import RouteDecision
from tests._fake_redis import FakeRedis


class FakeRouter:
    def __init__(self, route: str = "metric") -> None:
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
        self.calls.append({"messages": list(messages), "kwargs": {**kwargs, "stream": True}})
        response = self.responses.pop(0)
        content = response.content or ""
        if content:
            yield content

    async def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": {**kwargs, "stream": True}})
        response = self.responses.pop(0)
        content = response.content or ""
        if content:
            yield LLMStreamChunk(content=content)
        yield LLMStreamChunk(response=response)

    async def aclose(self) -> None:
        return None


def test_missing_required_evidence_gaps_detects_metric_without_metric_tool():
    gaps = missing_required_evidence_gaps(
        required_evidence=["指标曲线或告警", "异常时间窗口"],
        successful_tools={"retrieve_knowledge"},
    )
    assert any("指标" in g or "告警" in g for g in gaps)


def test_missing_required_evidence_gaps_satisfied_by_prometheus_tool():
    gaps = missing_required_evidence_gaps(
        required_evidence=["指标曲线或告警"],
        successful_tools={"query_prometheus_alerts"},
    )
    assert gaps == []


def test_verifier_marks_type_mismatch_when_evidence_match_enabled(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_evidence_match_enabled", True)
    plan = HarnessPlan(
        todos=["t"],
        required_evidence=["指标曲线或告警", "异常时间窗口"],
        focus_route="metric",
    )
    result = EvidenceVerifier().verify(
        answer="something with knowledge only",
        timeline_events=[
            {
                "type": "tool_event",
                "tool": "retrieve_knowledge",
                "status": "completed",
                "summary": "kb hit",
            }
        ],
        plan=plan,
    )
    assert result.status == "degraded"
    assert result.evidence_count == 1
    assert any("缺少证据类型" in g for g in result.gaps)


def test_verifier_skips_type_match_when_disabled(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_evidence_match_enabled", False)
    plan = HarnessPlan(
        todos=["t"],
        required_evidence=["指标曲线或告警"],
        focus_route="metric",
    )
    result = EvidenceVerifier().verify(
        answer="ok",
        timeline_events=[
            {
                "type": "tool_event",
                "tool": "retrieve_knowledge",
                "status": "completed",
            }
        ],
        plan=plan,
    )
    assert result.status == "completed"
    assert not any("缺少证据类型" in g for g in result.gaps)


def test_checkpoint_default_idempotent_tools_include_readonly_queries():
    store = HarnessCheckpointStore(idempotent_tools=None)
    assert store.is_step_idempotent(["query_prometheus_alerts"]) is True
    assert store.is_step_idempotent(["retrieve_knowledge", "recall_experience"]) is True
    assert store.is_step_idempotent(["delegate_to_expert"]) is True
    assert store.is_step_idempotent(["dangerous_write_tool"]) is False


def test_stamp_current_turn_appends_user_and_assistant():
    state = AgentContextState(owner_key="u", session_id="s")
    state.conversation.recent_turns = [{"role": "user", "content": "old"}]
    HarnessService._stamp_current_turn(
        state,
        user_message="new question",
        assistant_answer="new answer " * 100,
    )
    turns = state.conversation.recent_turns
    assert turns[0]["content"] == "old"
    assert turns[-2]["role"] == "user"
    assert turns[-2]["content"] == "new question"
    assert turns[-1]["role"] == "assistant"
    assert len(turns[-1]["content"]) <= 610
    assert state.conversation.last_turn_index == 1


@pytest.mark.asyncio
async def test_harness_stamps_recent_turns_on_complete(monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_re_evidence_enabled",
        False,
    )

    captured: dict[str, Any] = {}

    async def fake_build_stateful_context(**kwargs):
        state = AgentContextState(
            owner_key=kwargs["owner_key"],
            session_id=kwargs["session_id"],
        )
        from app.agent.context.integration import StatefulContext

        return StatefulContext(state=state, view="view", recent_messages=[], source="fresh")

    async def fake_persist(state, **kwargs):
        captured["state"] = state
        captured["persist_snapshot"] = kwargs.get("persist_snapshot")
        return []

    monkeypatch.setattr("app.agent.harness.loop.build_stateful_context", fake_build_stateful_context)
    monkeypatch.setattr("app.agent.harness.loop.persist_stateful_context", fake_persist)
    # Avoid real context tools / registry complexity
    monkeypatch.setattr(
        "app.agent.harness.loop.HarnessToolRegistry.context_tools",
        lambda self, state: [],
    )

    fake_llm = FakeLLM(
        [LLMResponse(content="final answer text", raw={}, usage={"total_tokens": 3})]
    )
    service = HarnessService(
        router=FakeRouter(route="knowledge"),
        llm_client=fake_llm,
        tools=[],
        limits=HarnessLimits(max_steps=2, token_budget=5000, timeout_seconds=10),
        context_store=object(),
    )
    events = [
        event
        async for event in service.stream(
            "what is OOM?",
            session_id="sess-stamp",
            owner_key="owner-1",
        )
    ]
    assert any(e.get("stage") == "complete" for e in events)
    assert "state" in captured
    turns = captured["state"].conversation.recent_turns
    assert any(t.get("role") == "user" and "OOM" in t.get("content", "") for t in turns)
    assert any(t.get("role") == "assistant" for t in turns)
    assert captured["state"].output.last_answer_summary


@pytest.mark.asyncio
async def test_outer_timeout_best_effort_checkpoint(monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )
    saved: dict[str, Any] = {}

    service = HarnessService(
        router=FakeRouter(),
        llm_client=FakeLLM([]),
        tools=[],
        limits=HarnessLimits(
            max_steps=2,
            token_budget=5000,
            timeout_seconds=0.05,
            fallback_timeout_seconds=0.05,
        ),
        checkpoint_store=object(),  # non-None so schedule path checks owner
    )

    async def slow_inner(*args, **kwargs):
        runtime = kwargs.get("runtime")
        if runtime is not None:
            st = HarnessState(
                trace_id="t-timeout",
                session_id="t-timeout",
                owner_key="owner-timeout",
            )
            st.step = 2
            runtime["state"] = st
            runtime["messages"] = [ChatMessage(role="user", content="hi")]
        await asyncio.sleep(1.0)
        if False:
            yield {}

    async def empty_fallback(**kwargs):
        # Fallback may also time out; either path is fine for this test.
        if False:
            yield {}

    monkeypatch.setattr(service, "_stream_inner", slow_inner)
    monkeypatch.setattr(
        service,
        "_schedule_checkpoint_save",
        lambda **kwargs: saved.update(kwargs),
    )
    monkeypatch.setattr(service, "_fallback_stream", empty_fallback)
    monkeypatch.setattr(
        service,
        "_final_fallback_complete_event",
        lambda **kwargs: {
            "type": "agent_event",
            "stage": "timeout_fallback",
            "status": "degraded",
        },
    )

    events = [
        event
        async for event in service.stream(
            "timeout please",
            session_id="t-timeout",
            owner_key="owner-timeout",
        )
    ]
    assert saved, "expected best-effort checkpoint save on timeout"
    assert saved["state"].owner_key == "owner-timeout"
    assert int(saved["step_index"]) >= 1
    # May be timeout_fallback from outer path or final fallback event.
    assert events or saved
