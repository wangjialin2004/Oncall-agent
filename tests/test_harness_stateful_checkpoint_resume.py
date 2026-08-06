from app.agent.harness.checkpoint_ops import HarnessCheckpointOpsMixin
from app.agent.harness.state import HarnessState
from app.services.harness_checkpoint import CheckpointResume


def test_stateful_checkpoint_restores_state_without_messages():
    fallback = HarnessState(
        trace_id="new-trace",
        session_id="session-1",
        owner_key="owner-1",
        route="harness",
    )
    resume = CheckpointResume(
        next_step=4,
        state_fields={
            "trace_id": "old-trace",
            "session_id": "session-1",
            "owner_key": "owner-1",
            "route": "metric",
            "route_reason": "restored",
            "step": 3,
            "answer_parts": ["partial answer"],
            "usage_total": {"total_tokens": 42},
        },
        messages=[],
        steps=[{"step": 3}],
        route="metric",
        route_reason="restored",
        idempotent_tools=[],
        started_at="2026-07-18T00:00:00Z",
        completed=False,
        context_version=7,
        context_snapshot_ref="owner-1:session-1:v7",
    )

    restored = HarnessCheckpointOpsMixin._restore_state_from_resume(resume, fallback)

    assert resume.messages == []
    assert restored.trace_id == "old-trace"
    assert restored.route == "metric"
    assert restored.step == 3
    assert restored.answer_parts == ["partial answer"]
