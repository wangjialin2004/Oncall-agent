"""Tests for the harness checkpoint trim (Task 9).

Per plan ``plan/2026-07-08-stateful-agent-context.md`` §9, ``save_step``
records ``context_version`` / ``context_snapshot_ref`` in the meta. Resume
returns those fields on :class:`CheckpointResume`. ``persist_messages=False``
suppresses the messages payload write.

These tests deliberately exercise the checkpoint module in isolation rather
than coupling to the full harness service.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.context.operations import framework_patch
from app.agent.context.state import SECTION_INTENT, AgentContextState
from app.agent.context.store import ContextStateStore, ContextStateStoreSettings
from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessState
from app.config import config
from app.core.llm_client import ChatMessage, LLMResponse, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.services.harness_checkpoint import (
    HarnessCheckpointStore,
)
from app.services.router_service import RouteDecision
from tests._fake_redis import FakeRedis


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture(autouse=True)
def _disable_unrelated_memory_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep context tests isolated from live experience distill/index hooks."""

    monkeypatch.setattr(config, "long_term_memory_distill_enabled", False)
    monkeypatch.setattr(config, "harness_anti_pattern_capture_enabled", False)


@pytest.fixture
def store(fake_redis: FakeRedis) -> HarnessCheckpointStore:
    return HarnessCheckpointStore(
        namespace="t9",
        ttl_seconds=300,
        idempotent_tools=("delegate_to_expert",),
        redis_factory=lambda: fake_redis,
    )


def _state(**overrides) -> HarnessState:
    base = {"trace_id": "trace-1", "session_id": "sess-1", "owner_key": "owner-1",
            "route": "harness", "route_reason": "test", "step": 1}
    base.update(overrides)
    return HarnessState(**base)


class _Router:
    async def _resolve_route(self, message: str) -> RouteDecision:
        return RouteDecision(route="diagnosis", reason="stateful-test", confidence=0.9)


class _LLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs})
        return self.responses.pop(0)

    async def stream_complete(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": {**kwargs, "stream": True}})
        response = self.responses.pop(0)
        yield response.content

    async def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": {**kwargs, "stream": True}})
        from app.core.llm_client import LLMStreamChunk

        response = self.responses.pop(0)
        if response.content:
            yield LLMStreamChunk(content=response.content)
        yield LLMStreamChunk(response=response)


class _ExplodingContextBuilder:
    async def abuild(self, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("legacy ContextBuilder.abuild should not be called")

    def _build_system_prompt(self, **kwargs) -> str:
        return "base harness prompt"


class _MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def __call__(self, op: str, key: str, value, ttl):
        if op == "get":
            return self.data.get(key)
        if op == "set":
            self.data[key] = str(value)
            return True
        raise ValueError(op)


def _store(redis: _MemoryRedis) -> ContextStateStore:
    snapshots: dict[tuple[str, str], AgentContextState] = {}

    def snapshot_get(*, owner_key: str, session_id: str):
        return snapshots.get((owner_key, session_id))

    def snapshot_save(*, owner_key: str, session_id: str, state: AgentContextState):
        snapshots[(owner_key, session_id)] = state

    return ContextStateStore(
        db_snapshot_get=snapshot_get,
        db_snapshot_save=snapshot_save,
        redis_get_set=redis,
        settings=ContextStateStoreSettings(
            redis_enabled=True,
            redis_namespace="stateful-test",
            redis_ttl_seconds=300,
            db_snapshot_enabled=True,
            patch_history_limit=200,
        ),
    )


async def test_save_step_records_context_version_and_ref(
    store: HarnessCheckpointStore, fake_redis: FakeRedis,
) -> None:
    state = _state()
    messages = [ChatMessage(role="user", content="hi")]
    await store.save_step(
        owner_key="owner-1",
        session_id="sess-1",
        state=state,
        messages=messages,
        step_index=1,
        step_payload={"step": 1},
        context_version=42,
        context_snapshot_ref="owner-1:sess-1:v42",
    )
    meta_raw = await fake_redis.get(store._meta_key("owner-1", "sess-1"))
    meta = json.loads(meta_raw)
    assert meta["context_version"] == 42
    assert meta["context_snapshot_ref"] == "owner-1:sess-1:v42"


async def test_try_resume_returns_context_reference(
    store: HarnessCheckpointStore,
) -> None:
    state = _state()
    await store.save_step(
        owner_key="owner-1",
        session_id="sess-1",
        state=state,
        messages=[],
        step_index=1,
        step_payload={"step": 1},
        context_version=9,
        context_snapshot_ref="ref-9",
    )
    resume = await store.try_resume("owner-1", "sess-1")
    assert resume is not None
    assert resume.context_version == 9
    assert resume.context_snapshot_ref == "ref-9"
    assert resume.checkpoint_version == 2  # bumped on write


async def test_persist_messages_false_still_writes_run_metadata(
    store: HarnessCheckpointStore, fake_redis: FakeRedis,
) -> None:
    state = _state()
    await store.save_step(
        owner_key="owner-1",
        session_id="sess-1",
        state=state,
        messages=[ChatMessage(role="user", content="hi")],
        step_index=1,
        step_payload={"step": 1},
        context_version=5,
        context_snapshot_ref="ref-5",
        persist_messages=False,
    )
    # Meta carries context reference…
    meta = json.loads(await fake_redis.get(store._meta_key("owner-1", "sess-1")))
    assert meta["context_version"] == 5
    # …but messages payload is empty.
    msgs = json.loads(
        await fake_redis.get(store._messages_key("owner-1", "sess-1")) or "[]"
    )
    assert msgs == []


async def test_legacy_checkpoint_without_context_fields_still_resumes(
    store: HarnessCheckpointStore, fake_redis: FakeRedis,
) -> None:
    """Old checkpoints (no context_version/context_snapshot_ref) must still load.

    ``checkpoint_version=1`` signals the migration path is still in effect; the
    harness should rehydrate context via
    :class:`app.agent.context.store.ContextStateStore`.
    """
    state = _state()
    await store.save_step(
        owner_key="owner-1",
        session_id="sess-1",
        state=state,
        messages=[],
        step_index=1,
        step_payload={"step": 1},
    )
    resume = await store.try_resume("owner-1", "sess-1")
    assert resume is not None
    # Either the recorded version is bumped (write path updated) — at minimum
    # the resume object must expose the new fields as ``None``.
    assert resume.context_version is None
    assert resume.context_snapshot_ref is None


async def test_existing_signature_still_works_without_context_kwargs(
    store: HarnessCheckpointStore,
) -> None:
    """Regression: callers passing only the original kwargs must not break."""
    state = _state()
    # No context_version, no context_snapshot_ref — code path exercises None coercion.
    await store.save_step(
        owner_key="owner-1",
        session_id="sess-1",
        state=state,
        messages=[],
        step_index=1,
        step_payload={"step": 1},
    )
    resume = await store.try_resume("owner-1", "sess-1")
    assert resume is not None
    assert resume.next_step == 2


async def test_state_fields_no_longer_contains_timeline(
    store: HarnessCheckpointStore, fake_redis: FakeRedis,
) -> None:
    """Plan §9: checkpoint meta should not grow unbounded as timeline does.

    The harness state already excludes ``timeline_events`` from
    ``state_fields`` (kept only as a tail on meta). Verify that contract
    hasn't regressed.
    """
    state = _state()
    state.timeline_events = [{"type": "step_start"} for _ in range(50)]
    await store.save_step(
        owner_key="owner-1",
        session_id="sess-1",
        state=state,
        messages=[],
        step_index=1,
        step_payload={"step": 1},
    )
    meta = json.loads(await fake_redis.get(store._meta_key("owner-1", "sess-1")))
    assert "timeline_events" not in (meta.get("state_fields") or {})
    # Tail still capped at 20.
    assert len(meta.get("timeline_events_tail") or []) <= 20


def test_stateful_context_config_flags_are_explicit() -> None:
    assert config.harness_stateful_context_enabled is True
    assert config.harness_context_rebuild_from_turns_enabled is True
    assert config.harness_context_db_snapshot_enabled is True
    assert config.harness_context_tools_enabled is True


@pytest.mark.asyncio
async def test_stateful_context_redis_hit_skips_legacy_context_builder(monkeypatch) -> None:
    monkeypatch.setattr(config, "harness_stateful_context_enabled", True)
    monkeypatch.setattr(config, "harness_context_tools_enabled", False)
    redis = _MemoryRedis()
    store = _store(redis)
    seeded = AgentContextState(owner_key="owner-1", session_id="sess-stateful")
    framework_patch(seeded, SECTION_INTENT, "current_goal", op="set", value="seeded")
    await store.save("owner-1", "sess-stateful", seeded)

    llm = _LLM([LLMResponse(content="final answer", raw={})])
    service = HarnessService(
        context_builder=_ExplodingContextBuilder(),
        router=_Router(),
        llm_client=llm,
        tools=[],
        context_store=store,
    )

    events = [
        event
        async for event in service.stream(
            "redis hit?", session_id="sess-stateful", owner_key="owner-1"
        )
    ]

    assert any(event.get("type") == "complete" for event in events)
    system_prompt = llm.calls[0]["messages"][0].content  # type: ignore[index,union-attr]
    assert "base harness prompt" in system_prompt
    assert "当前会话状态白板" in system_prompt
    assert "current_goal: redis hit?" not in system_prompt
    assert "redis hit?" not in system_prompt
    assert "redis hit?" in llm.calls[0]["messages"][-1].content  # type: ignore[index,union-attr]


@pytest.mark.asyncio
async def test_stateful_context_registers_only_safe_context_tools(monkeypatch) -> None:
    monkeypatch.setattr(config, "harness_stateful_context_enabled", True)
    monkeypatch.setattr(config, "harness_context_tools_enabled", True)
    redis = _MemoryRedis()
    store = _store(redis)
    llm = _LLM([LLMResponse(content="done", raw={})])
    service = HarnessService(
        context_builder=_ExplodingContextBuilder(),
        router=_Router(),
        llm_client=llm,
        tools=[],
        context_store=store,
    )

    _ = [
        event
        async for event in service.stream(
            "tool list?", session_id="sess-tools", owner_key="owner-1"
        )
    ]

    tool_defs = llm.calls[0]["kwargs"]["tools"]  # type: ignore[index]
    names = {tool.name for tool in tool_defs}
    assert {"context_read", "context_note", "read_attachment"}.issubset(names)
    assert "context_patch" not in names
    assert "context_rollback" not in names


@pytest.mark.asyncio
async def test_stateful_context_merges_current_attachment_refs(monkeypatch) -> None:
    monkeypatch.setattr(config, "harness_stateful_context_enabled", True)
    monkeypatch.setattr(config, "harness_context_tools_enabled", True)
    redis = _MemoryRedis()
    store = _store(redis)
    llm = _LLM([LLMResponse(content="done", raw={})])
    service = HarnessService(
        context_builder=_ExplodingContextBuilder(),
        router=_Router(),
        llm_client=llm,
        tools=[],
        context_store=store,
    )

    _ = [
        event
        async for event in service.stream(
            "read uploaded file",
            session_id="sess-attachment",
            owner_key="owner-1",
            attachment_refs=[
                {
                    "file_id": "file_1",
                    "file_name": "runbook.md",
                    "summary": "CPU runbook",
                    "keywords": ["cpu"],
                    "status": "indexed",
                }
            ],
        )
    ]

    loaded = await store.get_or_rebuild("owner-1", "sess-attachment")
    assert loaded.state.conversation.active_attachment_refs[0]["file_id"] == "file_1"
    system_prompt = llm.calls[0]["messages"][0].content  # type: ignore[index,union-attr]
    assert "active_attachments" in system_prompt
    assert "file_1" in system_prompt


@pytest.mark.asyncio
async def test_stateful_context_records_tool_evidence_and_checkpoint_ref(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "harness_stateful_context_enabled", True)
    monkeypatch.setattr(config, "harness_context_tools_enabled", False)
    redis = _MemoryRedis()
    store = _store(redis)
    fake_redis = FakeRedis()
    checkpoint_store = HarnessCheckpointStore(
        namespace="stateful-ckpt",
        ttl_seconds=300,
        redis_factory=lambda: fake_redis,
    )
    async def fake_check_redis(arguments):
        return {
            "ok": True,
            "summary": "ping ok",
            "latency_ms": 7,
            "fact": "Redis PING 成功",
        }

    tool = RuntimeTool(
        name="check_redis_health",
        description="Check Redis.",
        handler=fake_check_redis,
    )
    llm = _LLM(
        [
            LLMResponse(
                content="",
                raw={},
                tool_calls=[
                    ToolCall(id="call-redis", name="check_redis_health", arguments={})
                ],
            ),
            LLMResponse(content="redis is healthy", raw={}),
        ]
    )
    service = HarnessService(
        context_builder=_ExplodingContextBuilder(),
        router=_Router(),
        llm_client=llm,
        tools=[tool],
        context_store=store,
        checkpoint_store=checkpoint_store,
    )

    _ = [
        event
        async for event in service.stream(
            "check redis", session_id="sess-evidence", owner_key="owner-1"
        )
    ]
    await asyncio.sleep(0.05)

    loaded = await store.get_or_rebuild("owner-1", "sess-evidence")
    assert loaded.state.evidence.tool_summaries
    assert loaded.state.evidence.tool_summaries[0].tool == "check_redis_health"
    assert loaded.state.evidence.tool_summaries[0].raw_ref == "tool:call-redis"

    meta = json.loads(
        await fake_redis.get(checkpoint_store._meta_key("owner-1", "sess-evidence"))
    )
    assert meta["context_version"] is not None
    assert meta["context_snapshot_ref"].endswith(f"v{meta['context_version']}")
    messages = json.loads(
        await fake_redis.get(checkpoint_store._messages_key("owner-1", "sess-evidence"))
        or "[]"
    )
    assert messages == []
