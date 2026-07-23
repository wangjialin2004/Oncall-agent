"""Deterministic projection reducer for committed conversation turns.

The reducer is intentionally storage-agnostic. Runtime repair and operator
migration both call the same functions so a watermark is advanced only after
the corresponding committed turns have been applied to the projection.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.agent.context.state import AgentContextState, EvidenceItem, ToolSummary
from app.utils.serialization import json_loads


def _value(turn: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    try:
        value = turn[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json_loads(value, [])
        return parsed if isinstance(parsed, list) else []
    return []


def _event_ref(event: dict[str, Any], *, turn_id: int, position: int) -> str:
    explicit = str(
        event.get("span_id")
        or event.get("event_id")
        or (event.get("payload") or {}).get("tool_call_id")
        or ""
    ).strip()
    return explicit or f"turn:{turn_id}:event:{position}"


def _append_tool_event(
    state: AgentContextState,
    event: dict[str, Any],
    *,
    turn_id: int,
    position: int,
    recorded_at: str,
) -> None:
    if str(event.get("type") or "") != "tool_event":
        return
    tool = str(event.get("tool") or event.get("agent") or "tool").strip() or "tool"
    status = str(event.get("status") or "unknown").strip() or "unknown"
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    latency_ms = int(
        payload.get("tool_latency_ms") or payload.get("latency_ms") or event.get("duration_ms") or 0
    )
    raw_ref = _event_ref(event, turn_id=turn_id, position=position)
    note = str(event.get("summary") or payload.get("summary") or "")[:1500]

    summary_key = (tool, status, raw_ref)
    existing_summaries = {
        (item.tool, item.status, item.raw_ref) for item in state.evidence.tool_summaries
    }
    if summary_key not in existing_summaries:
        state.evidence.tool_summaries.append(
            ToolSummary(
                tool=tool,
                status=status,
                latency_ms=max(0, latency_ms),
                note=note,
                raw_ref=raw_ref,
                recorded_at=recorded_at,
            )
        )

    facts_raw = payload.get("facts")
    facts = (
        [str(item) for item in facts_raw if str(item).strip()]
        if isinstance(facts_raw, list)
        else []
    )
    explicit_fact = str(payload.get("fact") or "").strip()
    if explicit_fact:
        facts.append(explicit_fact)
    gaps_raw = payload.get("gaps")
    gaps = (
        [str(item) for item in gaps_raw if str(item).strip()] if isinstance(gaps_raw, list) else []
    )
    fact_key = (tool, status, raw_ref)
    existing_facts = {
        (item.source, item.status, item.raw_ref) for item in state.evidence.observed_facts
    }
    if (facts or gaps) and fact_key not in existing_facts:
        state.evidence.observed_facts.append(
            EvidenceItem(
                source=tool,
                status=status,
                latency_ms=max(0, latency_ms),
                facts=facts,
                gaps=gaps,
                raw_ref=raw_ref,
                recorded_at=recorded_at,
            )
        )

    state.tool.recent_calls.append(
        {
            "tool": tool,
            "status": status,
            "latency_ms": max(0, latency_ms),
            "raw_ref": raw_ref,
        }
    )
    state.tool.recent_calls = state.tool.recent_calls[-20:]
    state.tool.last_latency_ms = max(0, latency_ms)
    state.tool.last_status = status


def apply_committed_turns(
    state: AgentContextState,
    turns: Iterable[Mapping[str, Any] | Any],
) -> AgentContextState:
    """Apply committed turns in ascending index order to ``state``.

    Full user/assistant text remains canonical in ``conversation_turns``. The
    reducer keeps only compact intent/output/evidence and attachment indexes.
    Existing long-lived goals and model notes are preserved during incremental
    repair; a fresh rebuild seeds the goal from the first committed question.
    """

    ordered = sorted(turns, key=lambda item: int(_value(item, "turn_index", -1)))
    for turn in ordered:
        user_message = str(_value(turn, "user_message", "") or "").strip()
        assistant_answer = str(_value(turn, "assistant_answer", "") or "").strip()
        route = str(_value(turn, "route", "") or "").strip()
        case_id = str(_value(turn, "case_id", "") or "").strip()
        turn_id = int(_value(turn, "id", 0) or 0)
        turn_index = int(_value(turn, "turn_index", -1) or 0)
        created_at = str(_value(turn, "created_at", "") or "").strip()

        if user_message:
            state.intent.current_question = user_message
            if not state.intent.current_goal:
                state.intent.current_goal = user_message
        if assistant_answer:
            state.output.last_answer_summary = assistant_answer[:600]
        if route:
            state.identity.route = route
        if case_id:
            state.identity.case_id = case_id

        refs = _as_list(_value(turn, "attachment_refs", None))
        if not refs:
            refs = _as_list(_value(turn, "attachment_refs_json", None))
        by_file = {
            str(item.get("file_id")): dict(item)
            for item in state.conversation.active_attachment_refs
            if isinstance(item, dict) and item.get("file_id")
        }
        for raw_ref in refs:
            if not isinstance(raw_ref, dict) or not raw_ref.get("file_id"):
                continue
            ref = dict(raw_ref)
            ref.setdefault("source_turn_id", turn_id)
            ref.setdefault("source_turn_index", turn_index)
            by_file[str(ref["file_id"])] = ref
        state.conversation.active_attachment_refs = list(by_file.values())[-20:]

        events = _as_list(_value(turn, "events", None))
        if not events:
            events = _as_list(_value(turn, "events_json", None))
        for position, event in enumerate(events):
            if isinstance(event, dict):
                _append_tool_event(
                    state,
                    event,
                    turn_id=turn_id,
                    position=position,
                    recorded_at=str(
                        event.get("recorded_at")
                        or event.get("created_at")
                        or event.get("timestamp")
                        or created_at
                        or ""
                    ),
                )

        state.version += 1
        if created_at:
            state.updated_at = created_at

    state.patch_tail = []
    state.conversation.recent_turns = []
    state.conversation.last_turn_index = 0
    return state


def rebuild_projection_from_turns(
    *,
    owner_key: str,
    session_id: str,
    turns: Iterable[Mapping[str, Any] | Any],
) -> AgentContextState:
    state = AgentContextState(owner_key=owner_key, session_id=session_id)
    state.identity.owner_key = owner_key
    state.identity.session_id = session_id
    return apply_committed_turns(state, turns)


__all__ = ["apply_committed_turns", "rebuild_projection_from_turns"]
