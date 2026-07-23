"""Dataclasses for the AgentContextState whiteboard.

The state is split into seven sections, each with a single owner (framework vs
LLM-writable). The split mirrors plan
``plan/2026-07-08-stateful-agent-context.md`` §3.2.

Mutating fields outside of :mod:`app.agent.context.operations` is unsupported
by design — callers should use the controlled patch API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Single source of truth for the schema version. Bump only when the on-disk /
# on-wire shape changes in a non-backward-compatible way.
SCHEMA_VERSION = 1


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass(slots=True)
class IdentityBlock:
    """Framework-only identity.

    ``owner_key`` / ``session_id`` are duplicated on the parent state for cheap
    access but kept here so the section view is self-contained when persisted
    in DB snapshots or Redis hash sub-fields.
    """

    owner_key: str = ""
    session_id: str = ""
    case_id: str = ""
    route: str = "harness"


@dataclass(slots=True)
class IntentBlock:
    """Framework writes current_question / current_goal; LLM may append user_corrections."""

    current_question: str = ""
    current_goal: str = ""
    pending_hypotheses: list[str] = field(default_factory=list)
    user_corrections: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkingBlock:
    """Working plan + step bookkeeping.

    Framework owns ``completed_steps`` / ``pending_steps``; the LLM may set
    ``plan`` text but only via the controlled :func:`llm_note` patch.
    """

    plan: str = ""
    completed_steps: list[str] = field(default_factory=list)
    pending_steps: list[str] = field(default_factory=list)
    blocker: str = ""


@dataclass(slots=True)
class EvidenceItem:
    """Structured fact observed by the framework via tool execution."""

    source: str  # tool name
    status: str  # success | failure | timeout
    latency_ms: int = 0
    facts: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    raw_ref: str = ""  # e.g. timeline:event-id or step_ref
    recorded_at: str = field(default_factory=_utcnow_iso)


@dataclass(slots=True)
class ToolSummary:
    """LLM-visible tool summary, kept short on purpose."""

    tool: str
    status: str
    latency_ms: int = 0
    note: str = ""
    raw_ref: str = ""
    recorded_at: str = field(default_factory=_utcnow_iso)


@dataclass(slots=True)
class EvidenceState:
    """Evidence block: split into fact-only vs model-note fields.

    Framework writes ``observed_facts`` / ``tool_summaries``. LLM writes
    ``model_notes`` and ``cannot_conclude_reasons`` via :func:`llm_note`.
    """

    observed_facts: list[EvidenceItem] = field(default_factory=list)
    tool_summaries: list[ToolSummary] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    cannot_conclude_reasons: list[str] = field(default_factory=list)
    model_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConversationBlock:
    """Compact conversation snapshot used in the rendered view.

    This is a view of the most recent turns only — the canonical history still
    lives in the conversation DB; the field exists so the renderer doesn't have
    to round-trip through Redis/DB each request.
    """

    recent_turns: list[dict[str, Any]] = field(default_factory=list)
    active_attachment_refs: list[dict[str, Any]] = field(default_factory=list)
    cold_start_summary: str = ""
    last_turn_index: int = 0


@dataclass(slots=True)
class ToolBlock:
    """Tool usage bookkeeping — framework-only.

    ``do_not_repeat`` is consulted when re-running tools; ``recent_calls`` is
    kept short and never carries raw payload bytes.
    """

    recent_calls: list[dict[str, Any]] = field(default_factory=list)
    do_not_repeat: list[str] = field(default_factory=list)
    last_latency_ms: int = 0
    last_status: str = ""


@dataclass(slots=True)
class OutputBlock:
    """Output contract controls.

    ``answer_contract`` and ``low_confidence_rule`` are framework defaults;
    the LLM may refine them.
    """

    answer_contract: str = ""
    required_evidence: list[str] = field(default_factory=list)
    low_confidence_rule: str = ""
    last_answer_summary: str = ""


# Section name constants used by operations.py / persistence.py / views.py.
SECTION_IDENTITY = "identity"
SECTION_INTENT = "intent"
SECTION_WORKING = "working"
SECTION_EVIDENCE = "evidence"
SECTION_CONVERSATION = "conversation"
SECTION_TOOL = "tool"
SECTION_OUTPUT = "output"

# Mapping used by :func:`AgentContextState.section` to dispatch by name. Kept
# alongside the dataclasses so persistence code can rely on it.
SECTION_NAMES: tuple[str, ...] = (
    SECTION_IDENTITY,
    SECTION_INTENT,
    SECTION_WORKING,
    SECTION_EVIDENCE,
    SECTION_CONVERSATION,
    SECTION_TOOL,
    SECTION_OUTPUT,
)


@dataclass(slots=True)
class AgentContextState:
    """The full whiteboard — versioned and patchable.

    ``version`` increments on every controlled patch. ``patch_tail`` is a
    bounded audit log (oldest entries drop when the cap is exceeded); it is the
    only inspection affordance for debugging sequence of ownership.
    """

    owner_key: str = ""
    session_id: str = ""
    schema_version: int = SCHEMA_VERSION
    version: int = 0
    updated_at: str = field(default_factory=_utcnow_iso)
    identity: IdentityBlock = field(default_factory=IdentityBlock)
    intent: IntentBlock = field(default_factory=IntentBlock)
    working: WorkingBlock = field(default_factory=WorkingBlock)
    evidence: EvidenceState = field(default_factory=EvidenceState)
    conversation: ConversationBlock = field(default_factory=ConversationBlock)
    tool: ToolBlock = field(default_factory=ToolBlock)
    output: OutputBlock = field(default_factory=OutputBlock)
    patch_tail: list[dict[str, Any]] = field(default_factory=list)
    # Runtime-only unified repository metadata. Persistence serializers omit
    # these fields; they only bind inflight recovery to the loaded projection.
    _runtime_base_projection_version: int = field(default=0, repr=False)
    _runtime_run_id: str = field(default="", repr=False)
    _runtime_rehydrated_run_id: str = field(default="", repr=False)

    # ---- access by section name (used by operations / persistence) ----

    def section(self, name: str) -> Any:
        """Return the block dataclass for a section name.

        Raises :class:`KeyError` for unknown names. Persistence code uses this
        single dispatch instead of branching on ``isinstance`` everywhere.
        """
        if name == SECTION_IDENTITY:
            return self.identity
        if name == SECTION_INTENT:
            return self.intent
        if name == SECTION_WORKING:
            return self.working
        if name == SECTION_EVIDENCE:
            return self.evidence
        if name == SECTION_CONVERSATION:
            return self.conversation
        if name == SECTION_TOOL:
            return self.tool
        if name == SECTION_OUTPUT:
            return self.output
        raise KeyError(f"unknown context section: {name!r}")

    def touch(self, at: str | None = None) -> None:
        """Refresh ``updated_at`` (callers usually don't need this — patch API does it)."""
        self.updated_at = at or _utcnow_iso()

    def record_patch(self, entry: dict[str, Any], history_limit: int) -> None:
        """Append a patch audit entry, dropping oldest when over the cap.

        ``history_limit <= 0`` disables the audit tail.
        """
        if history_limit <= 0:
            return
        self.patch_tail.append(entry)
        if len(self.patch_tail) > history_limit:
            # Drop from the front; keeps the most recent frames.
            del self.patch_tail[: len(self.patch_tail) - history_limit]
