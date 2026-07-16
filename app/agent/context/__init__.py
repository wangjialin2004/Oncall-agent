"""Stateful Agent context whiteboard.

The :mod:`app.agent.context` package is the conversation whiteboard backing the
stateful harness. The single source of truth for in-flight context lives in
:class:`AgentContextState`; Redis is the hot store and the DB snapshot is the
cold backup. ``memory cache`` and the old rolling-summary/token-window path are
explicitly NOT part of this pipeline — see plan
``plan/2026-07-08-stateful-agent-context.md`` §2.2 / §2.5 / §10.
"""

from app.agent.context.persistence import (
    ContextSchemaError,
    state_from_dict,
    state_to_dict,
)
from app.agent.context.state import (
    SCHEMA_VERSION,
    SECTION_CONVERSATION,
    SECTION_EVIDENCE,
    SECTION_IDENTITY,
    SECTION_INTENT,
    SECTION_NAMES,
    SECTION_OUTPUT,
    SECTION_TOOL,
    SECTION_WORKING,
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

__all__ = [
    "SCHEMA_VERSION",
    "AgentContextState",
    "IdentityBlock",
    "IntentBlock",
    "WorkingBlock",
    "EvidenceItem",
    "EvidenceState",
    "ToolSummary",
    "ConversationBlock",
    "ToolBlock",
    "OutputBlock",
    "ContextSchemaError",
    "state_from_dict",
    "state_to_dict",
    "SECTION_IDENTITY",
    "SECTION_INTENT",
    "SECTION_WORKING",
    "SECTION_EVIDENCE",
    "SECTION_CONVERSATION",
    "SECTION_TOOL",
    "SECTION_OUTPUT",
    "SECTION_NAMES",
]
