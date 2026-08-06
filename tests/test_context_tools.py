"""Tests for ``app.agent.context.tools``."""

from __future__ import annotations

import pytest

from app.agent.context.operations import (
    ContextPatchError,
    framework_patch,
)
from app.agent.context.state import AgentContextState
from app.agent.context.tools import (
    CONTEXT_NOTE_NAME,
    CONTEXT_READ_NAME,
    READ_ATTACHMENT_NAME,
    build_runtime_tools,
    get_tool_definitions,
    list_advertised_tools,
    note_context,
    read_attachment,
    read_context,
    recent_messages,
)
from app.agent.context.tools_evidence import record_tool_result


def _state() -> AgentContextState:
    state = AgentContextState(owner_key="u", session_id="s")
    state.intent.current_goal = "ping redis"
    state.intent.current_question = "redis ok?"
    state.working.plan = "ping → info"
    state.evidence.model_notes.append("可能与昨天故障相关")
    return state


def test_read_context_summary_only_returns_view() -> None:
    state = _state()
    result = read_context(state)
    assert "view" in result
    assert "current_goal" in result["view"]
    assert "ping redis" in result["view"]


def test_read_context_section_returns_block_data() -> None:
    state = _state()
    result = read_context(state, section="intent")
    assert result["section"] == "intent"
    assert result["data"]["current_goal"] == "ping redis"


def test_read_context_identity_returns_error() -> None:
    state = _state()
    result = read_context(state, section="identity")
    assert "error" in result
    assert "framework-only" in result["error"]


def test_read_context_snapshot_mode() -> None:
    state = _state()
    result = read_context(state, summary_only=False)
    assert "snapshot" in result
    assert result["snapshot"]["schema_version"] == 1


def test_note_context_appends_to_model_notes() -> None:
    state = _state()
    result = note_context(
        state, section="evidence", field="model_notes",
        text="another guess",
    )
    assert result["ok"] is True
    assert state.evidence.model_notes == [
        "可能与昨天故障相关",
        "another guess",
    ]


def test_note_context_noop_for_empty_text() -> None:
    state = _state()
    result = note_context(
        state, section="evidence", field="model_notes", text="   ",
    )
    assert result["ok"] is True
    assert result["noop"] is True
    assert state.evidence.model_notes == ["可能与昨天故障相关"]


def test_note_context_rejects_observed_facts() -> None:
    state = _state()
    with pytest.raises(ContextPatchError):
        note_context(
            state, section="evidence", field="observed_facts", text="not allowed",
        )


def test_note_context_rejects_identity() -> None:
    state = _state()
    with pytest.raises(ContextPatchError):
        note_context(state, section="identity", field="case_id", text="x")


def test_note_context_can_edit_intent_hypotheses() -> None:
    state = _state()
    note_context(state, section="intent", field="pending_hypotheses", text="h1")
    note_context(state, section="intent", field="pending_hypotheses", text="h2")
    assert state.intent.pending_hypotheses == ["h1", "h2"]


def test_recent_messages_returns_copy() -> None:
    state = _state()
    state.conversation.recent_turns = [{"role": "user", "text": "hi"}]
    msgs = recent_messages(state)
    assert msgs == [{"role": "user", "text": "hi"}]
    msgs.append({"role": "user", "text": "tampered"})
    assert state.conversation.recent_turns == [{"role": "user", "text": "hi"}]


def test_get_tool_definitions_lists_only_safe_tools() -> None:
    defs = get_tool_definitions()
    names = {td["name"] for td in defs}
    assert names == {CONTEXT_READ_NAME, CONTEXT_NOTE_NAME, READ_ATTACHMENT_NAME}
    assert "context_patch" not in names
    assert "context_rollback" not in names


def test_list_advertised_tools_matches_definitions() -> None:
    assert sorted(list_advertised_tools()) == sorted(
        {CONTEXT_READ_NAME, CONTEXT_NOTE_NAME, READ_ATTACHMENT_NAME}
    )


def test_build_runtime_tools_omits_read_when_whiteboard_already_injected() -> None:
    state = _state()
    names = {tool.name for tool in build_runtime_tools(state, whiteboard_injected=True)}
    assert CONTEXT_READ_NAME not in names
    assert {CONTEXT_NOTE_NAME, READ_ATTACHMENT_NAME}.issubset(names)


def test_build_runtime_tools_includes_read_when_forced(monkeypatch) -> None:
    from app.config import config

    monkeypatch.setattr(config, "harness_context_tools_enabled", True)
    monkeypatch.setattr(config, "harness_context_read_when_view_injected", True)
    state = _state()
    names = {tool.name for tool in build_runtime_tools(state, whiteboard_injected=True)}
    assert CONTEXT_READ_NAME in names
    assert CONTEXT_NOTE_NAME in names


def test_build_runtime_tools_includes_read_when_view_not_injected() -> None:
    state = _state()
    names = {tool.name for tool in build_runtime_tools(state, whiteboard_injected=False)}
    assert {CONTEXT_READ_NAME, CONTEXT_NOTE_NAME, READ_ATTACHMENT_NAME} == names


def test_tool_definitions_have_required_fields() -> None:
    defs = {td["name"]: td for td in get_tool_definitions()}
    note_def = defs[CONTEXT_NOTE_NAME]
    assert "required" in note_def["parameters"]
    assert set(note_def["parameters"]["required"]) == {"section", "field", "text"}
    assert note_def["parameters"]["additionalProperties"] is False


def test_framework_can_record_evidence_via_helpers() -> None:
    """Sanity: a tool call ends up appending structured evidence rows."""
    state = _state()
    record_tool_result(
        state,
        tool_name="check_redis_health",
        success=True,
        latency_ms=42,
        raw_ref="timeline:1",
        content_summary="ping ok",
        fact="Redis PING 成功",
    )
    # LLM-side: ask to write a hypothesis. Allowed.
    note_context(
        state, section="intent", field="pending_hypotheses",
        text="基于 ping 成功推断 Redis 健康",
    )
    # LLM cannot replace observed_facts with its own narrative.
    with pytest.raises(ContextPatchError):
        note_context(
            state, section="evidence", field="observed_facts",
            text="Redis 不健康",  # would be misleading if accepted
        )
    assert state.intent.pending_hypotheses == ["基于 ping 成功推断 Redis 健康"]

@pytest.mark.asyncio
async def test_read_attachment_returns_summary_for_active_ref() -> None:
    state = _state()
    state.conversation.active_attachment_refs = [
        {
            "file_id": "file_1",
            "file_name": "runbook.md",
            "summary": "CPU runbook",
            "keywords": ["cpu", "runbook"],
            "status": "indexed",
        }
    ]

    result = await read_attachment(state, file_id="file_1", mode="summary")

    assert result["ok"] is True
    assert result["file_name"] == "runbook.md"
    assert result["summary"] == "CPU runbook"


@pytest.mark.asyncio
async def test_read_attachment_rejects_inactive_file() -> None:
    state = _state()

    result = await read_attachment(state, file_id="file_missing", mode="summary")

    assert result["ok"] is False
    assert "not active" in result["error"]


@pytest.mark.asyncio
async def test_read_attachment_loads_content_for_active_ref(monkeypatch) -> None:
    class _AttachmentService:
        async def build_context(self, owner_key: str, attachment_ids: list[str]) -> str:
            assert owner_key == "u"
            assert attachment_ids == ["file_1"]
            return "attachment body"

    state = _state()
    state.conversation.active_attachment_refs = [
        {"file_id": "file_1", "file_name": "runbook.md", "summary": "CPU"}
    ]
    monkeypatch.setattr(
        "app.services.attachment_context_service.attachment_context_service",
        _AttachmentService(),
    )

    result = await read_attachment(state, file_id="file_1", mode="content")

    assert result["ok"] is True
    assert "UNTRUSTED_ATTACHMENT_CONTEXT" in result["content"]
    assert "attachment body" in result["content"]
