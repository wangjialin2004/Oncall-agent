"""Context envelope: single load result for structured and legacy renderers.

The envelope separates durable scope/watermarks from runtime-only history.
Render policies must consume this object and must not open conversation,
snapshot, or Redis services themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.agent.context.state import AgentContextState

ProjectionStatus = Literal[
    "ready",
    "missing",
    "stale",
    "disabled",
    "rebuilt",
    "corrupt",
]
CacheSource = Literal[
    "redis_committed",
    "redis_inflight",
    "sqlite_projection",
    "turns_rebuild",
    "fresh",
    "legacy",
]


@dataclass(slots=True)
class ContextScope:
    """Authoritative tenant/session scope for a loaded envelope."""

    owner_key: str
    session_id: str


@dataclass(slots=True)
class ContextWatermark:
    """Durable projection watermarks.

    ``last_applied_turn_index`` is the last committed turn actually included in
    the projection. Empty conversations use ``-1`` / ``None``.
    """

    schema_version: int = 2
    projection_version: int = 0
    state_version: int = 0
    latest_turn_id: int | None = None
    latest_turn_index: int = -1
    last_applied_turn_id: int | None = None
    last_applied_turn_index: int = -1
    projection_status: ProjectionStatus = "missing"


@dataclass(slots=True)
class ContextEnvelope:
    """Unified load result shared by structured and legacy render policies."""

    scope: ContextScope
    watermark: ContextWatermark
    projection: AgentContextState
    turn_window: list[dict[str, Any]] = field(default_factory=list)
    rolling_summary: str = ""
    rolling_summary_turn_index: int = -1
    active_attachment_refs: list[dict[str, Any]] = field(default_factory=list)
    source: CacheSource = "fresh"
    warnings: list[str] = field(default_factory=list)
    repair_actions: list[str] = field(default_factory=list)

    @property
    def owner_key(self) -> str:
        return self.scope.owner_key

    @property
    def session_id(self) -> str:
        return self.scope.session_id


@dataclass(slots=True)
class CompletedTurnCommit:
    """Input for atomic turn + projection commit."""

    owner_key: str
    session_id: str
    commit_id: str
    user_message: str
    assistant_answer: str
    user_context: str = ""
    attachment_refs: list[dict[str, Any]] = field(default_factory=list)
    route: str = ""
    case_id: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    projection: AgentContextState | None = None
    title_hint: str = ""


@dataclass(slots=True)
class CommitResult:
    """Result of a durable context commit attempt."""

    committed: bool
    turn_id: int | None = None
    turn_index: int | None = None
    projection_version: int | None = None
    idempotent_replay: bool = False
    projection_status: ProjectionStatus = "ready"
    warnings: list[str] = field(default_factory=list)


__all__ = [
    "CacheSource",
    "CommitResult",
    "CompletedTurnCommit",
    "ContextEnvelope",
    "ContextScope",
    "ContextWatermark",
    "ProjectionStatus",
]
