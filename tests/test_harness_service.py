from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from app.agent.agent_loop import GuardedToolExecutor
from app.agent.experts.base import ToolCallingExpert
from app.agent.harness.context import ContextBuilder
from app.agent.harness.loop import HarnessService
from app.agent.harness.registry import HarnessToolRegistry
from app.agent.harness.state import HarnessLimits
from app.agent.harness.subagent import create_delegate_tool
from app.api.assistant import assistant
from app.core.llm_client import LLMResponse, LLMStreamChunk, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.models.request import ChatRequest
from app.services.conversation_service import ConversationService
from app.services.router_service import RouteDecision, RouterService


@dataclass
class FakeConversationService:
    turns: list[dict]

    def get_turns(self, owner_key: str, session_id: str) -> list[dict]:
        return self.turns


class FakeRouter:
    def __init__(self, route: str = "diagnosis") -> None:
        self.route = route

    async def _resolve_route(self, message: str) -> RouteDecision:
        return RouteDecision(route=self.route, reason="fake_focus", confidence=0.8)


class FakeLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs})
        return self.responses.pop(0)

    async def stream_complete(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": {**kwargs, "stream": True}})
        response = self.responses.pop(0)
        content = response.content
        midpoint = max(1, len(content) // 2)
        for chunk in (content[:midpoint], content[midpoint:]):
            if chunk:
                yield chunk

    async def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": {**kwargs, "stream": True}})
        response = self.responses.pop(0)
        content = response.content
        midpoint = max(1, len(content) // 2)
        for chunk in (content[:midpoint], content[midpoint:]):
            if chunk:
                yield LLMStreamChunk(content=chunk)
        yield LLMStreamChunk(response=response)

    async def aclose(self) -> None:
        pass


class FakeExpert:
    async def run(self, *, message: str, session_id: str, trace_id: str, context: str = ""):
        yield {
            "type": "agent_event",
            "agent": "metric_expert",
            "stage": "start",
            "status": "in_progress",
            "summary": f"context={context}",
        }
        yield {"type": "content", "data": f"delegated:{message}"}


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


class FakeStreamService:
    def __init__(self) -> None:
        self.calls = []

    async def stream(self, message: str, session_id: str, owner_key: str = ""):
        self.calls.append(
            {"message": message, "session_id": session_id, "owner_key": owner_key}
        )
        yield {
            "type": "complete",
            "route": "fake",
            "answer": "ok",
            "case_id": "",
            "events": [],
        }


class FakeKnowledgeFallbackExpert:
    def __init__(self, *, answer: str = "knowledge fallback answer", raise_error: bool = False) -> None:
        self.answer = answer
        self.raise_error = raise_error
        self.calls = []

    async def run(self, *, message: str, session_id: str, trace_id: str, context: str = ""):
        self.calls.append(
            {"message": message, "session_id": session_id, "trace_id": trace_id, "context": context}
        )
        if self.raise_error:
            raise RuntimeError("knowledge down")
        yield {
            "type": "agent_event",
            "agent": "knowledge_expert",
            "stage": "start",
            "status": "in_progress",
            "summary": "knowledge fallback started",
        }
        if self.answer:
            yield {"type": "content", "data": self.answer, "agent": "knowledge_expert"}


def test_router_service_status_check_uses_metric_hint_without_shortcut():
    decision = RouterService().route_message("check cpu metrics")

    assert decision.route == "diagnosis"
    assert decision.reason == "keyword_hints_semantic"
    assert decision.confidence == 0.3
    assert decision.hints == ("metric",)


def test_router_strong_metric_keyword_still_shortcuts():
    decision = RouterService().route_message("check prometheus metrics")

    assert decision.route == "metric"
    assert decision.reason == "matched_strong_metric_keyword"
    assert decision.confidence == 0.9
    assert decision.hints == ("metric",)


def test_router_conflicting_strong_keywords_go_to_semantic_with_hints():
    decision = RouterService().route_message("prometheus metrics after deploy")

    assert decision.route == "diagnosis"
    assert decision.reason == "keyword_hints_semantic"
    assert decision.hints == ("metric", "change")


@pytest.mark.asyncio
async def test_router_injects_keyword_hints_into_semantic_prompt():
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content='{"route":"knowledge","reason":"generic how-to","confidence":0.86}',
                raw={},
                usage={"total_tokens": 5},
            )
        ]
    )
    router = RouterService(llm_client=fake_llm)

    decision = await router._resolve_route("how to troubleshoot high cpu usage")

    assert decision.route == "knowledge"
    assert decision.reason == "llm_semantic_knowledge"
    assert decision.hints == ("metric",)
    system_prompt = fake_llm.calls[0]["messages"][0].content
    user_prompt = fake_llm.calls[0]["messages"][-1].content
    assert "generic how-to" in system_prompt
    assert "prefer knowledge even if resource words" in system_prompt
    assert "Choose metric only when the user wants to inspect current monitoring data" in system_prompt
    assert "Keyword route hints: metric" in user_prompt
    assert "Do not choose metric solely because" in user_prompt
    assert "without a concrete target or time window, prefer knowledge" in user_prompt


def test_router_keyword_tiering_can_be_disabled(monkeypatch):
    monkeypatch.setattr("app.services.router_service.config.router_keyword_tiering_enabled", False)

    decision = RouterService().route_message("check cpu metrics")

    assert decision.route == "metric"
    assert decision.reason == "matched_metric_keyword"


@pytest.mark.asyncio
async def test_harness_registry_scopes_mcp_tools_by_route(monkeypatch):
    calls = []

    async def fake_collect_tools(local_tools, *, mcp_server=None):
        calls.append(mcp_server)
        return list(local_tools)

    monkeypatch.setattr("app.agent.harness.registry.config.harness_mcp_enabled", True)
    monkeypatch.setattr("app.agent.harness.registry.collect_tools", fake_collect_tools)

    registry = HarnessToolRegistry()
    for route in ("knowledge", "metric", "log", "diagnosis"):
        await registry.collect(
            route=route,
            session_id="trace-route",
            trace_id="trace-route",
            context_getter=lambda: "",
        )

    assert calls == [None, "monitor", "cls", ("monitor", "cls")]


class FakeSearchResult:
    def __init__(
        self,
        *,
        content: str,
        source: str = "runbook.md",
        score: float = 0.42,
        rank: int = 1,
    ) -> None:
        self.content = content
        self.source = source
        self.score = score
        self.rank = rank


class FakeVectorSearcher:
    def __init__(self, results: list[FakeSearchResult]) -> None:
        self.results = results
        self.calls = []

    def search(self, query: str, top_k: int = 3):
        self.calls.append({"query": query, "top_k": top_k})
        return self.results


async def _drain_event_source_response(response) -> None:
    async for _ in response.body_iterator:
        pass


@pytest.mark.asyncio
async def test_assistant_stream_uses_raw_session_id_for_harness(monkeypatch):
    fake_harness = FakeStreamService()
    monkeypatch.setattr("app.api.assistant.harness_service", fake_harness)
    monkeypatch.setattr("app.api.assistant._persist_turn", lambda *args, **kwargs: None)

    response = await assistant(
        ChatRequest(id="visible-session", question="check cpu"),
        owner_key="owner-1",
    )
    await _drain_event_source_response(response)

    assert fake_harness.calls[0]["session_id"] == "visible-session"
    assert fake_harness.calls[0]["owner_key"] == "owner-1"


@pytest.mark.asyncio
async def test_assistant_stream_always_uses_harness_when_flag_is_false(monkeypatch):
    fake_harness = FakeStreamService()
    monkeypatch.setattr("app.api.assistant.harness_service", fake_harness)
    monkeypatch.setattr("app.api.assistant._persist_turn", lambda *args, **kwargs: None)

    response = await assistant(
        ChatRequest(id="visible-session", question="check memory"),
        owner_key="owner-1",
    )
    await _drain_event_source_response(response)

    assert fake_harness.calls[0]["session_id"] == "visible-session"
    assert fake_harness.calls[0]["owner_key"] == "owner-1"


@pytest.mark.asyncio
async def test_harness_error_falls_back_to_knowledge_expert():
    class FailingRouter:
        async def _resolve_route(self, message: str) -> RouteDecision:
            raise RuntimeError("router down")

    fallback_expert = FakeKnowledgeFallbackExpert(answer="knowledge fallback answer")
    service = HarnessService(
        router=FailingRouter(),
        llm_client=FakeLLM([]),
        tools=[],
        fallback_expert=fallback_expert,
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream("check knowledge", session_id="trace-fallback", owner_key="user-1")
    ]

    assert any(event.get("stage") == "route" for event in events[:1])
    assert any(event.get("stage") == "error" for event in events)
    assert any(event.get("stage") == "fallback_start" for event in events)
    assert any(event.get("agent") == "knowledge_expert" for event in events)
    assert fallback_expert.calls[0]["session_id"] == "trace-fallback"
    assert events[-1]["type"] == "complete"
    assert events[-1]["route"] == "knowledge"
    assert events[-1]["route_reason"] == "harness_error:fallback_knowledge_expert"
    assert events[-1]["answer"] == "knowledge fallback answer"


@pytest.mark.asyncio
async def test_harness_fallback_uses_raw_vector_when_knowledge_is_empty():
    class FailingRouter:
        async def _resolve_route(self, message: str) -> RouteDecision:
            raise RuntimeError("router down")

    fallback_expert = FakeKnowledgeFallbackExpert(answer="")
    vector_searcher = FakeVectorSearcher(
        [FakeSearchResult(content="runbook fallback snippet for cpu incident")]
    )
    service = HarnessService(
        router=FailingRouter(),
        llm_client=FakeLLM([]),
        tools=[],
        fallback_expert=fallback_expert,
        vector_searcher=vector_searcher,
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "check cpu runbook", session_id="trace-vector", owner_key="user-1"
        )
    ]

    assert vector_searcher.calls == [{"query": "check cpu runbook", "top_k": 3}]
    assert any(event.get("stage") == "knowledge_fallback_empty" for event in events)
    assert any(event.get("stage") == "raw_vector_fallback_complete" for event in events)
    assert events[-2]["type"] == "content"
    assert "runbook fallback snippet" in events[-2]["data"]
    assert events[-1]["route_reason"] == "harness_error:fallback_raw_vector"


@pytest.mark.asyncio
async def test_assistant_harness_two_turn_flow_persists_and_reloads_history(
    tmp_path, monkeypatch
):
    conversation_service = ConversationService(tmp_path / "conversation.db")
    first_answer = "first persisted answer at 10:03"
    second_answer = "second answer mentioning CPU history"
    first_question = "check service=aiops-assistant-api CPU"
    second_question = "what happened before for service=aiops-assistant-api"
    fake_llm = FakeLLM(
        [
            LLMResponse(content=first_answer, raw={}, usage={"total_tokens": 5}),
            LLMResponse(content=second_answer, raw={}, usage={"total_tokens": 6}),
        ]
    )
    harness = HarnessService(
        context_builder=ContextBuilder(history_max_turns=3),
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )
    monkeypatch.setattr("app.api.assistant.harness_service", harness)
    monkeypatch.setattr("app.api.assistant.conversation_service", conversation_service)
    monkeypatch.setattr(
        "app.agent.harness.context.conversation_service", conversation_service
    )
    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_corrective_verify_enabled", False
    )

    first_response = await assistant(
        ChatRequest(id="visible-session", question=first_question),
        owner_key="owner-1",
    )
    await _drain_event_source_response(first_response)
    second_response = await assistant(
        ChatRequest(id="visible-session", question=second_question),
        owner_key="owner-1",
    )
    await _drain_event_source_response(second_response)

    turns = conversation_service.get_turns("owner-1", "visible-session")
    assert [turn["user_message"] for turn in turns] == [first_question, second_question]
    assert [turn["assistant_answer"] for turn in turns] == [first_answer, second_answer]

    second_messages = fake_llm.calls[1]["messages"]
    assert [message.role for message in second_messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert [message.content for message in second_messages[1:]] == [
        first_question,
        first_answer,
        second_question,
    ]


@pytest.mark.asyncio
async def test_harness_stream_executes_tool_and_completes():
    tool = RuntimeTool(
        name="echo_tool",
        description="Echoes input for deterministic tests.",
        handler=lambda arguments: f"echo:{arguments['text']}",
    )
    final_answer = "final diagnosis answer"
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(id="call-1", name="echo_tool", arguments={"text": "hello"})
                ],
                usage={"total_tokens": 10},
            ),
            LLMResponse(content=final_answer, raw={}, usage={"total_tokens": 5}),
        ]
    )
    service = HarnessService(
        router=FakeRouter(),
        llm_client=fake_llm,
        tools=[tool],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    raw_events = [
        event
        async for event in service.stream(
            "run echo hello", session_id="trace-1", owner_key="user-1"
        )
    ]

    progress_stages = [
        event.get("stage")
        for event in raw_events
        if event.get("type") == "agent_event" and event.get("status") == "in_progress"
    ]
    expected_progress = [
        "route",
        "context",
        "planning",
        "model_decision",
        "model_decision",
        "verify",
        "report",
    ]
    cursor = 0
    for stage in progress_stages:
        if cursor < len(expected_progress) and stage == expected_progress[cursor]:
            cursor += 1
    assert cursor == len(expected_progress)

    complete_event = dict(raw_events[-1])
    complete_event["events"] = [
        event
        for event in complete_event["events"]
        if not (event.get("status") == "in_progress" and event.get("stage") != "start")
    ]
    events = [
        next(event for event in raw_events if event.get("type") == "route_event"),
        next(event for event in raw_events if event.get("stage") == "start"),
        next(event for event in raw_events if event.get("stage") == "plan"),
        next(event for event in raw_events if event.get("type") == "tool_event"),
        next(
            event
            for event in raw_events
            if event.get("stage") == "verify" and event.get("status") != "in_progress"
        ),
        next(event for event in raw_events if event.get("type") == "content"),
        next(event for event in raw_events if event.get("stage") == "complete"),
        complete_event,
    ]

    assert [event["type"] for event in events] == [
        "route_event",
        "agent_event",
        "agent_event",
        "tool_event",
        "agent_event",
        "content",
        "agent_event",
        "complete",
    ]
    assert events[2]["stage"] == "plan"
    assert events[2]["payload"]["required_evidence"]
    assert any("echo_tool" in todo for todo in events[2]["payload"]["todos"])
    assert events[3]["tool"] == "echo_tool"
    assert events[4]["stage"] == "verify"
    assert events[4]["status"] == "completed"
    assert events[4]["payload"]["confidence"] == "medium"
    assert events[4]["payload"]["gaps"] == []
    assert events[-1]["answer"] == final_answer
    assert events[-1]["route"] == "diagnosis"
    assert [event["type"] for event in events[-1]["events"]] == [
        "route_event",
        "agent_event",
        "agent_event",
        "tool_event",
        "agent_event",
        "agent_event",
    ]
    first_messages = fake_llm.calls[0]["messages"]
    assert "echo_tool" in first_messages[0].content
    assert first_messages[-1].content == "run echo hello"
    content_events = [event for event in raw_events if event.get("type") == "content"]
    assert len(content_events) >= 2
    assert "".join(str(event["data"]) for event in content_events) == final_answer


@pytest.mark.asyncio
async def test_harness_verify_marks_answer_without_tool_evidence_as_degraded():
    fake_llm = FakeLLM(
        [LLMResponse(content="answer without evidence", raw={}, usage={"total_tokens": 5})]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "check service=aiops-assistant-api CPU",
            session_id="trace-no-tool",
            owner_key="user-1",
        )
    ]

    plan_event = next(event for event in events if event.get("stage") == "plan")
    verify_event = next(
        event
        for event in events
        if event.get("stage") == "verify" and event.get("status") != "in_progress"
    )
    assert plan_event["payload"]["focus_route"] == "metric"
    assert plan_event["payload"]["available_tools"] == []
    assert any("当前无可用工具" in todo for todo in plan_event["payload"]["todos"])
    assert verify_event["status"] == "degraded"
    assert verify_event["payload"]["confidence"] == "low"
    assert verify_event["payload"]["evidence_count"] == 0
    assert "未产生成功工具证据" in verify_event["payload"]["gaps"]
    assert "计划要求的证据尚未满足：指标曲线或告警、异常时间窗口" in verify_event["payload"]["gaps"][-1]


@pytest.mark.asyncio
async def test_harness_asks_for_missing_metric_subject_after_plan():
    fake_llm = FakeLLM([])
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "cpu",
            session_id="trace-missing-subject",
            owner_key="user-1",
        )
    ]

    assert fake_llm.calls == []
    clarify_event = next(
        event for event in events if event.get("stage") == "clarify_missing_params"
    )
    assert clarify_event["status"] == "degraded"
    assert "主机/IP/instance/服务/pod/job 任一标识" in clarify_event["payload"]["missing_params"]
    assert not any(event.get("stage") == "verify" for event in events)
    content_events = [event for event in events if event.get("type") == "content"]
    assert content_events
    assert "主机/IP/instance/服务/pod/job 任一标识" in content_events[-1]["data"]
    assert events[-1]["type"] == "complete"
    assert events[-1]["answer"] == content_events[-1]["data"]


@pytest.mark.asyncio
async def test_harness_delays_missing_param_clarification_until_after_tool_attempt():
    def handler(arguments):
        raise RuntimeError("401 unauthorized: target unavailable")

    tool = RuntimeTool(
        name="metric_probe",
        description="Probe metric context.",
        handler=handler,
    )
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="",
                raw={},
                tool_calls=[ToolCall(id="call-probe", name="metric_probe", arguments={})],
                usage={"total_tokens": 5},
            ),
            LLMResponse(
                content="Need a concrete target before giving a conclusion.",
                raw={},
                usage={"total_tokens": 3},
            ),
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[tool],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "cpu memory",
            session_id="trace-delayed-clarify",
            owner_key="user-1",
        )
    ]

    assert len(fake_llm.calls) == 2
    tool_index = next(
        index for index, event in enumerate(events) if event.get("type") == "tool_event"
    )
    clarify_index = next(
        index
        for index, event in enumerate(events)
        if event.get("stage") == "clarify_missing_params"
    )
    assert tool_index < clarify_index
    assert events[tool_index]["tool"] == "metric_probe"
    assert events[tool_index]["status"] == "failed"
    assert not any(event.get("stage") == "verify" for event in events)
    assert events[-1]["type"] == "complete"
    assert events[-1]["answer"]


@pytest.mark.asyncio
async def test_harness_stream_injects_delegate_events_into_main_timeline():
    delegate_tool = create_delegate_tool(
        session_id="trace-delegate",
        trace_id="trace-delegate",
        context_getter=lambda: "parent context",
        expert_getter=lambda route: FakeDelegateExpert(),
    )
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(
                        id="call-delegate",
                        name="delegate_to_expert",
                        arguments={"expert": "metric", "subtask": "check CPU"},
                    )
                ],
                usage={"total_tokens": 12},
            ),
            LLMResponse(
                content="delegated evidence summarized",
                raw={},
                usage={"total_tokens": 6},
            ),
        ]
    )
    service = HarnessService(
        router=FakeRouter(),
        llm_client=fake_llm,
        tools=[delegate_tool],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "delegate metric check", session_id="trace-delegate", owner_key="user-1"
        )
    ]

    assert any(
        event.get("type") == "tool_event"
        and event.get("tool") == "delegate_to_expert"
        for event in events
    )
    child_events = [
        event for event in events if event.get("agent") == "metric_expert"
    ]
    assert [event["type"] for event in child_events] == ["agent_event", "tool_event"]
    assert child_events[0]["span_id"].startswith("delegate:call-delegate:")
    assert child_events[0]["payload"]["parent_tool_call_id"] == "call-delegate"
    assert any(event.get("agent") == "metric_expert" for event in events[-1]["events"])


def test_context_builder_includes_recent_history(monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.context.conversation_service",
        FakeConversationService(
            [
                {
                    "turn_index": 0,
                    "user_message": "old knowledge question",
                    "assistant_answer": "old knowledge answer",
                    "route": "knowledge",
                },
                {
                    "turn_index": 1,
                    "user_message": "recent CPU question",
                    "assistant_answer": "recent CPU answer",
                    "route": "metric",
                },
            ]
        ),
    )
    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)

    context = ContextBuilder(history_max_turns=1).build(
        message="current metric question",
        owner_key="user-1",
        session_id="session-1",
        tools=[],
        focus_hint="metric focus",
    )

    assert [message.role for message in context.history_messages] == ["user", "assistant"]
    assert context.history_messages[0].content == "recent CPU question"
    assert context.history_messages[1].content == "recent CPU answer"
    assert "recent CPU answer" not in context.system_prompt
    assert "metric focus" in context.system_prompt
    assert "current metric question" not in context.system_prompt


def test_context_builder_reads_real_sqlite_history_by_raw_session_id(tmp_path, monkeypatch):
    service = ConversationService(tmp_path / "conversation.db")
    service.append_turn(
        owner_key="owner-1",
        session_id="visible-session",
        user_message="previous CPU question",
        assistant_answer="CPU was high at 10:03",
        route="metric",
    )
    monkeypatch.setattr("app.agent.harness.context.conversation_service", service)
    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)

    raw_context = ContextBuilder(history_max_turns=3).build(
        message="follow up question",
        owner_key="owner-1",
        session_id="visible-session",
        tools=[],
    )
    scoped_context = ContextBuilder(history_max_turns=3).build(
        message="follow up question",
        owner_key="owner-1",
        session_id="owner:owner-1:visible-session",
        tools=[],
    )

    assert [message.content for message in raw_context.history_messages] == [
        "previous CPU question",
        "CPU was high at 10:03",
    ]
    assert scoped_context.history_messages == []


@pytest.mark.asyncio
async def test_guarded_tool_executor_truncates_large_output():
    tool = RuntimeTool(
        name="large_tool",
        description="Returns large output.",
        handler=lambda arguments: "x" * 20,
    )
    executor = GuardedToolExecutor(timeout_seconds=1, max_output_chars=5)

    results = await executor.execute(
        [ToolCall(id="call-large", name="large_tool", arguments={})],
        [tool],
    )

    assert results[0].success is True
    assert results[0].content.startswith("xxxxx")
    assert len(results[0].content) > 5


@pytest.mark.asyncio
async def test_delegate_tool_runs_selected_expert():
    tool = create_delegate_tool(
        session_id="session-1",
        trace_id="trace-1",
        context_getter=lambda: "parent context",
        expert_getter=lambda route: FakeExpert(),
    )

    result = await tool.run({"expert": "metric", "subtask": "check CPU"})

    assert result["expert"] == "metric"
    assert result["status"] == "completed"
    assert result["answer"] == "delegated:check CPU"
    assert result["events"][0]["agent"] == "metric_expert"


@pytest.mark.asyncio
async def test_harness_corrective_verify_prepends_gap_notice():
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="CPU saturation is about 80 percent",
                raw={},
                usage={"total_tokens": 5},
            ),
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "check service=aiops-assistant-api CPU",
            session_id="trace-correct",
            owner_key="user-1",
        )
    ]

    content_events = [event for event in events if event.get("type") == "content"]
    assert content_events
    streamed_answer = "".join(str(event["data"]) for event in content_events)
    assert "CPU saturation is about 80 percent" in streamed_answer
    assert events[-1]["answer"].startswith(">")
    assert "未产生成功工具证据" in events[-1]["answer"]
    assert streamed_answer in events[-1]["answer"]


@pytest.mark.asyncio
async def test_guarded_tool_executor_retries_transient_error():
    calls = {"n": 0}

    def handler(arguments):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("upstream 503 temporarily unavailable")
        return "recovered"

    tool = RuntimeTool(name="flaky_tool", description="Flaky tool.", handler=handler)
    executor = GuardedToolExecutor(
        timeout_seconds=1, max_output_chars=0, max_retries=2, retry_backoff_seconds=0
    )

    results = await executor.execute(
        [ToolCall(id="call-flaky", name="flaky_tool", arguments={})], [tool]
    )

    assert calls["n"] == 2
    assert results[0].success is True
    assert results[0].content == "recovered"


@pytest.mark.asyncio
async def test_guarded_tool_executor_does_not_retry_auth_error():
    calls = {"n": 0}

    def handler(arguments):
        calls["n"] += 1
        raise RuntimeError("401 unauthorized: invalid token")

    tool = RuntimeTool(name="auth_tool", description="Auth tool.", handler=handler)
    executor = GuardedToolExecutor(
        timeout_seconds=1, max_output_chars=0, max_retries=2, retry_backoff_seconds=0
    )

    results = await executor.execute(
        [ToolCall(id="call-auth", name="auth_tool", arguments={})], [tool]
    )

    assert calls["n"] == 1
    assert results[0].success is False
    assert "401" in results[0].content


@pytest.mark.asyncio
async def test_harness_stream_stops_on_no_progress():
    tool = RuntimeTool(
        name="echo_tool",
        description="Echoes input for deterministic tests.",
        handler=lambda arguments: f"echo:{arguments['text']}",
    )
    repeated_call = ToolCall(id="call-loop", name="echo_tool", arguments={"text": "same"})
    final_answer = "stopped after repeated tool call"
    fake_llm = FakeLLM(
        [
            LLMResponse(content="", raw={}, tool_calls=[repeated_call], usage={"total_tokens": 4}),
            LLMResponse(content="", raw={}, tool_calls=[repeated_call], usage={"total_tokens": 4}),
            LLMResponse(content=final_answer, raw={}, usage={"total_tokens": 3}),
        ]
    )
    service = HarnessService(
        router=FakeRouter(),
        llm_client=fake_llm,
        tools=[tool],
        limits=HarnessLimits(
            max_steps=6, token_budget=10000, timeout_seconds=5, no_progress_limit=1
        ),
    )

    events = [
        event
        async for event in service.stream(
            "repeat the same check", session_id="trace-stuck", owner_key="user-1"
        )
    ]

    tool_events = [event for event in events if event.get("type") == "tool_event"]
    assert len(tool_events) == 1
    assert any(
        event.get("stage") == "no_progress" and event.get("status") == "degraded"
        for event in events
    )
    assert events[-1]["answer"] == final_answer


@pytest.mark.asyncio
async def test_harness_stream_runs_log_pipeline_for_large_log_output():
    log_lines = [f"2026-06-20 10:00:0{i % 10} ERROR upstream timeout id={i}" for i in range(60)]
    log_payload = json.dumps({"logs": log_lines}, ensure_ascii=False)
    log_tool = RuntimeTool(
        name="search_cls_log",
        description="Search large logs.",
        handler=lambda arguments: log_payload,
    )
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(
                        id="call-log",
                        name="search_cls_log",
                        arguments={"query": "error"},
                    )
                ],
                usage={"total_tokens": 8},
            ),
            LLMResponse(content="log pipeline final answer", raw={}, usage={"total_tokens": 5}),
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="log"),
        llm_client=fake_llm,
        tools=[log_tool],
        limits=HarnessLimits(max_steps=3, token_budget=100000, timeout_seconds=5),
    )
    service.tool_executor.max_output_chars = 80

    events = [
        event
        async for event in service.stream(
            "check service=aiops-assistant-api logs",
            session_id="trace-log",
            owner_key="user-1",
        )
    ]

    assert any(event.get("stage") == "log_pipeline" for event in events)
    second_call_messages = fake_llm.calls[1]["messages"]
    tool_messages = [message for message in second_call_messages if message.role == "tool"]
    assert tool_messages
    assert "ERROR upstream timeout" in tool_messages[-1].content
    assert "id=59" not in tool_messages[-1].content


@pytest.mark.asyncio
async def test_harness_llm_planner_overrides_rule_plan(monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.planner.config.harness_llm_planning_enabled", True
    )
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content=(
                    '{"todos": ["LLM step one", "LLM step two"], '
                    '"required_evidence": ["LLM evidence"], "required_params": []}'
                ),
                raw={},
                usage={"total_tokens": 7},
            ),
            LLMResponse(content="planner final answer", raw={}, usage={"total_tokens": 3}),
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "check cpu for service=api", session_id="trace-plan", owner_key="user-1"
        )
    ]

    plan_event = next(event for event in events if event.get("stage") == "plan")
    assert plan_event["payload"]["todos"] == ["LLM step one", "LLM step two"]
    assert plan_event["payload"]["required_evidence"] == ["LLM evidence"]


@pytest.mark.asyncio
async def test_harness_llm_verifier_refines_status(monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.verifier.config.harness_llm_verify_enabled", True
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_corrective_verify_enabled", False
    )
    fake_llm = FakeLLM(
        [
            LLMResponse(content="answer to verify", raw={}, usage={"total_tokens": 5}),
            LLMResponse(
                content=(
                    '{"status": "failed", "confidence": "low", '
                    '"gaps": ["LLM says evidence is missing"], '
                    '"summary": "LLM refined failure"}'
                ),
                raw={},
                usage={"total_tokens": 6},
            ),
        ]
    )
    service = HarnessService(
        router=FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "check service=aiops-assistant-api cpu",
            session_id="trace-verify",
            owner_key="user-1",
        )
    ]

    verify_event = next(
        event
        for event in events
        if event.get("stage") == "verify" and event.get("status") != "in_progress"
    )
    assert verify_event["status"] == "failed"
    assert verify_event["payload"]["confidence"] == "low"
    assert "LLM says evidence is missing" in verify_event["payload"]["gaps"]
    assert verify_event["summary"] == "LLM refined failure"


@pytest.mark.asyncio
async def test_expert_run_streams_tool_then_answer_via_shared_kernel():
    """Locks tool execution, transformed tool content, and streamed final answer."""
    tool = RuntimeTool(
        name="echo_tool",
        description="Echoes input for deterministic tests.",
        handler=lambda arguments: f"echo:{arguments['text']}",
    )
    final_answer = "expert final answer"
    fake_llm = FakeLLM(
        [
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(id="call-1", name="echo_tool", arguments={"text": "hi"})
                ],
                usage={"total_tokens": 4},
            ),
            LLMResponse(content=final_answer, raw={}, usage={"total_tokens": 3}),
        ]
    )

    class _Expert(ToolCallingExpert):
        agent_label = "test_expert"
        display_name = "Test Expert"
        system_prompt = "system"

        async def get_tools(self):
            return [tool]

        async def transform_tool_result(
            self, *, tool_name, content, raw, events_sink, trace_id, llm_client
        ):
            events_sink.append(
                {
                    "type": "agent_event",
                    "agent": self.agent_label,
                    "stage": "enrich",
                    "status": "completed",
                    "summary": "transformed",
                }
            )
            return f"[T]{content}"

    expert = _Expert()
    expert._new_llm_client = lambda: fake_llm  # inject deterministic client

    events = [
        event async for event in expert.run(message="hi", session_id="s", trace_id="t")
    ]

    assert [event["type"] for event in events] == [
        "agent_event",  # start
        "agent_event",  # transform-injected pre event
        "tool_event",
        "content",
        "content",
        "agent_event",  # complete
    ]
    assert events[0]["stage"] == "start"
    assert events[1]["stage"] == "enrich"
    assert events[2]["tool"] == "echo_tool"
    assert events[2]["agent"] == "test_expert"
    assert "duration_ms" in events[2]  # experts stamp per-tool duration
    streamed_answer = "".join(
        str(event["data"]) for event in events if event.get("type") == "content"
    )
    assert streamed_answer == final_answer
    assert events[-1]["stage"] == "complete"

    tool_messages = [
        message for message in fake_llm.calls[1]["messages"] if message.role == "tool"
    ]
    assert tool_messages[-1].content == "[T]echo:hi"

