"""Controlled patch API for :class:`AgentContextState`.

Two entry points:

- :func:`framework_patch` — single owner of ``observed_facts`` / ``tool_summaries``
  / ``identity`` / ``working.completed_steps`` etc. Internal callers (Harness
  framework, evidence writers) use this.
- :func:`llm_note` — restricted write for LLM-initiated notes. Can only touch
  ``model_notes``, ``pending_hypotheses``, ``user_corrections``, ``working.plan``
  (and a few others). Attempting to write ``observed_facts`` or ``identity``
  raises :class:`ContextPatchError`.

Mutating the dataclass directly (e.g. ``state.evidence.observed_facts.append``)
is unsupported and breaks the audit trail + version bump — operations ALWAYS go
through here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.agent.context.state import (
    AgentContextState,
    ConversationBlock,
    EvidenceItem,
    EvidenceState,
    IdentityBlock,
    IntentBlock,
    OutputBlock,
    SECTION_CONVERSATION,
    SECTION_EVIDENCE,
    SECTION_IDENTITY,
    SECTION_INTENT,
    SECTION_OUTPUT,
    SECTION_TOOL,
    SECTION_WORKING,
    ToolBlock,
    ToolSummary,
    WorkingBlock,
)


class ContextPatchError(PermissionError):
    """Raised when a patch target is not allowed for the writer."""


# ---------------------------------------------------------------------------
# Field allowlists
# ---------------------------------------------------------------------------

#: Fields only the framework may write. The LLM is blocked from these.
FRAMEWORK_ONLY_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        (SECTION_IDENTITY, "owner_key"),
        (SECTION_IDENTITY, "session_id"),
        (SECTION_IDENTITY, "case_id"),
        (SECTION_IDENTITY, "route"),
        (SECTION_EVIDENCE, "observed_facts"),
        (SECTION_EVIDENCE, "tool_summaries"),
        (SECTION_EVIDENCE, "evidence_gaps"),
        (SECTION_CONVERSATION, "recent_turns"),
        (SECTION_CONVERSATION, "active_attachment_refs"),
        (SECTION_CONVERSATION, "last_turn_index"),
        (SECTION_TOOL, "recent_calls"),
        (SECTION_TOOL, "do_not_repeat"),
        (SECTION_TOOL, "last_latency_ms"),
        (SECTION_TOOL, "last_status"),
        (SECTION_OUTPUT, "last_answer_summary"),
        (SECTION_WORKING, "completed_steps"),
        (SECTION_WORKING, "pending_steps"),
        (SECTION_WORKING, "blocker"),
        (SECTION_INTENT, "current_question"),
        (SECTION_INTENT, "current_goal"),
    }
)

#: Fields the LLM MAY write via ``llm_note``. Framework writes are also allowed.
LLM_WRITABLE_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        (SECTION_EVIDENCE, "model_notes"),
        (SECTION_EVIDENCE, "cannot_conclude_reasons"),
        (SECTION_INTENT, "pending_hypotheses"),
        (SECTION_INTENT, "user_corrections"),
        (SECTION_WORKING, "plan"),
        (SECTION_OUTPUT, "answer_contract"),
        (SECTION_OUTPUT, "required_evidence"),
        (SECTION_OUTPUT, "low_confidence_rule"),
    }
)


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _validate_section(section: str) -> None:
    if section not in {SECTION_IDENTITY, SECTION_INTENT, SECTION_WORKING,
                       SECTION_EVIDENCE, SECTION_CONVERSATION, SECTION_TOOL,
                       SECTION_OUTPUT}:
        raise ContextPatchError(f"unknown section: {section!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PatchResult:
    """Outcome of a patch — used by callers (store / audit) to confirm writes."""

    section: str
    field: str
    op: str
    version_after: int
    touched_at: str = field(default_factory=_utcnow)


def framework_patch(
    state: AgentContextState,
    section: str,
    field_name: str,
    *,
    op: str,
    value: Any = None,
    append_value: Any = None,
    merge_value: Any = None,
    history_limit: int = 200,
) -> PatchResult:
    """Apply a framework-owned patch to ``state``.

    Supported ``op``:

    - ``"set"`` — replace the field value with ``value`` (whole-field replace).
    - ``"append"`` — append ``append_value`` to a list field.
    - ``"merge"`` — shallow merge ``merge_value`` into a dict field.
    - ``"delete"`` — delete a key from a dict field via ``value`` (the key).
    """
    _validate_section(section)
    block = state.section(section)

    if (section, field_name) not in FRAMEWORK_ONLY_FIELDS | LLM_WRITABLE_FIELDS:
        raise ContextPatchError(f"field not on patch allowlist: {section}.{field_name}")

    _apply_op(block, field_name, op=op, value=value,
              append_value=append_value, merge_value=merge_value)

    state.version += 1
    state.touch()
    state.record_patch(
        {
            "writer": "framework",
            "section": section,
            "field": field_name,
            "op": op,
            "version": state.version,
            "at": state.updated_at,
        },
        history_limit=history_limit,
    )
    return PatchResult(section=section, field=field_name, op=op,
                       version_after=state.version)


def llm_note(
    state: AgentContextState,
    section: str,
    field_name: str,
    *,
    op: str,
    value: Any = None,
    append_value: Any = None,
    merge_value: Any = None,
    history_limit: int = 200,
) -> PatchResult:
    """Apply a restricted LLM note to ``state``.

    Same ops as :func:`framework_patch`; the field allowlist is narrower. The
    LLM must never write evidence facts — that's reserved for the framework.
    """
    _validate_section(section)
    if (section, field_name) not in LLM_WRITABLE_FIELDS:
        raise ContextPatchError(
            f"LLM cannot write {section}.{field_name} "
            "(reserved for framework or out of scope)"
        )

    block = state.section(section)
    _apply_op(block, field_name, op=op, value=value,
              append_value=append_value, merge_value=merge_value)

    state.version += 1
    state.touch()
    state.record_patch(
        {
            "writer": "llm",
            "section": section,
            "field": field_name,
            "op": op,
            "version": state.version,
            "at": state.updated_at,
        },
        history_limit=history_limit,
    )
    return PatchResult(section=section, field=field_name, op=op,
                       version_after=state.version)


# ---------------------------------------------------------------------------
# Bulk helpers used by harness integration
# ---------------------------------------------------------------------------


def append_observed_fact(
    state: AgentContextState,
    item: EvidenceItem,
    *,
    history_limit: int = 200,
) -> PatchResult:
    """Convenience: framework append to ``evidence.observed_facts``."""
    return framework_patch(
        state,
        SECTION_EVIDENCE,
        "observed_facts",
        op="append",
        append_value=item,
        history_limit=history_limit,
    )


def append_tool_summary(
    state: AgentContextState,
    summary: ToolSummary,
    *,
    history_limit: int = 200,
) -> PatchResult:
    """Convenience: framework append to ``evidence.tool_summaries``."""
    return framework_patch(
        state,
        SECTION_EVIDENCE,
        "tool_summaries",
        op="append",
        append_value=summary,
        history_limit=history_limit,
    )


def append_evidence_gap(
    state: AgentContextState,
    gap: str,
    *,
    history_limit: int = 200,
) -> PatchResult:
    """Convenience: framework append to ``evidence.evidence_gaps``."""
    return framework_patch(
        state,
        SECTION_EVIDENCE,
        "evidence_gaps",
        op="append",
        append_value=gap,
        history_limit=history_limit,
    )


def set_intent(
    state: AgentContextState,
    current_question: str | None = None,
    current_goal: str | None = None,
    *,
    history_limit: int = 200,
) -> list[PatchResult]:
    """Bulk-update intent fields owned by framework."""
    results: list[PatchResult] = []
    if current_question is not None:
        results.append(
            framework_patch(
                state, SECTION_INTENT, "current_question",
                op="set", value=current_question, history_limit=history_limit,
            )
        )
    if current_goal is not None:
        results.append(
            framework_patch(
                state, SECTION_INTENT, "current_goal",
                op="set", value=current_goal, history_limit=history_limit,
            )
        )
    return results


def append_recent_turns(
    state: AgentContextState,
    turns: Iterable[dict[str, Any]],
    *,
    last_turn_index: int | None = None,
    history_limit: int = 200,
) -> PatchResult:
    """Replace ``conversation.recent_turns`` with the newest snapshot."""
    snapshot = list(turns)
    state.conversation.recent_turns = snapshot
    if last_turn_index is not None:
        state.conversation.last_turn_index = last_turn_index
    state.version += 1
    state.touch()
    state.record_patch(
        {
            "writer": "framework",
            "section": SECTION_CONVERSATION,
            "field": "recent_turns",
            "op": "snapshot",
            "count": len(snapshot),
            "version": state.version,
            "at": state.updated_at,
        },
        history_limit=history_limit,
    )
    return PatchResult(section=SECTION_CONVERSATION, field="recent_turns",
                       op="snapshot", version_after=state.version)


def merge_active_attachment_refs(
    state: AgentContextState,
    refs: Iterable[dict[str, Any]],
    *,
    history_limit: int = 200,
) -> PatchResult:
    """Merge current-turn attachment refs into the conversation attachment index."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in [*state.conversation.active_attachment_refs, *list(refs)]:
        if not isinstance(raw, dict):
            continue
        file_id = str(raw.get("file_id") or "").strip()
        if not file_id:
            continue
        if file_id not in merged:
            order.append(file_id)
        merged[file_id] = dict(raw)
    snapshot = [merged[file_id] for file_id in order]
    return framework_patch(
        state,
        SECTION_CONVERSATION,
        "active_attachment_refs",
        op="set",
        value=snapshot,
        history_limit=history_limit,
    )


# ---------------------------------------------------------------------------
# Internal op dispatch
# ---------------------------------------------------------------------------


def _apply_op(
    block: Any,
    field_name: str,
    *,
    op: str,
    value: Any,
    append_value: Any,
    merge_value: Any,
) -> None:
    """Dispatch a single op against a block's field.

    Raises :class:`ContextPatchError` for unknown fields/ops so callers can
    surface the issue to the audit trail rather than silently no-op.
    """
    if not hasattr(block, field_name):
        raise ContextPatchError(f"unknown field: {field_name}")

    if op == "set":
        setattr(block, field_name, value)
        return
    if op == "append":
        target = getattr(block, field_name)
        if not isinstance(target, list):
            raise ContextPatchError(f"{field_name} is not a list field")
        if append_value is None:
            raise ContextPatchError("append op requires append_value")
        target.append(append_value)
        return
    if op == "merge":
        target = getattr(block, field_name)
        if not isinstance(target, dict):
            raise ContextPatchError(f"{field_name} is not a dict field")
        if merge_value is None:
            raise ContextPatchError("merge op requires merge_value")
        target.update(merge_value)
        return
    if op == "delete":
        target = getattr(block, field_name)
        if not isinstance(target, dict):
            raise ContextPatchError(f"{field_name} is not a dict field")
        target.pop(value, None)
        return

    raise ContextPatchError(f"unknown op: {op!r}")


__all__ = [
    "ContextPatchError",
    "PatchResult",
    "framework_patch",
    "llm_note",
    "append_observed_fact",
    "append_tool_summary",
    "append_evidence_gap",
    "set_intent",
    "append_recent_turns",
    "merge_active_attachment_refs",
    "FRAMEWORK_ONLY_FIELDS",
    "LLM_WRITABLE_FIELDS",
    "IdentityBlock",
    "IntentBlock",
    "WorkingBlock",
    "EvidenceState",
    "EvidenceItem",
    "ToolSummary",
    "ConversationBlock",
    "ToolBlock",
    "OutputBlock",
]
