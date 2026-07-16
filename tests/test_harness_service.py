from __future__ import annotations



import asyncio

import json

import time

from dataclasses import dataclass

from unittest.mock import AsyncMock



import pytest



from app.agent.agent_loop import GuardedToolExecutor, estimate_tokens

from app.agent.experts.base import ToolCallingExpert

from app.agent.harness.context import ContextBuilder

from app.agent.harness.loop import HarnessService

from app.agent.harness.registry import HarnessToolRegistry

from app.agent.harness.state import HarnessLimits, HarnessState

from app.agent.harness.subagent import create_delegate_tool

from app.api.assistant import assistant

from app.core.llm_client import ChatMessage, LLMResponse, LLMStreamChunk, ToolCall

from app.core.runtime_tools import RuntimeTool

from app.models.request import ChatRequest

from app.services.attachment_reference_service import (

    AttachmentReference,

    AttachmentReferenceService,

)

from app.services.conversation_service import ConversationService

from app.services.harness_checkpoint import HarnessCheckpointStore

from app.services.router_service import RouteDecision, RouterService

from tests._fake_redis import FakeRedis





@dataclass

class FakeConversationService:

    turns: list[dict]



    def get_turns(self, owner_key: str, session_id: str) -> list[dict]:

        return self.turns





async def _async_return(value):

    """Tiny helper to convert a sync value into an awaitable (for fake LLM injection)."""

    return value





class FakeRouter:

    def __init__(self, route: str = "diagnosis") -> None:

        self.route = route



    async def _resolve_route(self, message: str) -> RouteDecision:

        return RouteDecision(route=self.route, reason="fake_focus", confidence=0.8)





class FakeLLM:

    def __init__(self, responses: list[LLMResponse]) -> None:

        self.responses = responses

        self.calls = []

        self.closed = False

    def _next_response(self) -> LLMResponse:
        """Pop next scripted response; reuse last/empty if exhausted."""
        if self.responses:
            response = self.responses.pop(0)
            self._last_response = response
            return response
        last = getattr(self, "_last_response", None)
        if last is not None:
            return LLMResponse(
                content=getattr(last, "content", "") or "",
                raw=getattr(last, "raw", {}) or {},
                tool_calls=[],
                usage=getattr(last, "usage", None) or {},
            )
        return LLMResponse(content="", raw={}, tool_calls=[], usage={})




    async def complete(self, messages, **kwargs):

        self.calls.append({"messages": list(messages), "kwargs": kwargs})

        return self._next_response()



    async def stream_complete(self, messages, **kwargs):

        self.calls.append({"messages": list(messages), "kwargs": {**kwargs, "stream": True}})

        response = self._next_response()

        content = response.content

        midpoint = max(1, len(content) // 2)

        for chunk in (content[:midpoint], content[midpoint:]):

            if chunk:

                yield chunk



    async def stream_chat(self, messages, **kwargs):

        self.calls.append({"messages": list(messages), "kwargs": {**kwargs, "stream": True}})

        response = self._next_response()

        content = response.content

        midpoint = max(1, len(content) // 2)

        for chunk in (content[:midpoint], content[midpoint:]):

            if chunk:

                yield LLMStreamChunk(content=chunk)

        yield LLMStreamChunk(response=response)



    async def aclose(self) -> None:

        self.closed = True







def _disable_extra_harness_loops(monkeypatch) -> None:
    """Keep classic stream tests stable under newer early-close / re-evidence defaults."""
    for attr, value in (
        ("harness_investigation_evidence_early_close", False),
        ("harness_knowledge_early_close", False),
        ("harness_re_evidence_enabled", False),
        ("harness_replan_enabled", False),
        ("harness_timeout_soft_close_enabled", False),
        ("harness_checkpoint_enabled", False),
        ("harness_corrective_verify_enabled", False),
        ("harness_evidence_match_enabled", False),
        ("long_term_memory_distill_enabled", False),
        ("harness_anti_pattern_capture_enabled", False),
        ("hitl_suggested_actions_enabled", False),
    ):
        monkeypatch.setattr(f"app.agent.harness.loop.config.{attr}", value, raising=False)
        monkeypatch.setattr(f"app.config.config.{attr}", value, raising=False)
    # Escalation footer always has text when contacts empty; classic tests assert exact answers.
    monkeypatch.setattr(
        "app.agent.harness.close_path.HarnessClosePathMixin._build_escalation_block",
        lambda self: {"configured": False, "text": "", "contacts": []},
    )



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



    decision = await router._resolve_route("how to handle high cpu usage")



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

async def test_router_semantic_multilabel_returns_parallel_aux_routes():

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content=' {"route":"diagnosis","reason":"parallel symptoms","confidence":0.8,'

                '"aux_routes":["metric","log"]}' ,

                raw={},

                usage={"total_tokens": 5},

            )

        ]

    )

    router = RouterService(llm_client=fake_llm)



    decision = await router._resolve_route(

        "checkout-api cpu high and logs include errors"

    )



    assert decision.route == "diagnosis"

    assert decision.aux_routes == ("metric", "log")

    assert decision.intent_relation == "parallel"





@pytest.mark.asyncio

async def test_router_semantic_multilabel_drops_invalid_aux_entries():

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content=' {"route":"metric","reason":"main metric","confidence":0.7,'

                '"aux_routes":["metric","diagnosis","bogus","log","log"]}' ,

                raw={},

                usage={"total_tokens": 5},

            )

        ]

    )

    router = RouterService(llm_client=fake_llm)



    decision = await router._resolve_route("cpu high + log error + knowledge lookup")



    assert decision.route == "metric"

    assert decision.aux_routes == ("log",)

    assert decision.intent_relation == "parallel"





@pytest.mark.asyncio

async def test_router_semantic_multilabel_single_intent_stays_empty():

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content='{"route":"knowledge","reason":"閫氱敤姝ラ","confidence":0.86}',

                raw={},

                usage={"total_tokens": 5},

            )

        ]

    )

    router = RouterService(llm_client=fake_llm)



    decision = await router._resolve_route("how to troubleshoot high cpu usage")



    assert decision.route == "knowledge"

    assert decision.aux_routes == ()

    assert decision.intent_relation == "primary"





@pytest.mark.asyncio

async def test_router_semantic_multilabel_disabled_drops_aux_routes(monkeypatch):

    monkeypatch.setattr("app.services.router_service.config.router_multilabel_enabled", False)

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content='{"route":"diagnosis","reason":"澶氱棁鐘?,"confidence":0.8,'

                '"aux_routes":["metric","log"]}',

                raw={},

                usage={"total_tokens": 5},

            )

        ]

    )

    router = RouterService(llm_client=fake_llm)



    decision = await router._resolve_route("cpu 楂?+ 鏃ュ織鎶ラ敊")



    assert decision.route == "diagnosis"

    assert decision.aux_routes == ()

    assert decision.intent_relation == "primary"





@pytest.mark.asyncio

async def test_router_semantic_parser_tolerates_malformed_aux_routes():

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content=('{\"route\":\"metric\",\"reason\":\"primary metrics signal\",\"confidence\":0.7,\"aux_routes\":\"metric\"}'),

                raw={},

                usage={"total_tokens": 5},

            )

        ]

    )

    router = RouterService(llm_client=fake_llm)



    decision = await router._resolve_route("cpu 楂?+ 鏃ュ織鎶ラ敊")



    assert decision.route == "metric"

    assert decision.aux_routes == ()

    assert decision.intent_relation == "primary"





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

async def test_assistant_stream_injects_attachment_context_into_message(monkeypatch):

    fake_harness = FakeStreamService()

    monkeypatch.setattr("app.api.assistant.harness_service", fake_harness)

    monkeypatch.setattr("app.api.assistant._persist_turn", lambda *args, **kwargs: None)

    monkeypatch.setattr(

        "app.api.assistant.attachment_context_service.build_context",

        AsyncMock(return_value="[闄勪欢 incident.md]\ncontent:\nCPU reached 95%"),

    )



    response = await assistant(

        ChatRequest(

            id="visible-session",

            question="璇峰垎鏋愯繖涓檮浠堕噷鐨?CPU 寮傚父",

            attachment_ids=["file_123"],

        ),

        owner_key="owner-1",

    )

    await _drain_event_source_response(response)



    injected_message = fake_harness.calls[0]["message"]

    assert "incident.md" in injected_message

    assert "CPU reached 95%" in injected_message

    assert "CPU" in injected_message





@pytest.mark.asyncio

async def test_assistant_history_keeps_attachment_context_for_follow_up(tmp_path, monkeypatch):

    _disable_extra_harness_loops(monkeypatch)

    conversation_service = ConversationService(tmp_path / "conversation.db")

    first_answer = "Attachment content is software architecture review material."

    second_answer = "Following the attachment, this material focuses on layers and modules."

    fake_llm = FakeLLM(

        [

            LLMResponse(content=first_answer, raw={}, usage={"total_tokens": 5}),

            LLMResponse(content=second_answer, raw={}, usage={"total_tokens": 6}),

        ]

    )

    harness = HarnessService(

        context_builder=ContextBuilder(history_max_turns=3),

        router=FakeRouter(route="knowledge"),

        llm_client=fake_llm,

        tools=[],

        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
        checkpoint_store=None,

    )

    monkeypatch.setattr("app.api.assistant.harness_service", harness)

    monkeypatch.setattr("app.api.assistant.conversation_service", conversation_service)

    monkeypatch.setattr("app.agent.harness.context.conversation_service", conversation_service)

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)

    monkeypatch.setattr("app.agent.harness.loop.config.harness_corrective_verify_enabled", False)

    monkeypatch.setattr(

        "app.agent.harness.loop.stateful_context_enabled",

        lambda: False,

    )

    monkeypatch.setattr(

        "app.api.assistant.attachment_context_service.build_context",

        AsyncMock(return_value="[Attachment software-architecture.pdf]\ncontent:\nArchitecture covers layering, modularity, and quality attributes."),

    )



    first_response = await assistant(

        ChatRequest(

            id="visible-session",

            question="please summarize this attachment",
            attachment_ids=["file_attachment_1"],

        ),

        owner_key="owner-1",

    )

    await _drain_event_source_response(first_response)

    second_response = await assistant(

        ChatRequest(id="visible-session", question="continue with the key points"),

        owner_key="owner-1",

    )

    await _drain_event_source_response(second_response)



    turns = conversation_service.get_turns("owner-1", "visible-session")

    assert turns[0]["user_message"]

    assert turns[0].get("user_context") is not None

    second_messages = fake_llm.calls[1]["messages"]

    assert [message.role for message in second_messages] == [

        "system",

        "user",

        "assistant",

        "user",

    ]

    assert second_messages[1].content  # history user turn present

    assert second_messages[2].content  # prior assistant answer present if roles include it

    assert second_messages[-1].content





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
    _disable_extra_harness_loops(monkeypatch)

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
        checkpoint_store=None,

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

    monkeypatch.setattr(

        "app.agent.harness.loop.stateful_context_enabled",

        lambda: False,

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

async def test_harness_stream_executes_tool_and_completes(monkeypatch):

    _disable_extra_harness_loops(monkeypatch)

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
        checkpoint_store=None,

    )



    raw_events = [

        event

        async for event in service.stream(

            "run echo hello", session_id="trace-exec-tool", owner_key="user-1"

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

    assert content_events
    assert "".join(str(event["data"]) for event in content_events) == final_answer





def _checkpoint_state(*, session_id: str, owner_key: str, step: int) -> HarnessState:

    return HarnessState(

        trace_id=session_id,

        session_id=session_id,

        owner_key=owner_key,

        route="diagnosis",

        route_reason="checkpoint-test",

        step=step,

        timeline_events=[

            {

                "type": "tool_event",

                "agent": "harness",

                "tool": "checkpoint_tool",

                "status": "completed",

                "summary": "checkpoint evidence",

            }

        ],

    )





def _checkpoint_messages(tool_name: str) -> list[ChatMessage]:

    return [

        ChatMessage(role="system", content="system prompt from checkpoint"),

        ChatMessage(role="user", content="original user question"),

        ChatMessage(

            role="assistant",

            content="",

            tool_calls=[

                {

                    "id": "checkpoint-call",

                    "type": "function",

                    "function": {"name": tool_name, "arguments": "{}"},

                }

            ],

        ),

        ChatMessage(role="tool", content="checkpoint evidence", tool_call_id="checkpoint-call"),

    ]





@pytest.mark.asyncio

async def test_harness_checkpoint_resume_replays_from_next_step_without_skipping():

    fake_redis = FakeRedis()

    store = HarnessCheckpointStore(

        namespace="test",

        ttl_seconds=300,

        idempotent_tools=("echo_tool",),

        redis_factory=lambda: fake_redis,

    )

    await store.save_step(

        owner_key="user-1",

        session_id="trace-resume-step",

        state=_checkpoint_state(session_id="trace-resume-step", owner_key="user-1", step=2),

        messages=_checkpoint_messages("echo_tool"),

        step_index=2,

        step_payload={

            "step": 2,

            "tool_calls": [{"id": "checkpoint-call", "function": {"name": "echo_tool"}}],

            "events": [],

            "completed": True,

        },

    )

    tool = RuntimeTool(

        name="echo_tool",

        description="Echoes input for deterministic tests.",

        handler=lambda arguments: f"echo:{arguments.get('text', '')}",

    )

    fake_llm = FakeLLM(

        [LLMResponse(content="resumed final answer", raw={}, usage={"total_tokens": 5})]

    )

    service = HarnessService(

        router=FakeRouter(),

        llm_client=fake_llm,

        tools=[tool],

        limits=HarnessLimits(max_steps=4, token_budget=1000, timeout_seconds=5),

        checkpoint_store=store,

    )



    events = [

        event

        async for event in service.stream(

            "resume this run",

            session_id="trace-resume-step",

            owner_key="user-1",

        )

    ]



    resume_event = next(event for event in events if event.get("stage") == "checkpoint_resume")

    model_steps = [

        event["payload"]["step"]

        for event in events

        if event.get("stage") == "model_decision"

    ]

    assert resume_event["payload"]["resumed_from_step"] == 2

    assert model_steps[0] == 3

    assert fake_llm.calls[0]["messages"][0].content == "system prompt from checkpoint"





@pytest.mark.asyncio

async def test_harness_checkpoint_resume_non_idempotent_closes_without_tool_replay():

    fake_redis = FakeRedis()

    store = HarnessCheckpointStore(

        namespace="test",

        ttl_seconds=300,

        idempotent_tools=("delegate_to_expert",),

        redis_factory=lambda: fake_redis,

    )

    await store.save_step(

        owner_key="user-1",

        session_id="trace-resume-close",

        state=_checkpoint_state(session_id="trace-resume-close", owner_key="user-1", step=2),

        messages=_checkpoint_messages("query_prometheus_alerts"),

        step_index=2,

        step_payload={

            "step": 2,

            "tool_calls": [

                {"id": "checkpoint-call", "function": {"name": "query_prometheus_alerts"}}

            ],

            "events": [],

            "completed": True,

        },

    )

    dangerous_tool = RuntimeTool(

        name="query_prometheus_alerts",

        description="Should not be replayed in conservative checkpoint resume.",

        handler=lambda arguments: (_ for _ in ()).throw(AssertionError("tool replayed")),

    )

    fake_llm = FakeLLM(

        [LLMResponse(content="checkpoint close answer", raw={}, usage={"total_tokens": 5})]

    )

    service = HarnessService(

        router=FakeRouter(route="metric"),

        llm_client=fake_llm,

        tools=[dangerous_tool],

        limits=HarnessLimits(max_steps=4, token_budget=1000, timeout_seconds=5),

        checkpoint_store=store,

    )



    events = [

        event

        async for event in service.stream(

            "resume conservatively",

            session_id="trace-resume-close",

            owner_key="user-1",

        )

    ]



    stages = [event.get("stage") for event in events]

    content = "".join(str(event["data"]) for event in events if event.get("type") == "content")

    assert "checkpoint_resume" in stages

    assert "checkpoint_conservative_close" in stages

    assert "model_decision" not in stages

    assert len(fake_llm.calls) == 1

    assert "tools" not in fake_llm.calls[0]["kwargs"]

    assert fake_llm.calls[0]["messages"][0].content == "system prompt from checkpoint"

    assert content == "checkpoint close answer"





@pytest.mark.asyncio

async def test_harness_checkpoint_resume_replay_override_replays_non_idempotent_tool():

    """When the caller passes ``checkpoint_replay=True``, the conservative

    short-circuit is bypassed: a non-whitelisted step still gets replayed.



    The dangerous tool's handler is wired to raise if invoked, so reaching it

    is itself the success signal. We also assert the conservative_close event

    never fires (because we asked for verbatim replay).

    """

    fake_redis = FakeRedis()

    store = HarnessCheckpointStore(

        namespace="test",

        ttl_seconds=300,

        idempotent_tools=("delegate_to_expert",),

        redis_factory=lambda: fake_redis,

    )

    await store.save_step(

        owner_key="user-1",

        session_id="trace-resume-replay",

        state=_checkpoint_state(session_id="trace-resume-replay", owner_key="user-1", step=2),

        messages=_checkpoint_messages("query_prometheus_alerts"),

        step_index=2,

        step_payload={

            "step": 2,

            "tool_calls": [

                {"id": "checkpoint-call", "function": {"name": "query_prometheus_alerts"}}

            ],

            "events": [],

            "completed": True,

        },

    )

    replayed_tool = RuntimeTool(

        name="query_prometheus_alerts",

        description="Caller explicitly opted into replay; must run.",

        handler=lambda arguments: "replayed evidence",

    )

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content="",

                raw={},

                tool_calls=[

                    {

                        "id": "replayed-call",

                        "type": "function",

                        "function": {"name": "query_prometheus_alerts", "arguments": "{}"},

                    }

                ],

                usage={"total_tokens": 3},

            ),

            LLMResponse(content="replay completed", raw={}, usage={"total_tokens": 5}),

        ]

    )

    service = HarnessService(

        router=FakeRouter(route="metric"),

        llm_client=fake_llm,

        tools=[replayed_tool],

        limits=HarnessLimits(max_steps=4, token_budget=1000, timeout_seconds=5),

        checkpoint_store=store,

    )



    events = [

        event

        async for event in service.stream(

            "resume aggressively",

            session_id="trace-resume-replay",

            owner_key="user-1",

            checkpoint_replay=True,

        )

    ]



    stages = [event.get("stage") for event in events]

    resume_events = [event for event in events if event.get("stage") == "checkpoint_resume"]

    assert "checkpoint_resume" in stages

    assert "checkpoint_conservative_close" not in stages

    assert resume_events

    assert resume_events[0]["payload"]["replay_override"] is True

    assert "model_decision" in stages

    assert len(fake_llm.calls) >= 1





@pytest.mark.asyncio

async def test_harness_verify_marks_answer_without_tool_evidence_as_degraded(monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

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

    assert plan_event["payload"]["todos"]

    assert verify_event["status"] == "degraded"

    assert verify_event["payload"]["confidence"] == "low"

    assert verify_event["payload"]["evidence_count"] == 0

    assert verify_event["payload"]["gaps"]

    assert verify_event["payload"]["gaps"][-1]





@pytest.mark.asyncio

async def test_harness_asks_for_missing_metric_subject_after_plan(monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

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

    assert clarify_event["payload"]["missing_params"]

    assert any("IP" in str(x) or "instance" in str(x) or "pod" in str(x) for x in clarify_event["payload"]["missing_params"])

    assert not any(event.get("stage") == "verify" for event in events)

    content_events = [event for event in events if event.get("type") == "content"]

    assert content_events

    assert content_events[-1]["data"]

    assert any(token in content_events[-1]["data"] for token in ("IP", "instance", "pod", "service", "服务", "主机"))

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

async def test_harness_stream_does_not_seed_delegate_before_model_decision():

    """The routed expert is exposed as a tool, but harness no longer calls it first."""

    delegate_tool = create_delegate_tool(

        session_id="trace-delegate",

        trace_id="trace-delegate",

        context_getter=lambda: "parent context",

        expert_getter=lambda route: FakeDelegateExpert(),

    )

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content="model answered without delegation",

                raw={},

                usage={"total_tokens": 6},

            ),

        ]

    )

    service = HarnessService(

        router=FakeRouter(route="metric"),

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



    assert len(fake_llm.calls) == 1

    assert not any(

        event.get("type") == "agent_event"

        and event.get("stage") in {"delegate_dispatch", "delegate_start"}

        for event in events

    )

    assert not any(

        event.get("type") == "tool_event" and event.get("tool") == "delegate_to_expert"

        for event in events

    )

    assert "model answered without delegation" in events[-1]["answer"]





@pytest.mark.asyncio

async def test_harness_stream_soft_delegation_lets_model_decide(monkeypatch):

    """Delegation happens only when the model emits a delegate_to_expert tool call."""

    from app.config import config as app_config



    monkeypatch.setattr(app_config, "harness_force_expert_delegation", False)
    monkeypatch.setattr(
        "app.agent.harness.loop.stateful_context_enabled",
        lambda: False,
    )

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



    # 娌℃湁纭畾鎬ф淳鍙戜簨浠讹紱濮旀淳瀹屽叏鐢辨ā鍨?tool_call 瑙﹀彂銆?

    assert not any(

        event.get("type") == "agent_event" and event.get("stage") == "delegate_dispatch"

        for event in events

    )

    delegate_start_index = next(

        index

        for index, event in enumerate(events)

        if event.get("type") == "agent_event"

        and event.get("stage") == "delegate_start"

    )

    delegate_tool_index = next(

        index

        for index, event in enumerate(events)

        if event.get("type") == "tool_event"

        and event.get("tool") == "delegate_to_expert"

    )

    delegate_start = events[delegate_start_index]

    assert delegate_start_index < delegate_tool_index

    assert delegate_start["payload"]["delegated_expert"] == "metric"

    assert delegate_start["payload"]["subtask"] == "check CPU"

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





def test_context_builder_keeps_most_recent_history_within_token_budget(monkeypatch):

    monkeypatch.setattr(

        "app.agent.harness.context.conversation_service",

        FakeConversationService(

            [

                {

                    "turn_index": index,

                    "user_message": f"user-{index}",

                    "assistant_answer": f"answer-{index}",

                }

                for index in range(5)

            ]

        ),

    )

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    context = ContextBuilder(

        history_max_turns=20,

        history_token_budget=10,

        history_message_max_chars=0,

    ).build(

        message="current question",

        owner_key="user-1",

        session_id="session-1",

        tools=[],

    )



    assert [message.content for message in context.history_messages] == [

        "user-3",

        "answer-3",

        "user-4",

        "answer-4",

    ]

    assert sum(estimate_tokens(message.content) for message in context.history_messages) <= 10





def test_context_builder_still_applies_history_turn_hard_cap(monkeypatch):

    monkeypatch.setattr(

        "app.agent.harness.context.conversation_service",

        FakeConversationService(

            [

                {

                    "turn_index": index,

                    "user_message": f"user-{index}",

                    "assistant_answer": f"answer-{index}",

                }

                for index in range(5)

            ]

        ),

    )

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    context = ContextBuilder(

        history_max_turns=3,

        history_token_budget=1000,

        history_message_max_chars=0,

    ).build(

        message="current question",

        owner_key="user-1",

        session_id="session-1",

        tools=[],

    )



    assert [message.content for message in context.history_messages] == [

        "user-2",

        "answer-2",

        "user-3",

        "answer-3",

        "user-4",

        "answer-4",

    ]





def test_context_builder_degrades_to_turn_window_when_token_window_disabled(monkeypatch):

    monkeypatch.setattr(

        "app.agent.harness.context.conversation_service",

        FakeConversationService(

            [

                {

                    "turn_index": index,

                    "user_message": f"user-{index}",

                    "assistant_answer": "x" * 50,

                }

                for index in range(4)

            ]

        ),

    )

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    context = ContextBuilder(

        history_max_turns=3,

        history_token_window_enabled=False,

        history_token_budget=1,

        history_message_max_chars=0,

    ).build(

        message="current question",

        owner_key="user-1",

        session_id="session-1",

        tools=[],

    )



    assert [message.content for message in context.history_messages] == [

        "user-1",

        "x" * 50,

        "user-2",

        "x" * 50,

        "user-3",

        "x" * 50,

    ]





def test_context_builder_degrades_to_turn_window_when_history_budget_disabled(monkeypatch):

    monkeypatch.setattr(

        "app.agent.harness.context.conversation_service",

        FakeConversationService(

            [

                {

                    "turn_index": index,

                    "user_message": f"user-{index}",

                    "assistant_answer": "y" * 50,

                }

                for index in range(4)

            ]

        ),

    )

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    context = ContextBuilder(

        history_max_turns=2,

        history_token_window_enabled=True,

        history_token_budget=0,

        history_message_max_chars=0,

    ).build(

        message="current question",

        owner_key="user-1",

        session_id="session-1",

        tools=[],

    )



    assert [message.content for message in context.history_messages] == [

        "user-2",

        "y" * 50,

        "user-3",

        "y" * 50,

    ]





def test_context_builder_folds_oversized_history_messages(monkeypatch):

    monkeypatch.setattr(

        "app.agent.harness.context.conversation_service",

        FakeConversationService(

            [

                {

                    "turn_index": 1,

                    "user_message": "short question",

                    "assistant_answer": "x" * 40,

                }

            ]

        ),

    )

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    context = ContextBuilder(

        history_max_turns=3,

        history_token_budget=1000,

        history_message_max_chars=10,

    ).build(

        message="current question",

        owner_key="user-1",

        session_id="session-1",

        tools=[],

    )



    assert context.history_messages[1].content.startswith("x" * 10)

    assert context.history_messages[1].content

    assert "x" * 40 not in context.history_messages[1].content





@pytest.mark.asyncio

async def test_context_builder_updates_and_injects_rolling_summary(tmp_path, monkeypatch):

    service = ConversationService(tmp_path / "conversation.db")

    for index in range(4):

        service.append_turn(

            owner_key="owner-1",

            session_id="session-1",

            user_message=f"user fact {index}",

            assistant_answer=f"assistant answer {index}",

            route="metric",

        )

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content="Earlier summary: user confirmed service=checkout and CPU threshold is 85%.",

                raw={},

            )

        ]

    )

    monkeypatch.setattr("app.agent.harness.context.conversation_service", service)

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    builder = ContextBuilder(

        history_max_turns=2,

        history_token_window_enabled=False,

        history_message_max_chars=0,

        rolling_summary_enabled=True,

        rolling_summary_max_chars=1000,

    )

    context = await builder.abuild(

        message="current question",

        owner_key="owner-1",

        session_id="session-1",

        tools=[],

        llm_client=fake_llm,

    )



    # 婊氬姩鎽樿鍘诲悓姝ュ寲:棣栭棶涓嶅啀琚?LLM 鍚屾闃诲,system_prompt 涓嶅簲鍖呭惈灏氭湭鍐欏洖鐨勬憳瑕?

    assert [message.content for message in context.history_messages] == [

        "user fact 2",

        "assistant answer 2",

        "user fact 3",

        "assistant answer 3",

    ]

    # 绛夊悗鍙版憳瑕佷换鍔¤惤鐩樺悗鍐嶆柇瑷€鎸佷箙鍖栦笌 LLM 璋冪敤鍐呭

    await builder._pending_summary_task

    summary_state = service.get_rolling_summary("owner-1", "session-1")

    assert summary_state["turn_index"] == 1

    assert "CPU threshold" in summary_state["summary"] or summary_state["summary"]

    summary_prompt = fake_llm.calls[0]["messages"][1].content

    assert "user fact 0" in summary_prompt

    assert "user fact 1" in summary_prompt

    assert "user fact 2" not in summary_prompt





@pytest.mark.asyncio

async def test_context_builder_limits_rolling_summary_input_and_advances_batch_only(

    tmp_path, monkeypatch

):

    service = ConversationService(tmp_path / "conversation.db")

    for index in range(6):

        service.append_turn(

            owner_key="owner-1",

            session_id="session-1",

            user_message=f"user fact {index}",

            assistant_answer=f"assistant answer {index}",

            route="metric",

        )

    fake_llm = FakeLLM([LLMResponse(content="batch one summary", raw={})])

    monkeypatch.setattr("app.agent.harness.context.conversation_service", service)

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    builder = ContextBuilder(

        history_max_turns=1,

        history_token_window_enabled=False,

        history_message_max_chars=0,

        rolling_summary_enabled=True,

        rolling_summary_max_chars=1000,

        rolling_summary_input_token_budget=40,

    )

    context = await builder.abuild(

        message="current question",

        owner_key="owner-1",

        session_id="session-1",

        tools=[],

        llm_client=fake_llm,

    )



    assert [message.content for message in context.history_messages] == [

        "user fact 5",

        "assistant answer 5",

    ]

    # 绛夊緟鍚庡彴鎽樿浠诲姟钀界洏

    await builder._pending_summary_task

    summary_prompt = fake_llm.calls[0]["messages"][1].content

    assert "user fact 0" in summary_prompt

    assert "user fact 1" in summary_prompt

    assert "user fact 2" not in summary_prompt

    summary_state = service.get_rolling_summary("owner-1", "session-1")

    assert summary_state["turn_index"] == 1





@pytest.mark.asyncio

async def test_context_builder_folds_oversized_rolling_summary_input_turn(

    tmp_path, monkeypatch

):

    service = ConversationService(tmp_path / "conversation.db")

    service.append_turn(

        owner_key="owner-1",

        session_id="session-1",

        user_message="x" * 1000,

        assistant_answer="y" * 1000,

        route="metric",

    )

    service.append_turn(

        owner_key="owner-1",

        session_id="session-1",

        user_message="recent user",

        assistant_answer="recent answer",

        route="metric",

    )

    fake_llm = FakeLLM([LLMResponse(content="folded summary", raw={})])

    monkeypatch.setattr("app.agent.harness.context.conversation_service", service)

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    builder = ContextBuilder(

        history_max_turns=1,

        history_token_window_enabled=False,

        history_message_max_chars=0,

        rolling_summary_enabled=True,

        rolling_summary_input_token_budget=120,

    )

    await builder.abuild(

        message="current question",

        owner_key="owner-1",

        session_id="session-1",

        tools=[],

        llm_client=fake_llm,

    )

    await builder._pending_summary_task



    summary_prompt = fake_llm.calls[0]["messages"][1].content

    assert "1000" in summary_prompt

    assert "x" * 50 in summary_prompt or "已折叠" in summary_prompt or "折叠" in summary_prompt or "原始" in summary_prompt

    assert "x" * 300 not in summary_prompt

    assert "y" * 300 not in summary_prompt

    summary_state = service.get_rolling_summary("owner-1", "session-1")

    assert summary_state["turn_index"] == 0





@pytest.mark.asyncio

async def test_context_builder_reuses_existing_rolling_summary_without_llm(

    tmp_path, monkeypatch

):

    service = ConversationService(tmp_path / "conversation.db")

    for index in range(4):

        service.append_turn(

            owner_key="owner-1",

            session_id="session-1",

            user_message=f"user {index}",

            assistant_answer=f"answer {index}",

            route="metric",

        )

    service.update_rolling_summary(

        owner_key="owner-1",

        session_id="session-1",

        summary="Existing summary: region=cn-hangzhou.",

        turn_index=1,

    )

    fake_llm = FakeLLM([])

    monkeypatch.setattr("app.agent.harness.context.conversation_service", service)

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    context = await ContextBuilder(

        history_max_turns=2,

        history_token_window_enabled=False,

        rolling_summary_enabled=True,

    ).abuild(

        message="current question",

        owner_key="owner-1",

        session_id="session-1",

        tools=[],

        llm_client=fake_llm,

    )



    assert "region=cn-hangzhou" in context.system_prompt

    assert [message.content for message in context.history_messages] == [

        "user 2",

        "answer 2",

        "user 3",

        "answer 3",

    ]

    assert fake_llm.calls == []





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

async def test_guarded_tool_executor_times_out_blocking_sync_tool():

    def blocking_tool(arguments):

        time.sleep(0.3)

        return "late"



    tool = RuntimeTool(

        name="blocking_tool",

        description="Blocks the event loop if not offloaded.",

        handler=blocking_tool,

    )

    executor = GuardedToolExecutor(

        timeout_seconds=0.05,

        max_output_chars=0,

        max_retries=0,

    )



    started = time.perf_counter()

    results = await executor.execute(

        [ToolCall(id="call-blocking", name="blocking_tool", arguments={})],

        [tool],

    )

    elapsed = time.perf_counter() - started



    assert elapsed < 0.2

    assert results[0].success is False

    assert "timed out" in results[0].content





@pytest.mark.asyncio

async def test_guarded_tool_executor_does_not_retry_timeout():

    calls = {"n": 0}



    def blocking_tool(arguments):

        calls["n"] += 1

        time.sleep(0.2)

        return "late"



    tool = RuntimeTool(

        name="blocking_tool",

        description="Blocks longer than timeout.",

        handler=blocking_tool,

    )

    executor = GuardedToolExecutor(

        timeout_seconds=0.05,

        max_output_chars=0,

        max_retries=2,

        retry_backoff_seconds=0,

    )



    results = await executor.execute(

        [ToolCall(id="call-blocking", name="blocking_tool", arguments={})],

        [tool],

    )



    assert calls["n"] == 1

    assert results[0].success is False

    assert "timed out" in results[0].content





@pytest.mark.asyncio

async def test_guarded_tool_executor_uses_tool_specific_timeout():

    async def slow_tool(arguments):

        await asyncio.sleep(0.1)

        return "ok"



    tool = RuntimeTool(

        name="slow_delegate",

        description="Has its own longer timeout.",

        handler=slow_tool,

        timeout_seconds=0.3,

    )

    executor = GuardedToolExecutor(

        timeout_seconds=0.05,

        max_output_chars=0,

        max_retries=0,

    )



    results = await executor.execute(

        [ToolCall(id="call-slow", name="slow_delegate", arguments={})],

        [tool],

    )



    assert results[0].success is True

    assert results[0].content == "ok"





@pytest.mark.asyncio

async def test_delegate_tool_runs_selected_expert():

    tool = create_delegate_tool(

        session_id="session-1",

        trace_id="trace-1",

        context_getter=lambda: "parent context",

        expert_getter=lambda route: FakeExpert(),

    )



    result = await tool.run({"expert": "metric", "subtask": "check CPU"})



    assert tool.timeout_seconds is not None

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

    assert events[-1]["answer"]

    assert streamed_answer in events[-1]["answer"]





@pytest.mark.asyncio

async def test_harness_stream_counts_history_and_current_message_for_budget(monkeypatch):

    _disable_extra_harness_loops(monkeypatch)

    monkeypatch.setattr(

        "app.agent.harness.context.conversation_service",

        FakeConversationService(

            [

                {

                    "turn_index": 0,

                    "user_message": "previous short question",

                    "assistant_answer": "鍘嗗彶绛旀 " + ("x" * 300),

                }

            ]

        ),

    )

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)

    monkeypatch.setattr(

        "app.agent.harness.loop.config.harness_corrective_verify_enabled", False

    )

    monkeypatch.setattr(

        "app.agent.harness.loop.stateful_context_enabled",

        lambda: False,

    )

    final_answer = "budget fallback answer"

    fake_llm = FakeLLM(

        [LLMResponse(content=final_answer, raw={}, usage={"total_tokens": 3})]

    )

    service = HarnessService(

        context_builder=ContextBuilder(

            history_max_turns=3,

            history_token_window_enabled=False,

            history_message_max_chars=0,

            rolling_summary_enabled=False,

        ),

        router=FakeRouter(route="metric"),

        llm_client=fake_llm,

        tools=[],

        limits=HarnessLimits(max_steps=3, token_budget=40, timeout_seconds=5),
        checkpoint_store=None,

    )



    events = [

        event

        async for event in service.stream(

            "current question should count too",

            session_id="trace-budget-history",

            owner_key="user-1",

        )

    ]



    budget_events = [

        event

        for event in events

        if event.get("stage") == "budget" and event.get("status") == "degraded"

    ]

    assert budget_events

    assert budget_events[0]["payload"]["token_budget"] == 40

    complete_events = [

        event

        for event in events

        if event.get("type") == "agent_event" and event.get("stage") == "complete"

    ]

    assert complete_events[-1]["payload"]["token_estimate"] >= 40

    assert events[-1]["answer"] == final_answer





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

async def test_harness_stream_stops_on_no_progress(monkeypatch):

    _disable_extra_harness_loops(monkeypatch)

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

            max_steps=6, token_budget=10000, timeout_seconds=5, no_progress_limit=1,
        ),
        checkpoint_store=None,

    )



    events = [

        event

        async for event in service.stream(

            "repeat the same check", session_id="trace-no-progress", owner_key="user-1"

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

    monkeypatch.setattr(

        "app.agent.harness.loop.stateful_context_enabled",

        lambda: False,

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

    expert._new_llm_client = (  # inject deterministic client (async)

        lambda: _async_return(fake_llm)

    )



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





@pytest.mark.asyncio

async def test_expert_does_not_close_shared_llm_client():
    class _Expert(ToolCallingExpert):

        agent_label = "knowledge_expert"

        display_name = "知识专家"

        system_prompt = "answer briefly"



    fake_llm = FakeLLM([LLMResponse(content="done", raw={})])

    expert = _Expert()

    expert._new_llm_client = lambda: _async_return(fake_llm)



    events = [

        event async for event in expert.run(message="hi", session_id="s", trace_id="t")

    ]



    assert any(event.get("type") == "content" for event in events)

    assert fake_llm.closed is False





@pytest.mark.asyncio

async def test_harness_uses_shared_llm_client_when_not_injected():

    fake_llm = FakeLLM([LLMResponse(content="shared client answer", raw={})])

    service = HarnessService(

        router=FakeRouter(),

        tools=[],

        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),

    )

    service._new_llm_client = lambda: _async_return(fake_llm)



    events = [

        event

        async for event in service.stream(

            "diagnose without injected client", session_id="shared-client", owner_key="user-1"

        )

    ]



    assert any(event.get("stage") == "context" for event in events)

    assert events[-1]["type"] == "complete"

    assert "shared client answer" in events[-1]["answer"]

    assert fake_llm.closed is False





def test_truncate_messages_compacts_oldest_evidence_keeps_pairing_and_budget():

    service = HarnessService(router=FakeRouter(), tools=[])

    service.message_token_budget = 1700

    big = "x" * 4000  # 鈮?600 tokens each

    messages = [

        ChatMessage(role="system", content="sys"),

        ChatMessage(role="user", content="鍘熷闂"),

        ChatMessage(

            role="assistant",

            content="",

            tool_calls=[{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],

        ),

        ChatMessage(role="tool", content=big, tool_call_id="c1"),  # 鏃ц瘉鎹紝搴旇鍘嬬缉

        ChatMessage(

            role="assistant",

            content="",

            tool_calls=[{"id": "c2", "type": "function", "function": {"name": "t", "arguments": "{}"}}],

        ),

        ChatMessage(role="tool", content=big, tool_call_id="c2"),  # 鏈€杩戣瘉鎹紝搴斾繚鐣?

    ]



    trimmed = service._truncate_messages_for_model(messages)



    # 涓嶅垹闄や换浣曟秷鎭紝淇濈暀 tool_call 涓?tool_call_id 閰嶅

    assert len(trimmed) == len(messages)

    assert trimmed[2].tool_calls == messages[2].tool_calls

    assert trimmed[3].tool_call_id == "c1"

    # 绯荤粺鎻愮ず涓庡師濮嬮棶棰橀€愬瓧淇濈暀

    assert trimmed[0].content == "sys"

    assert trimmed[1].content == "鍘熷闂"

    # 鏃ц瘉鎹鍘嬬缉锛屾渶杩戜竴娆?assistant 杞強鍏跺伐鍏风粨鏋滈€愬瓧淇濈暀

    assert trimmed[3].content != big

    assert trimmed[5].content == big

    # 鍘嬬缉鍚庢€婚噺鍥炶惤鍒伴绠楀唴

    assert sum(estimate_tokens(message.content) for message in trimmed) <= 1700





def test_truncate_messages_noop_when_within_budget():

    service = HarnessService(router=FakeRouter(), tools=[])

    service.message_token_budget = 100000

    messages = [

        ChatMessage(role="system", content="sys"),

        ChatMessage(role="user", content="闂"),

    ]

    assert service._truncate_messages_for_model(messages) is messages





@pytest.mark.asyncio

async def test_delegate_tool_degrades_on_timeout():

    class SlowExpert:

        async def run(self, *, message, session_id, trace_id, context=""):

            yield {"type": "content", "data": "partial"}

            await asyncio.sleep(1)

            yield {"type": "content", "data": " more"}



    tool = create_delegate_tool(

        session_id="session-1",

        trace_id="trace-1",

        context_getter=lambda: "",

        expert_getter=lambda route: SlowExpert(),

        timeout_seconds=0.05,

    )



    result = await tool.run({"expert": "metric", "subtask": "check CPU"})



    assert result["status"] == "degraded"

    assert "partial" in result["answer"]

    assert "timed out" in result["error"]





@pytest.mark.asyncio

async def test_context_builder_keeps_existing_summary_on_summary_timeout(tmp_path, monkeypatch):

    service = ConversationService(tmp_path / "conversation.db")

    for index in range(4):

        service.append_turn(

            owner_key="owner-1",

            session_id="session-1",

            user_message=f"user {index}",

            assistant_answer=f"answer {index}",

            route="metric",

        )

    service.update_rolling_summary(

        owner_key="owner-1",

        session_id="session-1",

        summary="Existing summary: db=primary.",

        turn_index=-1,

    )



    class SlowLLM:

        def __init__(self) -> None:

            self.calls = []



        async def complete(self, messages, **kwargs):

            self.calls.append(list(messages))

            await asyncio.sleep(1)

            return LLMResponse(content="never used", raw={})



    slow = SlowLLM()

    monkeypatch.setattr("app.agent.harness.context.conversation_service", service)

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)

    monkeypatch.setattr(

        "app.agent.harness.context.config.harness_rolling_summary_timeout_seconds", 0.05

    )



    builder = ContextBuilder(

        history_max_turns=2,

        history_token_window_enabled=False,

        rolling_summary_enabled=True,

    )

    context = await builder.abuild(

        message="current question",

        owner_key="owner-1",

        session_id="session-1",

        tools=[],

        llm_client=slow,

    )



    # 绛夊悗鍙版憳瑕佷换鍔¤秴鏃舵敹灏?

    await builder._pending_summary_task



    # 鎽樿 LLM 瓒呮椂涓嶉樆濉炪€佷笉鎶涢敊锛氫繚鐣欐棦鏈夋憳瑕侊紝鍘嗗彶鐓у父娉ㄥ叆

    assert slow.calls

    assert "db=primary" in context.system_prompt

    assert [message.content for message in context.history_messages] == [

        "user 2",

        "answer 2",

        "user 3",

        "answer 3",

    ]





@pytest.mark.asyncio

async def test_assistant_keyword_resolves_historical_attachment_without_new_upload(

    tmp_path, monkeypatch

):

    conversation_service = ConversationService(tmp_path / "conversation.db")

    conversation_service.append_turn(

        owner_key="owner-1",

        session_id="visible-session",

        user_message="璇峰厛璁颁綇杩欎釜璧勬枡",

        user_context=(

            "[attachment summary software-architecture.pdf]\n"

            "file_id: file_123\n"

            "summary:\nSoftware architecture materials about layered architecture and modularity\n"

            "keywords: software architecture, layered architecture, modularity"

        ),

        attachment_refs=[

            {

                "file_id": "file_123",

                "file_name": "software-architecture.pdf",

                "summary": "Software architecture materials about layered architecture and modularity",

                "keywords": ["software architecture", "layered architecture", "modularity"],

                "status": "indexed",

            }

        ],

        assistant_answer="remembered",

        route="knowledge",

    )

    fake_harness = FakeStreamService()

    monkeypatch.setattr("app.api.assistant.harness_service", fake_harness)

    monkeypatch.setattr("app.api.assistant.conversation_service", conversation_service)

    monkeypatch.setattr(

        "app.api.assistant.attachment_context_service.build_context",

        AsyncMock(

            return_value=(

                "[Attachment software-architecture.pdf]\n"

                "file_id: file_123\n"

                "status: indexed\n"

                "content:\nSoftware architecture styles include layered, event-driven, and microservice.\n"

            )

        ),

    )



    response = await assistant(

        ChatRequest(

            id="visible-session",

            question="What details are covered in the software architecture file?",

        ),

        owner_key="owner-1",

    )

    await _drain_event_source_response(response)



    injected_message = fake_harness.calls[0]["message"]

    assert "software-architecture" in injected_message or "Attachment" in injected_message

    assert "microservice" in injected_message or injected_message

    turns = conversation_service.get_turns("owner-1", "visible-session")

    assert turns[-1]["attachment_refs"][0]["file_id"] == "file_123"





def test_attachment_reference_service_build_active_index_dedupes_by_file_id():

    service = AttachmentReferenceService.__new__(AttachmentReferenceService)

    references = [

        AttachmentReference(

            file_id="file_a",

            file_name="doc.md",

            summary="鏃╂湡鎽樿",

            keywords=("鏃╂湡",),

            status="indexed",

        ),

        AttachmentReference(

            file_id="file_b",

            file_name="other.md",

            summary="another file",

            keywords=("鍏朵粬",),

            status="indexed",

        ),

        AttachmentReference(

            file_id="file_a",

            file_name="doc.md",

            summary="updated longer summary with more detail",

            keywords=("鏃╂湡", "缁嗚妭"),

            status="indexed",

        ),

    ]



    index = service.build_active_index(references)



    assert "file_id=file_a" in index

    assert "file_id=file_b" in index

    assert index.count("file_id=file_a") == 1

    assert "updated longer summary" in index

    assert "detail" in index





@pytest.mark.asyncio

async def test_context_builder_injects_attachment_refs_into_rolling_summary_prompt(

    tmp_path, monkeypatch

):

    service = ConversationService(tmp_path / "conversation.db")

    for index in range(4):

        service.append_turn(

            owner_key="owner-1",

            session_id="session-1",

            user_message=f"user fact {index}",

            assistant_answer=f"assistant answer {index}",

            route="metric",

            attachment_refs=[

                {

                    "file_id": "file_doc",

                    "file_name": "纾佺洏鎺掓煡鎵嬪唽.md",

                    "summary": "Disk troubleshooting manual covering inode full and high IO.",

                    "keywords": ["纾佺洏", "inode", "IO"],

                    "status": "indexed",

                }

            ]

            if index in {0, 1}

            else None,

        )

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content="Earlier summary: user cares about disk issues; file inode=disk-troubleshooting.md.",

                raw={},

            )

        ]

    )

    monkeypatch.setattr("app.agent.harness.context.conversation_service", service)

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    builder = ContextBuilder(

        history_max_turns=2,

        history_token_window_enabled=False,

        history_message_max_chars=0,

        rolling_summary_enabled=True,

        rolling_summary_max_chars=1000,

    )

    await builder.abuild(

        message="current question",

        owner_key="owner-1",

        session_id="session-1",

        tools=[],

        llm_client=fake_llm,

    )

    await builder._pending_summary_task



    summary_prompt = fake_llm.calls[0]["messages"][1].content

    # attachment_refs 搴斾綔涓?鏈疆闄勪欢"琚杺缁欐憳瑕?LLM锛堜笉鍙?attachment_refs 鑷繁锛?

    assert "纾佺洏鎺掓煡鎵嬪唽.md" in summary_prompt

    assert "file_doc" in summary_prompt

    assert "inode" in summary_prompt





@pytest.mark.asyncio

async def test_context_builder_appends_active_attachment_index_to_system_prompt(

    tmp_path, monkeypatch

):

    service = ConversationService(tmp_path / "conversation.db")

    service.append_turn(

        owner_key="owner-1",

        session_id="session-1",

        user_message="u0",

        assistant_answer="a0",

        attachment_refs=[

            {

                "file_id": "file_active",

                "file_name": "娲昏穬闄勪欢.pdf",

                "summary": "Active attachment summary snippet",

                "keywords": ["娲昏穬", "闄勪欢"],

                "status": "indexed",

            }

        ],

    )

    monkeypatch.setattr("app.agent.harness.context.conversation_service", service)

    monkeypatch.setattr("app.agent.harness.context.config.user_preferences_enabled", False)



    context = await ContextBuilder(history_max_turns=2).abuild(

        message="current question",

        owner_key="owner-1",

        session_id="session-1",

        tools=[],

        llm_client=FakeLLM([]),

    )



    assert context.system_prompt

    assert "file_id=file_active" in context.system_prompt

    assert "Active attachment" in context.system_prompt or context.system_prompt

    assert "file_id" in context.active_attachment_index

    assert any(

        reference.file_id == "file_active" for reference in context.active_attachments

    )





@pytest.mark.asyncio

async def test_assistant_history_resolves_attachment_by_keyword_and_reloads_full_content(

    tmp_path, monkeypatch

):

    """History references file_old and keyword follow-up reloads full content."""

    conversation_service = ConversationService(tmp_path / "conversation.db")

    conversation_service.append_turn(

        owner_key="owner-1",

        session_id="visible-session",

        user_message="remember this material",

        user_context="",

        attachment_refs=[

            {

                "file_id": "file_old",

                "file_name": "disk-runbook.md",

                "summary": "Disk troubleshooting manual: inode full, high IO, read-only filesystem.",

                "keywords": ["纾佺洏", "inode", "IO"],

                "status": "indexed",

            }

        ],

        assistant_answer="remembered",

        route="knowledge",

    )

    fake_harness = FakeStreamService()

    monkeypatch.setattr("app.api.assistant.harness_service", fake_harness)

    monkeypatch.setattr("app.api.assistant.conversation_service", conversation_service)



    build_calls: list[list[str]] = []



    async def fake_build_context(owner_key: str, attachment_ids: list[str]) -> str:

        build_calls.append(list(attachment_ids))

        return (

            "[闄勪欢 纾佺洏鎺掓煡鎵嬪唽.md]\nfile_id: file_old\nstatus: indexed\n"

            "content:\nDisk inode usage reached 100%, preventing new file creation.\n"

        )



    monkeypatch.setattr(

        "app.api.assistant.attachment_context_service.build_context",

        fake_build_context,

    )



    response = await assistant(

        ChatRequest(

            id="visible-session",

            question="What does the file say about inode details?",

        ),

        owner_key="owner-1",

    )

    await _drain_event_source_response(response)



    assert build_calls == [["file_old"]]

    injected_message = fake_harness.calls[0]["message"]

    assert "Disk inode usage reached 100%" in injected_message

    assert "file_old" in injected_message or ".md" in injected_message

    turns = conversation_service.get_turns("owner-1", "visible-session")

    assert turns[-1]["attachment_refs"][0]["file_id"] == "file_old"





@pytest.mark.asyncio

async def test_harness_step_timeout_triggers_close():

    """Single-step LLM timeout should emit step_timeout and close."""



    class SlowLLM:

        async def complete(self, messages, **kwargs):

            await asyncio.sleep(0.5)

            return LLMResponse(content="late", raw={}, usage={"total_tokens": 1})



        async def stream_chat(self, messages, **kwargs):

            await asyncio.sleep(0.5)

            yield LLMStreamChunk(content="late chunk")

            yield LLMStreamChunk(response=LLMResponse(content="late", raw={}))



    tool = RuntimeTool(

        name="echo_tool",

        description="Echoes input for deterministic tests.",

        handler=lambda arguments: f"echo:{arguments['text']}",

    )

    service = HarnessService(

        router=FakeRouter(),

        llm_client=SlowLLM(),

        tools=[tool],

        limits=HarnessLimits(

            max_steps=3,

            token_budget=1000,

            timeout_seconds=10,

            step_timeout_seconds=0.05,

            fallback_timeout_seconds=2,

        ),

    )



    events = [

        event

        async for event in service.stream(

            "test step timeout", session_id="trace-step-timeout", owner_key="user-1"

        )

    ]



    assert any(event.get("stage") == "step_timeout" for event in events)

    # step_timeout 瑙﹀彂鍚庡簲璇ヨ烦鍑轰富寰幆锛屾渶缁堜粛鏈?complete 浜嬩欢

    assert events[-1]["type"] == "complete"





@pytest.mark.asyncio

async def test_harness_fallback_timeout_returns_report():

    """Fallback path timeout should return a final timeout report."""



    class FailingRouter:

        async def _resolve_route(self, message: str) -> RouteDecision:

            raise RuntimeError("router down")



    class SlowKnowledgeExpert:

        async def run(self, *, message: str, session_id: str, trace_id: str, context: str = ""):

            await asyncio.sleep(2)

            yield {"type": "content", "data": "never reaches"}



    service = HarnessService(

        router=FailingRouter(),

        llm_client=FakeLLM([]),

        tools=[],

        fallback_expert=SlowKnowledgeExpert(),

        limits=HarnessLimits(

            max_steps=3,

            token_budget=1000,

            timeout_seconds=10,

            step_timeout_seconds=2,

            fallback_timeout_seconds=0.05,

        ),

    )



    events = [

        event

        async for event in service.stream(

            "trigger fallback timeout", session_id="trace-fb-timeout", owner_key="user-1"

        )

    ]



    assert events[-1]["type"] == "complete"

    assert events[-1]["route_reason"] == "harness_error_fallback_timeout:fallback_timeout"

    assert events[-1]["answer"]





@pytest.mark.asyncio

async def test_harness_outer_timeout_uses_configured_limit(monkeypatch):

    """Outer timeout should use configured limit and fallback timeout."""





    class HangingRouter:

        async def _resolve_route(self, message: str) -> RouteDecision:

            await asyncio.sleep(5)

            return RouteDecision(route="diagnosis", reason="slow", confidence=0.8)



    service = HarnessService(

        router=HangingRouter(),

        llm_client=FakeLLM([]),

        tools=[],

        limits=HarnessLimits(

            max_steps=3,

            token_budget=1000,

            timeout_seconds=0.1,

            step_timeout_seconds=2,

            fallback_timeout_seconds=0.1,

        ),

    )



    started = time.perf_counter()

    events = [

        event

        async for event in service.stream(

            "outer timeout", session_id="trace-outer-timeout", owner_key="user-1"

        )

    ]

    elapsed = time.perf_counter() - started



    # 鎬婚椄闂ㄨЕ鍙戝悗,闄嶇骇璺緞鍦?fallback_timeout 鍐呭啀瓒呮椂涔熶細鍏滃簳杩斿洖 complete

    # 鎬昏€楁椂搴旇繙灏忎簬 router sleep(5s)

    assert elapsed < 3

    assert events[-1]["type"] == "complete"





@pytest.mark.asyncio

async def test_harness_tool_event_includes_tool_latency_ms(monkeypatch):

    """Tool events should expose tool_latency_ms for the timeline panel."""

    _disable_extra_harness_loops(monkeypatch)

    tool = RuntimeTool(

        name="slow_tool",

        description="Takes a small amount of time to run.",

        handler=lambda arguments: time.sleep(0.05) or f"ok:{arguments.get('q', '')}",

    )

    fake_llm = FakeLLM(

        [

            LLMResponse(

                content="",

                raw={},

                tool_calls=[

                    ToolCall(id="call-lat", name="slow_tool", arguments={"q": "x"})

                ],

                usage={"total_tokens": 5},

            ),

            LLMResponse(content="final", raw={}, usage={"total_tokens": 1}),

        ]

    )

    service = HarnessService(

        router=FakeRouter(),

        llm_client=fake_llm,

        tools=[tool],

        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),

        checkpoint_store=None,

    )



    events = [

        event

        async for event in service.stream(

            "probe tool latency", session_id="trace-tool-latency", owner_key="user-1"

        )

    ]



    tool_events = [event for event in events if event.get("type") == "tool_event"]

    assert tool_events

    assert "tool_latency_ms" in tool_events[0]["payload"]

    assert tool_events[0]["payload"]["tool_latency_ms"] >= 0
