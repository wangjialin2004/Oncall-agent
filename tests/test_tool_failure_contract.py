from __future__ import annotations

import json

import pytest
from mcp import types as mcp_types

from app.agent.agent_loop import GuardedToolExecutor, stream_tool_results
from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessState
from app.agent.public_events import to_public_timeline_event
from app.api.assistant import assistant
from app.api.conversations import get_conversation
from app.core.llm_client import LLMResponse, LLMStreamChunk, ToolCall
from app.core.request_context import RequestContext, bind_request_context
from app.core.runtime_tools import RuntimeTool
from app.core.tool_calling import ToolExecutionResult
from app.models.request import ChatRequest
from app.services.conversation_service import ConversationService
from app.services.context_repository import ContextRepository, ContextRepositorySettings
from app.services.router_service import RouteDecision
from app.services.session_scope_service import AuthenticatedPrincipal
from app.tools.knowledge_tool import retrieve_knowledge
from app.tools.time_tool import get_current_time
from tests._context_db import initialize_context_db


@pytest.mark.asyncio
async def test_mcp_error_result_is_failed_without_outer_retry() -> None:
    calls = 0

    async def handler(_arguments: dict) -> mcp_types.CallToolResult:
        nonlocal calls
        calls += 1
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="monitor source unavailable")],
            isError=True,
        )

    tool = RuntimeTool(name="query_cpu_metrics", description="query cpu", handler=handler)
    results = await GuardedToolExecutor(max_retries=2, retry_backoff_seconds=0).execute(
        [ToolCall(id="mcp-error", name=tool.name, arguments={})], [tool]
    )

    assert calls == 1
    assert results[0].success is False
    assert results[0].retryable is False
    assert "monitor source unavailable" in results[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "serialized_result",
    [
        {"isError": True, "content": [{"type": "text", "text": "serialized MCP failure"}]},
        '{"isError": true, "message": "serialized MCP failure"}',
    ],
)
async def test_serialized_mcp_error_result_is_failed(serialized_result) -> None:
    async def handler(_arguments: dict):
        return serialized_result

    tool = RuntimeTool(name="query_cpu_metrics", description="query cpu", handler=handler)
    results = await GuardedToolExecutor(max_retries=2, retry_backoff_seconds=0).execute(
        [ToolCall(id="serialized-mcp-error", name=tool.name, arguments={})], [tool]
    )

    assert results[0].success is False
    assert results[0].retryable is False


@pytest.mark.asyncio
async def test_permanent_structured_failure_is_attempted_once() -> None:
    calls = 0

    async def handler(_arguments: dict) -> dict:
        nonlocal calls
        calls += 1
        return {
            "success": False,
            "source_available": False,
            "capability": "change_query",
            "message": "change source is intentionally unavailable",
        }

    tool = RuntimeTool(name="query_recent_changes", description="query changes", handler=handler)
    results = await GuardedToolExecutor(max_retries=2, retry_backoff_seconds=0).execute(
        [ToolCall(id="permanent", name=tool.name, arguments={})], [tool]
    )

    assert calls == 1
    assert results[0].success is False
    assert results[0].retryable is False


@pytest.mark.asyncio
async def test_transient_structured_failure_retries_and_recovers() -> None:
    calls = 0

    async def handler(_arguments: dict) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "success": False,
                "status": "error",
                "error": "upstream 503",
                "retryable": True,
            }
        return {"success": True, "alerts": []}

    tool = RuntimeTool(name="query_prometheus_alerts", description="query alerts", handler=handler)
    results = await GuardedToolExecutor(max_retries=2, retry_backoff_seconds=0).execute(
        [ToolCall(id="transient", name=tool.name, arguments={})], [tool]
    )

    assert calls == 2
    assert results[0].success is True


@pytest.mark.asyncio
async def test_knowledge_exception_is_failed_without_exception_details(monkeypatch) -> None:
    def fail_search(*_args, **_kwargs):
        raise RuntimeError("private tenant scope failure")

    monkeypatch.setattr("app.tools.knowledge_tool.vector_search_service.search", fail_search)
    context = RequestContext(
        owner_key="owner-1",
        storage_owner_key="owner-1",
        project_id="default",
        role="operator",
        session_id="tool-failure-knowledge",
        trace_id="tool-failure-knowledge",
    )
    with bind_request_context(context):
        results = await GuardedToolExecutor(max_retries=0).execute(
            [ToolCall(id="knowledge-error", name=retrieve_knowledge.name, arguments={"query": "runbook"})],
            [retrieve_knowledge],
        )

    assert results[0].success is False
    assert results[0].retryable is True
    assert isinstance(results[0].raw, dict)
    assert results[0].raw["error_code"] == "knowledge_retrieval_failed"
    assert "private tenant scope failure" not in results[0].content


@pytest.mark.asyncio
async def test_knowledge_missing_request_scope_is_not_evidence(monkeypatch) -> None:
    def should_not_search(*_args, **_kwargs):
        raise AssertionError("tenant-scoped lookup must fail before data access")

    monkeypatch.setattr("app.tools.knowledge_tool.vector_search_service.search", should_not_search)
    results = await GuardedToolExecutor(max_retries=2, retry_backoff_seconds=0).execute(
        [ToolCall(id="missing-scope", name=retrieve_knowledge.name, arguments={"query": "runbook"})],
        [retrieve_knowledge],
    )

    assert results[0].success is False
    assert results[0].retryable is False
    assert isinstance(results[0].raw, dict)
    assert results[0].raw["error_code"] == "missing_request_scope"


@pytest.mark.asyncio
async def test_invalid_timezone_is_a_terminal_structured_failure() -> None:
    results = await GuardedToolExecutor(max_retries=2, retry_backoff_seconds=0).execute(
        [ToolCall(id="invalid-timezone", name=get_current_time.name, arguments={"timezone": "Mars/Olympus"})],
        [get_current_time],
    )

    assert results[0].success is False
    assert results[0].retryable is False
    assert isinstance(results[0].raw, dict)
    assert results[0].raw["error_code"] == "invalid_timezone"


@pytest.mark.asyncio
async def test_streams_retryability_only_to_internal_tool_event() -> None:
    result = ToolExecutionResult(
        call_id="terminal-failure",
        tool_name="query_recent_changes",
        content='{"success": false}',
        success=False,
        retryable=False,
    )
    events = [
        event
        async for event in stream_tool_results(
            [result],
            messages=[],
            agent_label="harness",
            trace_id="tool-failure-stream",
            args_by_id={"terminal-failure": {}},
        )
    ]

    internal = events[0]
    public = to_public_timeline_event(internal)
    assert internal["status"] == "failed"
    assert internal["payload"]["retryable"] is False
    assert "retryable" not in public.get("payload", {})


def test_harness_suppresses_only_terminal_non_retryable_tools(monkeypatch) -> None:
    service = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="suppression", session_id="suppression")
    state.timeline_events = [
        {
            "type": "tool_event",
            "tool": "query_recent_changes",
            "status": "failed",
            "payload": {"retryable": False},
        },
        {
            "type": "tool_event",
            "tool": "query_prometheus_alerts",
            "status": "failed",
            "payload": {"retryable": True},
        },
    ]
    tools = [
        RuntimeTool(name="query_recent_changes", description="changes"),
        RuntimeTool(name="query_prometheus_alerts", description="alerts"),
        RuntimeTool(name="delegate_to_expert", description="delegate"),
        RuntimeTool(name="context_note", description="note"),
    ]
    monkeypatch.setattr(
        "app.agent.harness.policy.config.harness_slow_path_tool_cap", 0, raising=False
    )
    monkeypatch.setattr(
        "app.agent.harness.policy.config.harness_failed_tool_suppression_enabled",
        True,
        raising=False,
    )

    names = {tool.name for tool in service._filter_tools_by_cap(tools, state)}
    assert "query_recent_changes" not in names
    assert {"query_prometheus_alerts", "delegate_to_expert", "context_note"} <= names

    monkeypatch.setattr(
        "app.agent.harness.policy.config.harness_failed_tool_suppression_enabled",
        False,
        raising=False,
    )
    rollback_names = {tool.name for tool in service._filter_tools_by_cap(tools, state)}
    assert "query_recent_changes" in rollback_names


@pytest.mark.asyncio
async def test_harness_removes_terminal_tool_from_the_next_model_menu(
    tmp_path, monkeypatch
) -> None:
    for attribute, value in (
        ("harness_force_expert_delegation", False),
        ("harness_force_parallel_on_cross_domain", False),
        ("harness_replan_enabled", False),
        ("harness_re_evidence_enabled", False),
        ("harness_investigation_evidence_early_close", False),
        ("harness_knowledge_early_close", False),
        ("harness_corrective_verify_enabled", False),
        ("harness_checkpoint_enabled", False),
        ("harness_llm_planning_enabled", False),
        ("long_term_memory_distill_enabled", False),
        ("harness_anti_pattern_capture_enabled", False),
        ("hitl_suggested_actions_enabled", False),
        ("harness_failed_tool_suppression_enabled", True),
    ):
        monkeypatch.setattr(f"app.config.config.{attribute}", value, raising=False)
    monkeypatch.setattr("app.agent.harness.loop.stateful_context_enabled", lambda: False)

    calls = {"change": 0, "alternative": 0}

    async def unavailable_change(_arguments: dict) -> dict:
        calls["change"] += 1
        return {
            "success": False,
            "source_available": False,
            "message": "change source is intentionally unavailable",
        }

    async def alternative_probe(_arguments: dict) -> dict:
        calls["alternative"] += 1
        return {"success": True, "status": "ok"}

    class RecordingRouter:
        async def _resolve_route(self, _message: str, **_kwargs) -> RouteDecision:
            return RouteDecision(route="change", reason="test", confidence=1.0)

    class RecordingLLM:
        def __init__(self) -> None:
            self.responses = [
                LLMResponse(
                    content="",
                    raw={},
                    tool_calls=[
                        ToolCall(
                            id="change-call",
                            name="query_recent_changes",
                            arguments={},
                        )
                    ],
                    usage={"total_tokens": 1},
                ),
                LLMResponse(
                    content="",
                    raw={},
                    tool_calls=[
                        ToolCall(
                            id="alternative-call",
                            name="lookup_service_knowledge",
                            arguments={},
                        )
                    ],
                    usage={"total_tokens": 1},
                ),
                LLMResponse(content="Evidence gap handled.", raw={}, usage={"total_tokens": 1}),
            ]
            self.tool_menus: list[list[str]] = []

        async def stream_chat(self, _messages, **kwargs):
            self.tool_menus.append([tool.name for tool in kwargs.get("tools") or []])
            response = self.responses.pop(0)
            if response.content:
                yield LLMStreamChunk(content=response.content)
            yield LLMStreamChunk(response=response)

    llm = RecordingLLM()
    repository = ContextRepository(
        db_path=initialize_context_db(tmp_path / "tool-menu.db"),
        settings=ContextRepositorySettings(redis_enabled=False, db_snapshot_enabled=True),
    )
    service = HarnessService(
        router=RecordingRouter(),
        llm_client=llm,
        tools=[
            RuntimeTool(
                name="query_recent_changes",
                description="changes",
                handler=unavailable_change,
            ),
            RuntimeTool(
                name="lookup_service_knowledge",
                description="fallback knowledge",
                handler=alternative_probe,
            ),
        ],
        context_repository=repository,
    )

    events = [
        event
        async for event in service.stream(
            "check the recent release",
            session_id="failure-suppression-e2e",
            owner_key="owner-1",
        )
    ]

    assert calls == {"change": 1, "alternative": 1}
    assert "query_recent_changes" in llm.tool_menus[0]
    assert "query_recent_changes" not in llm.tool_menus[1]
    failed = next(
        event
        for event in events
        if event.get("type") == "tool_event"
        and event.get("tool") == "query_recent_changes"
    )
    assert failed["status"] == "failed"
    assert failed["payload"]["retryable"] is False


class _FailureProgressStream:
    async def stream(self, message: str, session_id: str, owner_key: str = ""):
        del message, session_id, owner_key
        private_failure = {
            "type": "tool_event",
            "agent": "harness",
            "tool": "query_recent_changes",
            "status": "failed",
            "evidence_id": "terminal-private-call",
            "summary": "internal source configuration detail",
            "payload": {
                "arguments": {"service": "private-service"},
                "result": "private failure detail",
                "retryable": False,
            },
        }
        yield private_failure
        yield {"type": "content", "data": "Evidence gap declared."}
        yield {
            "type": "complete",
            "route": "change",
            "answer": "Evidence gap declared.",
            "case_id": "tool-failure-public-contract",
            "events": [private_failure],
        }


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        username="test-user",
        owner_key="owner-1",
        storage_owner_key="owner-1",
        project_id="default",
        role="operator",
    )


@pytest.mark.asyncio
async def test_api_history_keeps_retryability_internal(tmp_path, monkeypatch) -> None:
    from app.agent.context import unified as unified_mod

    db_path = initialize_context_db(tmp_path / "tool-failure.db")
    conversations = ConversationService(db_path)
    repository = ContextRepository(
        db_path=db_path,
        settings=ContextRepositorySettings(redis_enabled=False, db_snapshot_enabled=True),
    )
    real_build = unified_mod.build_completion_committer

    def build_committer(**kwargs):
        kwargs["repository"] = repository
        return real_build(**kwargs)

    monkeypatch.setattr("app.api.assistant.harness_service", _FailureProgressStream())
    monkeypatch.setattr("app.api.assistant.build_completion_committer", build_committer)
    monkeypatch.setattr("app.api.assistant.conversation_service", conversations)
    monkeypatch.setattr("app.api.conversations.conversation_service", conversations)
    response = await assistant(
        ChatRequest(id="tool-failure-public-session", question="recent changes"),
        principal=_principal(),
    )
    streamed = [
        json.loads(item["data"])
        async for item in response.body_iterator
        if isinstance(item, dict) and item.get("data")
    ]
    history = await get_conversation("tool-failure-public-session", owner_key="owner-1")

    public_json = json.dumps({"stream": streamed, "history": history})
    assert "retryable" not in public_json
    assert "private failure detail" not in public_json
    internal = conversations.get_turns("owner-1", "tool-failure-public-session")
    assert internal[0]["events"][0]["payload"]["retryable"] is False
