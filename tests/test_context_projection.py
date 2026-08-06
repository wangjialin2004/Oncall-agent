"""Tests for compact projection serializers and envelope render policies."""

from __future__ import annotations

import ast
from pathlib import Path

from app.agent.context.envelope import ContextEnvelope, ContextScope, ContextWatermark
from app.agent.context.operations import append_recent_turns, set_intent
from app.agent.context.projection import (
    PROJECTION_SCHEMA_V2,
    is_compact_projection,
    projection_from_dict,
    projection_to_dict,
)
from app.agent.context.reducer import rebuild_projection_from_turns
from app.agent.context.renderers import LegacyRenderPolicy, StructuredRenderPolicy
from app.agent.context.state import AgentContextState


def _state() -> AgentContextState:
    state = AgentContextState(owner_key="o1", session_id="s1")
    set_intent(state, current_question="q", current_goal="g")
    append_recent_turns(
        state,
        [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "hi"}],
        last_turn_index=2,
    )
    state.identity.owner_key = "o1"
    state.identity.session_id = "s1"
    state.identity.case_id = "case-1"
    state.patch_tail.append({"op": "set", "field": "x", "value": "y"})
    state.conversation.active_attachment_refs = [
        {"file_id": "f1", "file_name": "a.txt", "summary": "sum", "extra": "drop-me"}
    ]
    return state


def test_projection_v2_excludes_recent_turns_identity_scope_and_patch_tail() -> None:
    payload = projection_to_dict(_state())
    assert payload["schema_version"] == PROJECTION_SCHEMA_V2
    assert payload["conversation"]["recent_turns"] == []
    assert payload["patch_tail"] == []
    assert "owner_key" not in payload["identity"]
    assert "session_id" not in payload["identity"]
    assert "owner_key" not in payload
    assert "session_id" not in payload
    assert payload["identity"]["case_id"] == "case-1"
    assert payload["conversation"]["active_attachment_refs"][0]["file_id"] == "f1"
    assert "extra" not in payload["conversation"]["active_attachment_refs"][0]
    assert is_compact_projection(payload)


def test_projection_v1_and_v2_readable_into_runtime_state() -> None:
    state = _state()
    v2 = projection_to_dict(state)
    loaded = projection_from_dict(v2, owner_key="o1", session_id="s1")
    assert loaded.owner_key == "o1"
    assert loaded.session_id == "s1"
    assert loaded.intent.current_goal == "g"
    assert loaded.conversation.recent_turns == []
    assert loaded.identity.case_id == "case-1"

    # v1 compatibility: full state_to_dict shape still loads.
    from app.agent.context.persistence import state_to_dict

    v1 = state_to_dict(state)
    loaded_v1 = projection_from_dict(v1, owner_key="o1", session_id="s1")
    assert loaded_v1.conversation.recent_turns
    assert loaded_v1.intent.current_question == "q"


def test_renderers_consume_envelope_only() -> None:
    state = _state()
    envelope = ContextEnvelope(
        scope=ContextScope(owner_key="o1", session_id="s1"),
        watermark=ContextWatermark(last_applied_turn_index=1, projection_status="ready"),
        projection=state,
        turn_window=[
            {"turn_index": 0, "user_message": "hello", "assistant_answer": "hi"},
        ],
        rolling_summary="old summary",
        source="sqlite_projection",
    )
    structured = StructuredRenderPolicy().render(envelope)
    legacy = LegacyRenderPolicy().render(envelope)
    assert "当前会话白板" in structured.system_prompt or structured.view
    assert structured.history_messages
    assert legacy.history_messages
    assert "历史摘要" in legacy.system_prompt


def test_renderers_module_does_not_import_storage_services() -> None:
    path = Path("app/agent/context/renderers.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = {
        "app.services.conversation_service",
        "app.services.redis_client",
        "app.services.context_repository",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    assert banned.isdisjoint(imported)


def test_turn_reducer_is_deterministic_and_keeps_only_explicit_facts() -> None:
    turns = [
        {
            "id": 7,
            "turn_index": 0,
            "user_message": "check redis",
            "assistant_answer": "redis is healthy",
            "route": "diagnosis",
            "case_id": "case-7",
            "attachment_refs_json": "[]",
            "events_json": (
                '[{"type":"tool_event","tool":"check_redis_health",'
                '"status":"completed","summary":"ping ok",'
                '"payload":{"fact":"Redis PING succeeded","latency_ms":7}}]'
            ),
            "created_at": "2026-07-19T00:00:00Z",
        }
    ]
    first = projection_to_dict(
        rebuild_projection_from_turns(owner_key="o", session_id="s", turns=turns)
    )
    second = projection_to_dict(
        rebuild_projection_from_turns(owner_key="o", session_id="s", turns=turns)
    )
    assert first == second
    assert first["evidence"]["observed_facts"][0]["facts"] == ["Redis PING succeeded"]
    assert first["output"]["last_answer_summary"] == "redis is healthy"
