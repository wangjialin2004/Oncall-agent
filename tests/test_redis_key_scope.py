import pytest

from app.agent.harness.state import HarnessState
from app.services.harness_checkpoint import HarnessCheckpointStore
from tests._fake_redis import FakeRedis


def _state(step: int) -> HarnessState:
    return HarnessState(
        trace_id=f"trace-{step}",
        session_id="session/raw:*",
        owner_key="owner:raw:*",
        step=step,
    )


def test_checkpoint_keys_hash_raw_owner_and_session():
    store = HarnessCheckpointStore(namespace="test")
    prefix = store._prefix("owner:raw:*", "session/raw:*")

    assert prefix.startswith("test:ckpt:")
    assert "owner" not in prefix
    assert "session" not in prefix
    assert "*" not in prefix


@pytest.mark.asyncio
async def test_checkpoint_writes_are_monotonic_and_delete_without_scan():
    redis = FakeRedis(fail_on={"scan"})
    store = HarnessCheckpointStore(
        namespace="test",
        redis_factory=lambda: redis,
    )
    await store.save_step(
        owner_key="owner-a",
        session_id="session-a",
        state=_state(3),
        messages=[],
        step_index=3,
        step_payload={"step": 3},
    )
    await store.save_step(
        owner_key="owner-a",
        session_id="session-a",
        state=_state(2),
        messages=[],
        step_index=2,
        step_payload={"step": 2},
    )

    resume = await store.try_resume("owner-a", "session-a")
    assert resume is not None
    assert resume.next_step == 4
    assert [item["step"] for item in resume.steps] == [3]
    assert await store.delete("owner-a", "session-a") == 3
