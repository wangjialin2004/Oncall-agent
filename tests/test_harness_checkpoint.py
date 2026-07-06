"""Tests for the harness loop step-level checkpoint subsystem."""

from __future__ import annotations

import pytest

from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessState
from app.core.llm_client import ChatMessage
from app.services.harness_checkpoint import (
    HarnessCheckpointStore,
    is_checkpoint_active,
    messages_from_dict,
    reset_default_checkpoint_store,
)
from tests._fake_redis import FakeRedis

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_store_singleton() -> None:
    reset_default_checkpoint_store()
    yield
    reset_default_checkpoint_store()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def store(fake_redis: FakeRedis) -> HarnessCheckpointStore:
    return HarnessCheckpointStore(
        namespace="test",
        ttl_seconds=300,
        idempotent_tools=("delegate_to_expert",),
        redis_factory=lambda: fake_redis,
    )


def _state(**overrides) -> HarnessState:
    base = {
        "trace_id": "trace-1",
        "session_id": "sess-1",
        "owner_key": "owner-1",
        "route": "metric",
        "route_reason": "seed",
        "step": 2,
        "answer_parts": ["hello"],
        "usage_total": {"total_tokens": 100},
        "token_estimate": 120,
    }
    base.update(overrides)
    return HarnessState(**base)


def _messages() -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="hi"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "delegate_to_expert", "arguments": "{}"},
                }
            ],
        ),
        ChatMessage(role="tool", content="done", tool_call_id="call-1"),
    ]


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_load_roundtrip(store: HarnessCheckpointStore, fake_redis: FakeRedis) -> None:
    state = _state()
    messages = _messages()
    await store.save_step(
        owner_key="owner-1",
        session_id="sess-1",
        state=state,
        messages=messages,
        step_index=2,
        step_payload={
            "step": 2,
            "tool_calls": [{"id": "call-1", "function": {"name": "delegate_to_expert"}}],
            "events": [{"type": "agent_event", "stage": "tool_event"}],
            "completed": True,
        },
    )

    assert len(fake_redis.data) == 3  # meta + steps:2 + messages

    resume = await store.try_resume("owner-1", "sess-1")
    assert resume is not None
    assert resume.next_step == 3
    assert resume.steps and resume.steps[0]["tool_calls"]
    restored = messages_from_dict(resume.messages)
    assert [m.role for m in restored] == ["system", "user", "assistant", "tool"]
    assert restored[2].tool_calls is not None and restored[2].tool_calls[0]["id"] == "call-1"
    assert resume.state_fields["trace_id"] == "trace-1"


# ---------------------------------------------------------------------------
# Partial resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_skips_to_next_step(store: HarnessCheckpointStore) -> None:
    state = _state(step=2)
    await store.save_step(
        owner_key="o",
        session_id="s",
        state=state,
        messages=_messages(),
        step_index=2,
        step_payload={"step": 2, "tool_calls": [], "events": [], "completed": True},
    )

    resume = await store.try_resume("o", "s")
    assert resume is not None
    assert resume.next_step == 3
    # Conservative: only one tool used (delegate_to_expert, in whitelist).
    assert HarnessService._should_replay_resume(resume) is True


@pytest.mark.asyncio
async def test_aggressive_replay_replays_verbatim(
    store: HarnessCheckpointStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.config.config.harness_checkpoint_replay", True, raising=False)
    state = _state(step=2)
    # Step uses a non-whitelisted tool — conservative would refuse to replay.
    await store.save_step(
        owner_key="o",
        session_id="s",
        state=state,
        messages=_messages(),
        step_index=2,
        step_payload={
            "step": 2,
            "tool_calls": [
                {"id": "c", "function": {"name": "query_metrics"}},
            ],
            "events": [],
            "completed": True,
        },
    )
    resume = await store.try_resume("o", "s")
    assert resume is not None
    assert HarnessService._should_replay_resume(resume) is True


@pytest.mark.asyncio
async def test_request_replay_override_forces_replay(
    store: HarnessCheckpointStore,
) -> None:
    """Per-request ``replay_override=True`` overrides config default and forces replay.

    Config stays at the conservative default (False); the checkpoint step uses a
    non-whitelisted tool. Without override, conservative would refuse. The
    explicit True from the HTTP layer must win.
    """
    state = _state(step=2)
    await store.save_step(
        owner_key="o",
        session_id="s",
        state=state,
        messages=_messages(),
        step_index=2,
        step_payload={
            "step": 2,
            "tool_calls": [
                {"id": "c", "function": {"name": "query_prometheus_alerts"}},
            ],
            "events": [],
            "completed": True,
        },
    )
    resume = await store.try_resume("o", "s")
    assert resume is not None
    # Conservative (no override): non-whitelist → no replay.
    assert HarnessService._should_replay_resume(resume) is False
    # Override True forces replay regardless of whitelist.
    assert HarnessService._should_replay_resume(resume, replay_override=True) is True


@pytest.mark.asyncio
async def test_request_replay_override_forces_conservative(
    store: HarnessCheckpointStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-request ``replay_override=False`` overrides config aggressive flag.

    Config is flipped to ``harness_checkpoint_replay=True`` so the default would
    replay verbatim. Override False must make the whitelist re-check happen,
    so a non-whitelisted step still triggers conservative close.
    """
    monkeypatch.setattr("app.config.config.harness_checkpoint_replay", True, raising=False)
    state = _state(step=2)
    await store.save_step(
        owner_key="o",
        session_id="s",
        state=state,
        messages=_messages(),
        step_index=2,
        step_payload={
            "step": 2,
            "tool_calls": [
                {"id": "c", "function": {"name": "query_prometheus_alerts"}},
            ],
            "events": [],
            "completed": True,
        },
    )
    resume = await store.try_resume("o", "s")
    assert resume is not None
    # Config aggressive → replay.
    assert HarnessService._should_replay_resume(resume) is True
    # Override False forces conservative: non-whitelist step → no replay.
    assert HarnessService._should_replay_resume(resume, replay_override=False) is False


@pytest.mark.asyncio
async def test_conservative_skips_non_idempotent_step(
    store: HarnessCheckpointStore,
) -> None:
    state = _state(step=2)
    await store.save_step(
        owner_key="o",
        session_id="s",
        state=state,
        messages=_messages(),
        step_index=2,
        step_payload={
            "step": 2,
            "tool_calls": [
                {"id": "c1", "function": {"name": "query_prometheus_alerts"}},
            ],
            "events": [],
            "completed": True,
        },
    )
    resume = await store.try_resume("o", "s")
    assert resume is not None
    assert HarnessService._should_replay_resume(resume) is False


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_unavailable_does_not_raise(store: HarnessCheckpointStore) -> None:
    store._redis_factory = lambda: FakeRedis(fail_on={"set"})  # type: ignore[assignment]
    # Should swallow the exception and bump stats.
    await store.save_step(
        owner_key="o",
        session_id="s",
        state=_state(step=1),
        messages=_messages(),
        step_index=1,
        step_payload={"step": 1, "tool_calls": [], "events": [], "completed": True},
    )
    assert store.stats.errors == 1
    # try_resume on a fully broken client returns None.
    resume = await store.try_resume("o", "s")
    assert resume is None


@pytest.mark.asyncio
async def test_ttl_expired_returns_none(store: HarnessCheckpointStore, fake_redis: FakeRedis) -> None:
    await store.save_step(
        owner_key="o",
        session_id="s",
        state=_state(step=1),
        messages=_messages(),
        step_index=1,
        step_payload={"step": 1, "tool_calls": [], "events": [], "completed": True},
    )
    # Advance the fake clock past the TTL.
    fake_redis.now += 10_000
    resume = await store.try_resume("o", "s")
    assert resume is None


@pytest.mark.asyncio
async def test_completed_meta_does_not_resume(store: HarnessCheckpointStore) -> None:
    await store.save_step(
        owner_key="o",
        session_id="s",
        state=_state(step=3),
        messages=_messages(),
        step_index=3,
        step_payload={"step": 3, "tool_calls": [], "events": [], "completed": True},
    )
    await store.mark_completed(owner_key="o", session_id="s", state=_state(step=3))
    resume = await store.try_resume("o", "s")
    assert resume is None


# ---------------------------------------------------------------------------
# Isolation + scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_sessions_isolated(store: HarnessCheckpointStore) -> None:
    await store.save_step(
        owner_key="A",
        session_id="shared",
        state=_state(session_id="shared", owner_key="A", step=1),
        messages=_messages(),
        step_index=1,
        step_payload={"step": 1, "tool_calls": [], "events": [], "completed": True},
    )
    await store.save_step(
        owner_key="B",
        session_id="shared",
        state=_state(session_id="shared", owner_key="B", step=1),
        messages=_messages(),
        step_index=1,
        step_payload={"step": 1, "tool_calls": [], "events": [], "completed": True},
    )

    a = await store.try_resume("A", "shared")
    b = await store.try_resume("B", "shared")
    assert a is not None and b is not None
    assert a.state_fields["owner_key"] == "A"
    assert b.state_fields["owner_key"] == "B"

    removed = await store.delete("A", "shared")
    assert removed == 3  # meta + steps:1 + messages
    assert await store.try_resume("A", "shared") is None
    assert await store.try_resume("B", "shared") is not None


# ---------------------------------------------------------------------------
# from_dict round-trip
# ---------------------------------------------------------------------------


def test_chat_message_roundtrip() -> None:
    original = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            {
                "id": "x",
                "type": "function",
                "function": {"name": "delegate_to_expert", "arguments": "{}"},
            }
        ],
    )
    restored = ChatMessage.from_dict(original.to_dict())
    assert restored is not None
    assert restored.role == "assistant"
    assert restored.tool_calls and restored.tool_calls[0]["id"] == "x"


def test_chat_message_from_dict_garbage() -> None:
    assert ChatMessage.from_dict(None) is None  # type: ignore[arg-type]
    assert ChatMessage.from_dict({}) is None
    assert ChatMessage.from_dict({"role": "bogus"}) is None
    assert (
        ChatMessage.from_dict(
            {"role": "user", "tool_calls": "not-a-list"}
        )
        is not None
    )


# ---------------------------------------------------------------------------
# is_step_idempotent
# ---------------------------------------------------------------------------


def test_is_step_idempotent_whitelist(store: HarnessCheckpointStore) -> None:
    assert store.is_step_idempotent([]) is True
    assert store.is_step_idempotent(["delegate_to_expert"]) is True
    assert store.is_step_idempotent(["query_metrics"]) is False
    assert (
        store.is_step_idempotent(["delegate_to_expert", "query_metrics"])
        is False
    )


# ---------------------------------------------------------------------------
# Feature flag gate
# ---------------------------------------------------------------------------


def test_is_checkpoint_active_when_all_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.config.harness_enabled", False, raising=False)
    monkeypatch.setattr("app.config.config.harness_checkpoint_enabled", False, raising=False)
    monkeypatch.setattr("app.config.config.redis_enabled", False, raising=False)
    assert is_checkpoint_active() is False
