"""Gated wiring tests for unified context repository path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agent.context.envelope import CompletedTurnCommit
from app.agent.context.operations import set_intent
from app.agent.context.state import AgentContextState
from app.agent.context.unified import (
    build_completion_committer,
    inflight_context_ref,
    load_unified_stateful_context,
    prepare_unified_context,
)
from app.agent.harness.loop import HarnessService
from app.config import config
from app.core.llm_client import LLMResponse, LLMStreamChunk
from app.models.request import ChatRequest
from app.services.context_repository import ContextRepository, ContextRepositorySettings
from app.services.router_service import RouteDecision
from app.services.session_scope_service import AuthenticatedPrincipal
from tests._context_db import initialize_context_db


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def __call__(self, op: str, key: str, value, ttl):
        if op == "get":
            return self.store.get(key)
        if op == "set":
            self.store[key] = value
            return True
        if op == "delete":
            self.store.pop(key, None)
            return 1
        raise ValueError(op)


class SpyRepository(ContextRepository):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.load_calls = 0

    async def load_envelope(self, owner_key: str, session_id: str):
        self.load_calls += 1
        return await super().load_envelope(owner_key, session_id)


class ExplodingLegacyBuilder:
    def __init__(self) -> None:
        self.abuild_calls = 0

    async def abuild(self, **kwargs):
        self.abuild_calls += 1
        raise AssertionError("legacy ContextBuilder.abuild must not load unified context")

    def _build_system_prompt(self, **kwargs) -> str:
        return "base harness prompt"


class ExplodingLegacyStore:
    async def get_or_rebuild(self, *args, **kwargs):
        raise AssertionError("legacy ContextStateStore must not load unified context")

    async def save(self, *args, **kwargs):
        raise AssertionError("legacy ContextStateStore must not persist unified context")


class FixedRouter:
    async def _resolve_route(self, message: str) -> RouteDecision:
        return RouteDecision(route="diagnosis", reason="test", confidence=0.9)


class FinalOnlyLlm:
    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(list(messages))
        return LLMResponse(content="done", raw={})

    async def stream_chat(self, messages, **kwargs):
        self.calls.append(list(messages))
        response = LLMResponse(content="done", raw={})
        yield LLMStreamChunk(content="done")
        yield LLMStreamChunk(response=response)

    async def stream_complete(self, messages, **kwargs):
        self.calls.append(list(messages))
        yield "done"


@pytest.mark.asyncio
async def test_completion_committer_writes_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.context_repository.config.harness_unified_context_repository_enabled",
        True,
        raising=False,
    )
    redis = FakeRedis()
    db_path = initialize_context_db(tmp_path / "memory.db")
    repository = ContextRepository(
        db_path=db_path,
        redis_get_set=redis,
        settings=ContextRepositorySettings(db_snapshot_enabled=True, redis_enabled=True),
    )
    committer = build_completion_committer(
        owner_key="o",
        session_id="s",
        commit_id="trace-xyz",
        user_message="question",
        user_context="",
        attachment_refs=[],
        repository=repository,
    )
    state = AgentContextState(owner_key="o", session_id="s")
    event = {
        "type": "complete",
        "answer": "final answer",
        "route": "harness",
        "case_id": "c1",
        "events": [{"type": "agent_event", "stage": "complete"}],
    }
    first = await committer(event, state)
    second = await committer(event, state)
    assert first is not None and first.committed
    assert second is not None and second.idempotent_replay
    envelope = await repository.load_envelope("o", "s")
    assert len(envelope.turn_window) == 1
    assert envelope.turn_window[0]["user_message"] == "question"
    assert envelope.watermark.last_applied_turn_index == 0


@pytest.mark.asyncio
async def test_stream_invokes_committer_before_yielding_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.context_repository.config.harness_unified_context_repository_enabled",
        True,
        raising=False,
    )
    calls: list[dict[str, Any]] = []

    async def fake_inner(self, *args, **kwargs):
        yield {"type": "agent_event", "stage": "start"}
        yield {
            "type": "complete",
            "answer": "ok",
            "route": "harness",
            "case_id": "",
            "events": [],
        }

    async def committer(event, context_state):
        calls.append({"event": event, "context_state": context_state})
        return {"committed": True}

    monkeypatch.setattr(HarnessService, "_stream_inner", fake_inner)
    service = HarnessService()
    events = []
    async for event in service.stream(
        "hi",
        session_id="s1",
        owner_key="o1",
        completion_committer=committer,
    ):
        events.append(event)
    assert calls and calls[0]["event"]["type"] == "complete"
    assert events[-1]["type"] == "complete"
    assert events[-1]["_unified_context_committed"] is True


@pytest.mark.asyncio
async def test_stream_marks_failed_unified_commit_as_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_inner(self, *args, **kwargs):
        yield {"type": "complete", "answer": "ok", "events": []}

    async def failing_committer(event, context_state):
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(HarnessService, "_stream_inner", fake_inner)
    service = HarnessService(checkpoint_store=None)
    events = [
        event
        async for event in service.stream(
            "hi",
            session_id="s1",
            owner_key="o1",
            completion_committer=failing_committer,
        )
    ]
    assert events[-1]["_unified_context_commit_attempted"] is True
    assert events[-1]["_unified_context_committed"] is False
    assert events[-1]["_unified_context_commit_error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_load_unified_stateful_context_uses_repository(tmp_path: Path) -> None:
    redis = FakeRedis()
    db_path = initialize_context_db(tmp_path / "memory.db")
    repository = ContextRepository(
        db_path=db_path,
        redis_get_set=redis,
        settings=ContextRepositorySettings(db_snapshot_enabled=True),
    )
    await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="o",
            session_id="s",
            commit_id="c1",
            user_message="prev q",
            assistant_answer="prev a",
            projection=AgentContextState(owner_key="o", session_id="s"),
        )
    )
    ctx = await load_unified_stateful_context(
        owner_key="o",
        session_id="s",
        current_question="next q",
        current_goal="next q",
        repository=repository,
    )
    assert ctx.state.intent.current_question == "next q"
    assert any(item.get("content") == "prev q" for item in ctx.recent_messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("stateful", [True, False])
async def test_real_harness_uses_one_repository_load_for_both_renderers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stateful: bool,
) -> None:
    monkeypatch.setattr(config, "harness_unified_context_repository_enabled", True)
    monkeypatch.setattr(config, "harness_stateful_context_enabled", stateful)
    monkeypatch.setattr(config, "harness_context_tools_enabled", False)
    monkeypatch.setattr(config, "harness_checkpoint_enabled", False)
    monkeypatch.setattr(config, "harness_anti_pattern_capture_enabled", False)
    monkeypatch.setattr(config, "long_term_memory_distill_enabled", False)
    db_path = initialize_context_db(tmp_path / f"renderer-{stateful}.db")
    redis = FakeRedis()
    repository = SpyRepository(
        db_path=db_path,
        redis_get_set=redis,
        settings=ContextRepositorySettings(redis_enabled=True, db_snapshot_enabled=True),
    )
    builder = ExplodingLegacyBuilder()
    llm = FinalOnlyLlm()
    service = HarnessService(
        context_builder=builder,
        router=FixedRouter(),
        llm_client=llm,
        tools=[],
        context_store=ExplodingLegacyStore(),
        context_repository=repository,
    )

    events = [
        event
        async for event in service.stream(
            "current question",
            session_id="s",
            owner_key="o",
            context_run_id="run-1",
        )
    ]

    assert events[-1]["type"] == "complete"
    assert repository.load_calls == 1
    assert builder.abuild_calls == 0
    system_prompt = str(llm.calls[0][0].content)
    assert "base harness prompt" in system_prompt
    assert ("当前会话白板" in system_prompt) is stateful


@pytest.mark.asyncio
async def test_checkpoint_resume_reads_inflight_but_normal_load_does_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "harness_unified_context_repository_enabled", True)
    db_path = initialize_context_db(tmp_path / "inflight-resume.db")
    redis = FakeRedis()
    repository = ContextRepository(
        db_path=db_path,
        redis_get_set=redis,
        settings=ContextRepositorySettings(redis_enabled=True, db_snapshot_enabled=True),
    )
    committed = AgentContextState(owner_key="o", session_id="s")
    set_intent(committed, current_question="base", current_goal="base-goal")
    await repository.commit_completed_turn(
        CompletedTurnCommit(
            owner_key="o",
            session_id="s",
            commit_id="base",
            user_message="base",
            assistant_answer="base answer",
            projection=committed,
        )
    )
    envelope = await repository.load_envelope("o", "s")
    inflight = envelope.projection
    set_intent(inflight, current_question="partial", current_goal="partial-goal")
    inflight.working.plan = "partial checkpoint plan"
    await repository.save_inflight(
        owner_key="o",
        session_id="s",
        run_id="run-resume",
        state=inflight,
        base_projection_version=envelope.watermark.projection_version,
    )

    resumed = await prepare_unified_context(
        owner_key="o",
        session_id="s",
        current_question="resume request",
        current_goal="resume request",
        repository=repository,
        stateful=True,
        run_id="run-resume",
        resume_context_ref=inflight_context_ref("run-resume"),
        resume_context_version=inflight.version,
    )
    assert resumed.envelope.source == "redis_inflight"
    assert resumed.state.working.plan == "partial checkpoint plan"

    normal = await prepare_unified_context(
        owner_key="o",
        session_id="s",
        current_question="new request",
        current_goal="new request",
        repository=repository,
        stateful=True,
        run_id="new-run",
    )
    assert normal.envelope.source in {"redis_committed", "sqlite_projection"}
    assert normal.state.working.plan == ""


@pytest.mark.asyncio
async def test_completion_commit_deletes_matching_inflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "harness_unified_context_repository_enabled", True)
    db_path = initialize_context_db(tmp_path / "inflight-delete.db")
    redis = FakeRedis()
    repository = ContextRepository(
        db_path=db_path,
        redis_get_set=redis,
        settings=ContextRepositorySettings(
            redis_enabled=True,
            redis_namespace="testns",
            db_snapshot_enabled=True,
        ),
    )
    state = AgentContextState(owner_key="o", session_id="s")
    await repository.save_inflight(
        owner_key="o",
        session_id="s",
        run_id="run-delete",
        state=state,
        base_projection_version=0,
    )
    committer = build_completion_committer(
        owner_key="o",
        session_id="s",
        commit_id="commit-delete",
        user_message="q",
        repository=repository,
        run_id="run-delete",
    )
    await committer({"type": "complete", "answer": "a", "events": []}, state)
    assert "testns:context:inflight:o:s:run-delete" not in redis.store


@pytest.mark.asyncio
async def test_api_does_not_legacy_append_after_unified_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.assistant import assistant

    monkeypatch.setattr(config, "harness_unified_context_repository_enabled", True)
    persisted: list[dict[str, Any]] = []

    class FailedUnifiedStream:
        async def stream(self, message, session_id, completion_committer=None, **kwargs):
            assert completion_committer is not None
            yield {
                "type": "complete",
                "answer": "visible answer",
                "events": [],
                "_unified_context_commit_attempted": True,
                "_unified_context_committed": False,
            }

    monkeypatch.setattr("app.api.assistant.harness_service", FailedUnifiedStream())
    monkeypatch.setattr(
        "app.api.assistant._persist_turn",
        lambda *args, **kwargs: persisted.append({"args": args, "kwargs": kwargs}),
    )
    principal = AuthenticatedPrincipal(
        username="test",
        owner_key="stable-owner",
        storage_owner_key="storage-owner",
        project_id="default",
        role="operator",
    )
    response = await assistant(
        ChatRequest(id="session-1", question="question"),
        principal=principal,
    )
    chunks = [chunk async for chunk in response.body_iterator]
    assert chunks
    assert persisted == []
