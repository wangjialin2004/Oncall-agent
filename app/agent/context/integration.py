"""Harness integration layer for the stateful whiteboard.

Per plan ``plan/2026-07-08-stateful-agent-context.md`` §6 / §8.1, the
production harness loads and persists state through ``ContextRepository``.
``harness_stateful_context_enabled`` selects only the LLM-facing render policy;
it does not restore a ``ContextBuilder`` storage or loading path.

This compatibility module keeps state primitives available to isolated tests.
Production harness callers use ``app.agent.context.unified`` to:

1. Construct / fetch :class:`AgentContextState` for a turn.
2. Render the LLM-facing view (``system_prompt`` + recent message slice).
3. Apply controlled patches after each step / tool call.

``build_stateful_context`` and ``persist_stateful_context`` are retained only
for explicit compatibility fixtures, not as default runtime entry points.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.context.operations import (
    append_recent_turns,
    merge_active_attachment_refs,
    set_intent,
)
from app.agent.context.state import (
    AgentContextState,
)
from app.agent.context.store import (
    ContextStateStore,
    ContextStateStoreSettings,
)
from app.agent.context.tools_evidence import (
    record_tool_call_meta,
    record_tool_result,
    tool_result_to_summary,
)
from app.agent.context.views import (
    DEFAULT_VIEW_TOKEN_BUDGET,
    render_context_view,
    render_recent_message_view,
)
from app.config import config
from app.services.attachment_reference_service import strip_attachment_wrapper


def stateful_context_enabled() -> bool:
    """Read the LLM-view rendering policy flag; it defaults to enabled."""
    return bool(getattr(config, "harness_stateful_context_enabled", True))


def build_store_settings() -> ContextStateStoreSettings:
    """Read store knobs from config — used by callers constructing a store."""
    return ContextStateStoreSettings(
        redis_enabled=bool(getattr(config, "redis_enabled", True)),
        redis_namespace=str(getattr(config, "redis_namespace", "super_biz_agent")),
        redis_ttl_seconds=int(
            getattr(config, "harness_context_redis_ttl_seconds", 86_400)
        ),
        db_snapshot_enabled=bool(
            getattr(config, "harness_context_db_snapshot_enabled", True)
        ),
        patch_history_limit=int(
            getattr(config, "harness_context_patch_history_limit", 200)
        ),
    )


def build_default_store(
    *,
    snapshot_service: Any | None = None,
    redis_get_set: Callable[..., Awaitable[Any]] | None = None,
) -> ContextStateStore:
    """Build an explicitly injected compatibility store for isolated tests.

    Production runtime context is owned by :class:`ContextRepository`; callers
    must provide both callbacks when exercising the retired store in tests.
    """
    if snapshot_service is None:
        raise RuntimeError(
            "build_default_store is retired; use ContextRepository for runtime context"
        )

    if redis_get_set is None:
        async def _redis_get_set(op: str, key: str, value: Any, ttl: Any) -> Any:
            from app.services.redis_client import get_redis_client
            client = await get_redis_client()
            if op == "get":
                return await client.get(key)
            if op == "set":
                return await client.set(key, value, ex=ttl)
            raise ValueError(op)

        redis_get_set = _redis_get_set

    return ContextStateStore(
        db_snapshot_get=snapshot_service.get_latest_context_snapshot,
        db_snapshot_save=snapshot_service.save_context_snapshot,
        redis_get_set=redis_get_set,
        settings=build_store_settings(),
    )


@dataclass(slots=True)
class StatefulContext:
    """Bundle returned to the harness per turn.

    ``ContextRepository`` owns the persisted state. ``view`` and
    ``recent_messages`` are rendering products, regardless of the selected
    compatibility view policy.
    """

    state: AgentContextState
    view: str
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    source: str = "fresh"
    warnings: list[str] = field(default_factory=list)


async def build_stateful_context(
    *,
    owner_key: str,
    session_id: str,
    current_question: str,
    current_goal: str,
    store: ContextStateStore | None = None,
    rebuild: Callable[[str, str], Awaitable[AgentContextState | None]] | None = None,
    active_attachment_refs: list[dict[str, Any]] | None = None,
    token_budget: int = DEFAULT_VIEW_TOKEN_BUDGET,
    history_limit: int = 200,
) -> StatefulContext:
    """Fetch / rebuild state, then frame the prompt view.

    The ``current_question`` / ``current_goal`` are stamped onto ``intent``
    so the rendered view always reflects what the user is asking right now.
    They are framework-only writes (plan §3.3).
    """
    # Intent is a compact framework field.  API callers may pass the composed
    # attachment message for compatibility, so normalize it at this boundary
    # before writing the whiteboard.
    current_question = strip_attachment_wrapper(current_question)
    current_goal = strip_attachment_wrapper(current_goal)
    store = store or build_default_store()
    result = await store.get_or_rebuild(owner_key, session_id, rebuild=rebuild)

    state = result.state
    set_intent(
        state,
        current_question=current_question,
        current_goal=current_goal,
        history_limit=history_limit,
    )
    if active_attachment_refs:
        merge_active_attachment_refs(
            state,
            active_attachment_refs,
            history_limit=history_limit,
        )

    view = render_context_view(state, token_budget=token_budget)
    recent = render_recent_message_view(state)

    return StatefulContext(
        state=state,
        view=view,
        recent_messages=recent,
        source=result.source,
        warnings=list(result.warnings),
    )


async def persist_stateful_context(
    state: AgentContextState,
    *,
    store: ContextStateStore | None = None,
    persist_snapshot: bool = True,
) -> list[str]:
    """Persist state to Redis + DB snapshot. Returns warnings collected."""
    store = store or build_default_store()
    return await store.save(
        state.owner_key, state.session_id, state,
        persist_snapshot=persist_snapshot,
    )


def capture_tool_outcome(
    state: AgentContextState,
    *,
    tool_name: str,
    raw_result: Any,
    raw_ref: str,
    history_limit: int = 200,
) -> None:
    """Harness-side convenience: bridge a raw tool result into evidence.

    Delegates to :mod:`app.agent.context.tools_evidence` so all "raw payload
    → whiteboard row" reductions happen in one place. Never writes raw
    payloads (plan §8.2).
    """
    summary, fact, ok, latency = tool_result_to_summary(
        raw_result if isinstance(raw_result, dict) else {"ok": True, "content": str(raw_result)},
    )
    record_tool_result(
        state,
        tool_name=tool_name,
        success=ok,
        latency_ms=latency,
        raw_ref=raw_ref,
        content_summary=summary,
        fact=fact,
        history_limit=history_limit,
    )
    record_tool_call_meta(
        state,
        tool_name=tool_name,
        raw_ref=raw_ref,
        latency_ms=latency,
        status="success" if ok else "failure",
        history_limit=history_limit,
    )


def stamp_recent_turns(
    state: AgentContextState,
    *,
    turns: list[dict[str, Any]],
    last_turn_index: int | None = None,
    history_limit: int = 200,
) -> None:
    """Replace ``conversation.recent_turns`` with the harness's current slice.

    Plan §8.1: this is a *view* snapshot, not a full audit log; the canonical
    history is still in the conversation DB.
    """
    append_recent_turns(
        state,
        turns,
        last_turn_index=last_turn_index,
        history_limit=history_limit,
    )


__all__ = [
    "StatefulContext",
    "stateful_context_enabled",
    "build_store_settings",
    "build_default_store",
    "build_stateful_context",
    "persist_stateful_context",
    "capture_tool_outcome",
    "stamp_recent_turns",
]
