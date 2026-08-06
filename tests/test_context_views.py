"""Tests for ``app.agent.context.views``."""

from __future__ import annotations

import pytest

from app.agent.context.operations import (
    append_evidence_gap,
    append_observed_fact,
    append_recent_turns,
    append_tool_summary,
    llm_note,
    set_intent,
)
from app.agent.context.state import (
    AgentContextState,
    EvidenceItem,
    SCHEMA_VERSION,
    ToolSummary,
)
from app.agent.context.views import (
    DEFAULT_VIEW_TOKEN_BUDGET,
    MODEL_NOTE_MARKER,
    render_context_view,
    render_recent_message_view,
    render_snapshot_for_debug,
)


def _state_with_content() -> AgentContextState:
    state = AgentContextState(owner_key="u", session_id="s")
    set_intent(state, current_question="redis 还在吗", current_goal="ping redis")
    state.working.plan = "ping → info → dbsize"
    state.working.completed_steps.append("ping")
    state.working.pending_steps.append("info")
    state.working.blocker = ""
    append_observed_fact(state, EvidenceItem(
        source="check_redis_health", status="success",
        facts=["ping ok"], raw_ref="timeline:1"))
    append_tool_summary(state, ToolSummary(
        tool="check_redis_health", status="success", latency_ms=42,
        note="ping ok", raw_ref="timeline:1"))
    append_evidence_gap(state, "missing latency baseline")
    llm_note(state, "evidence", "model_notes", op="append", append_value="可能因网络抖动")
    llm_note(state, "intent", "pending_hypotheses", op="append", append_value="触发限流")
    state.tool.do_not_repeat.append("ping_redis")
    return state


def test_render_includes_every_required_section() -> None:
    state = _state_with_content()
    view = render_context_view(state)
    for required in (
        "current_goal",
        "working_plan",
        "completed_steps",
        "pending_steps",
        "observed_facts",
        "tool_summaries",
        "evidence_gaps",
        "do_not_repeat",
    ):
        assert required in view, f"missing key: {required}"
    # The final user message is the sole source of truth for the full current
    # question; the default whiteboard view keeps only a distinct short goal.
    assert "current_question" not in view


def test_render_marks_model_notes_as_unverified() -> None:
    state = AgentContextState()
    llm_note(state, "evidence", "model_notes", op="append", append_value="未验证的想法")
    view = render_context_view(state)
    assert MODEL_NOTE_MARKER in view
    assert "未验证的想法" in view


def test_render_truncates_long_lists() -> None:
    state = AgentContextState()
    for i in range(20):
        llm_note(state, "evidence", "model_notes", op="append", append_value=f"n{i}")
    view = render_context_view(state, token_budget=8000)
    assert "n0" not in view  # oldest dropped
    assert "n19" in view
    assert "+" in view  # has an overflow marker


def test_render_enforces_token_budget() -> None:
    state = _state_with_content()
    long_lines = ["x" * 200 for _ in range(50)]
    for line in long_lines:
        llm_note(state, "evidence", "model_notes", op="append", append_value=line)
    full_view_len = len(render_context_view(state, token_budget=8000))
    view = render_context_view(state, token_budget=10)
    # Trimmed view should be at least 50% smaller than the full-budget render.
    assert "view truncated" in view
    assert len(view) < full_view_len // 2


def test_render_recent_message_view_returns_copy() -> None:
    state = _state_with_content()
    append_recent_turns(
        state,
        [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "ok"}],
    )
    msgs = render_recent_message_view(state, max_turns=4)
    assert msgs[-1]["text"] == "ok"
    msgs.append({"role": "user", "text": "tampered"})
    assert len(state.conversation.recent_turns) == 2  # original untouched


def test_recent_message_view_trims() -> None:
    state = _state_with_content()
    append_recent_turns(
        state,
        [{"role": "user", "text": f"t{i}"} for i in range(10)],
    )
    msgs = render_recent_message_view(state, max_turns=3)
    assert len(msgs) == 3
    assert msgs[0]["text"] == "t7"
    assert msgs[-1]["text"] == "t9"


def test_debug_snapshot_excludes_raw_tool_result() -> None:
    state = _state_with_content()
    snap = render_snapshot_for_debug(state)
    assert snap["schema_version"] == SCHEMA_VERSION
    assert "raw_tool_result" not in str(snap)
    assert snap["evidence"]["observed_facts"] == 1
    assert snap["working"]["plan"] == "ping → info → dbsize"


def test_empty_state_renders_cleanly() -> None:
    view = render_context_view(AgentContextState())
    # No fields populated → no required sections present in output.
    assert "current_goal" not in view  # intent.current_goal is empty by default
    assert "observed_facts" not in view
