"""Serialize / deserialize :class:`AgentContextState` to JSON-friendly dicts.

This is the boundary between in-memory dataclasses and either Redis or the DB
snapshot row. It is deliberately narrow: no I/O, no schema migrations beyond
the version constant.

A schema mismatch surfaces as :class:`ContextSchemaError` from
:func:`state_from_dict` — callers should treat it as "snapshot unavailable"
and rebuild from conversation turns (see plan §5.3).
"""

from __future__ import annotations

from typing import Any

from app.agent.context.state import (
    SCHEMA_VERSION,
    AgentContextState,
    ConversationBlock,
    EvidenceItem,
    EvidenceState,
    IdentityBlock,
    IntentBlock,
    OutputBlock,
    ToolBlock,
    ToolSummary,
    WorkingBlock,
)


class ContextSchemaError(ValueError):
    """Raised when a serialized snapshot cannot be loaded by this code."""


def state_to_dict(state: AgentContextState) -> dict[str, Any]:
    """JSON-serializable dict for the whole whiteboard."""
    return {
        "schema_version": state.schema_version,
        "version": state.version,
        "owner_key": state.owner_key,
        "session_id": state.session_id,
        "updated_at": state.updated_at,
        "identity": _identity_to_dict(state.identity),
        "intent": _intent_to_dict(state.intent),
        "working": _working_to_dict(state.working),
        "evidence": _evidence_to_dict(state.evidence),
        "conversation": _conversation_to_dict(state.conversation),
        "tool": _tool_to_dict(state.tool),
        "output": _output_to_dict(state.output),
        "patch_tail": list(state.patch_tail),
    }


def state_from_dict(payload: dict[str, Any]) -> AgentContextState:
    """Hydrate a state from a serialized dict.

    Raises :class:`ContextSchemaError` on schema_version mismatch so callers
    can treat it as "unavailable" and rebuild.
    """
    if not isinstance(payload, dict):
        raise ContextSchemaError("payload is not a dict")
    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ContextSchemaError(
            f"snapshot schema_version={schema_version} "
            f"!= runtime schema_version={SCHEMA_VERSION}"
        )

    state = AgentContextState(
        owner_key=str(payload.get("owner_key", "")),
        session_id=str(payload.get("session_id", "")),
        schema_version=SCHEMA_VERSION,
        version=int(payload.get("version", 0)),
        updated_at=str(payload.get("updated_at", "")),
    )
    state.identity = _identity_from_dict(payload.get("identity") or {})
    state.intent = _intent_from_dict(payload.get("intent") or {})
    state.working = _working_from_dict(payload.get("working") or {})
    state.evidence = _evidence_from_dict(payload.get("evidence") or {})
    state.conversation = _conversation_from_dict(payload.get("conversation") or {})
    state.tool = _tool_from_dict(payload.get("tool") or {})
    state.output = _output_from_dict(payload.get("output") or {})
    state.patch_tail = list(payload.get("patch_tail") or [])
    return state


# ---- section serializers ----


def _identity_to_dict(b: IdentityBlock) -> dict[str, Any]:
    return {
        "owner_key": b.owner_key,
        "session_id": b.session_id,
        "case_id": b.case_id,
        "route": b.route,
    }


def _identity_from_dict(d: dict[str, Any]) -> IdentityBlock:
    return IdentityBlock(
        owner_key=str(d.get("owner_key", "")),
        session_id=str(d.get("session_id", "")),
        case_id=str(d.get("case_id", "")),
        route=str(d.get("route", "harness")),
    )


def _intent_to_dict(b: IntentBlock) -> dict[str, Any]:
    return {
        "current_question": b.current_question,
        "current_goal": b.current_goal,
        "pending_hypotheses": list(b.pending_hypotheses),
        "user_corrections": list(b.user_corrections),
    }


def _intent_from_dict(d: dict[str, Any]) -> IntentBlock:
    return IntentBlock(
        current_question=str(d.get("current_question", "")),
        current_goal=str(d.get("current_goal", "")),
        pending_hypotheses=list(d.get("pending_hypotheses") or []),
        user_corrections=list(d.get("user_corrections") or []),
    )


def _working_to_dict(b: WorkingBlock) -> dict[str, Any]:
    return {
        "plan": b.plan,
        "completed_steps": list(b.completed_steps),
        "pending_steps": list(b.pending_steps),
        "blocker": b.blocker,
    }


def _working_from_dict(d: dict[str, Any]) -> WorkingBlock:
    return WorkingBlock(
        plan=str(d.get("plan", "")),
        completed_steps=list(d.get("completed_steps") or []),
        pending_steps=list(d.get("pending_steps") or []),
        blocker=str(d.get("blocker", "")),
    )


def _evidence_to_dict(b: EvidenceState) -> dict[str, Any]:
    return {
        "observed_facts": [
            {
                "source": i.source,
                "status": i.status,
                "latency_ms": i.latency_ms,
                "facts": list(i.facts),
                "gaps": list(i.gaps),
                "raw_ref": i.raw_ref,
                "recorded_at": i.recorded_at,
            }
            for i in b.observed_facts
        ],
        "tool_summaries": [
            {
                "tool": s.tool,
                "status": s.status,
                "latency_ms": s.latency_ms,
                "note": s.note,
                "raw_ref": s.raw_ref,
                "recorded_at": s.recorded_at,
            }
            for s in b.tool_summaries
        ],
        "evidence_gaps": list(b.evidence_gaps),
        "cannot_conclude_reasons": list(b.cannot_conclude_reasons),
        "model_notes": list(b.model_notes),
    }


def _evidence_from_dict(d: dict[str, Any]) -> EvidenceState:
    return EvidenceState(
        observed_facts=[EvidenceItem(**item) for item in (d.get("observed_facts") or [])],
        tool_summaries=[ToolSummary(**item) for item in (d.get("tool_summaries") or [])],
        evidence_gaps=list(d.get("evidence_gaps") or []),
        cannot_conclude_reasons=list(d.get("cannot_conclude_reasons") or []),
        model_notes=list(d.get("model_notes") or []),
    )


def _conversation_to_dict(b: ConversationBlock) -> dict[str, Any]:
    return {
        "recent_turns": list(b.recent_turns),
        "active_attachment_refs": list(b.active_attachment_refs),
        "cold_start_summary": b.cold_start_summary,
        "last_turn_index": b.last_turn_index,
    }


def _conversation_from_dict(d: dict[str, Any]) -> ConversationBlock:
    return ConversationBlock(
        recent_turns=list(d.get("recent_turns") or []),
        active_attachment_refs=list(d.get("active_attachment_refs") or []),
        cold_start_summary=str(d.get("cold_start_summary", "")),
        last_turn_index=int(d.get("last_turn_index", 0)),
    )


def _tool_to_dict(b: ToolBlock) -> dict[str, Any]:
    return {
        "recent_calls": list(b.recent_calls),
        "do_not_repeat": list(b.do_not_repeat),
        "last_latency_ms": b.last_latency_ms,
        "last_status": b.last_status,
    }


def _tool_from_dict(d: dict[str, Any]) -> ToolBlock:
    return ToolBlock(
        recent_calls=list(d.get("recent_calls") or []),
        do_not_repeat=list(d.get("do_not_repeat") or []),
        last_latency_ms=int(d.get("last_latency_ms", 0)),
        last_status=str(d.get("last_status", "")),
    )


def _output_to_dict(b: OutputBlock) -> dict[str, Any]:
    return {
        "answer_contract": b.answer_contract,
        "required_evidence": list(b.required_evidence),
        "low_confidence_rule": b.low_confidence_rule,
        "last_answer_summary": b.last_answer_summary,
    }


def _output_from_dict(d: dict[str, Any]) -> OutputBlock:
    return OutputBlock(
        answer_contract=str(d.get("answer_contract", "")),
        required_evidence=list(d.get("required_evidence") or []),
        low_confidence_rule=str(d.get("low_confidence_rule", "")),
        last_answer_summary=str(d.get("last_answer_summary", "")),
    )


__all__ = [
    "ContextSchemaError",
    "state_to_dict",
    "state_from_dict",
]
