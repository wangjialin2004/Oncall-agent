"""Runtime wiring for the default-off unified context repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.context.envelope import CompletedTurnCommit, ContextEnvelope
from app.agent.context.integration import StatefulContext, build_default_store
from app.agent.context.operations import merge_active_attachment_refs, set_intent
from app.agent.context.renderers import RenderedContext, select_render_policy
from app.agent.context.state import AgentContextState
from app.agent.context.views import DEFAULT_VIEW_TOKEN_BUDGET
from app.config import config
from app.services.attachment_reference_service import strip_attachment_wrapper
from app.services.context_repository import (
    ContextRepository,
    build_default_context_repository,
    unified_context_repository_enabled,
)

_INFLIGHT_REF_PREFIX = "context-inflight:"


@dataclass(slots=True)
class UnifiedContextLoad:
    envelope: ContextEnvelope
    rendered: RenderedContext

    @property
    def state(self) -> AgentContextState:
        return self.envelope.projection


def inflight_context_ref(run_id: str) -> str:
    return f"{_INFLIGHT_REF_PREFIX}{run_id}" if run_id else ""


def inflight_run_id(reference: str | None) -> str:
    value = str(reference or "")
    if not value.startswith(_INFLIGHT_REF_PREFIX):
        return ""
    return value[len(_INFLIGHT_REF_PREFIX) :].strip()


async def prepare_unified_context(
    *,
    owner_key: str,
    session_id: str,
    current_question: str,
    current_goal: str,
    repository: ContextRepository | None = None,
    active_attachment_refs: list[dict[str, Any]] | None = None,
    token_budget: int = DEFAULT_VIEW_TOKEN_BUDGET,
    history_limit: int = 200,
    stateful: bool | None = None,
    base_prompt: str | None = None,
    run_id: str = "",
    resume_context_ref: str | None = None,
    resume_context_version: int | None = None,
) -> UnifiedContextLoad:
    """Load once, optionally rehydrate checkpoint inflight state, then render."""

    repository = repository or build_default_context_repository()
    current_question = strip_attachment_wrapper(current_question)
    current_goal = strip_attachment_wrapper(current_goal)
    envelope = await repository.load_envelope(owner_key, session_id)

    resumed_run_id = inflight_run_id(resume_context_ref)
    if resumed_run_id:
        inflight, inflight_warnings = await repository.load_inflight(
            owner_key=owner_key,
            session_id=session_id,
            run_id=resumed_run_id,
            expected_base_projection_version=envelope.watermark.projection_version,
            expected_state_version=resume_context_version,
        )
        envelope.warnings.extend(inflight_warnings)
        if inflight is not None:
            envelope.projection = inflight
            envelope.source = "redis_inflight"
            envelope.repair_actions.append("rehydrate_checkpoint_inflight")
            inflight._runtime_rehydrated_run_id = resumed_run_id
    elif resume_context_ref:
        envelope.warnings.append("legacy_checkpoint_context_ref")

    state = envelope.projection
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
    state._runtime_base_projection_version = envelope.watermark.projection_version
    state._runtime_run_id = run_id or session_id

    policy = select_render_policy(stateful=stateful)
    if hasattr(policy, "token_budget"):
        policy.token_budget = int(token_budget)  # type: ignore[attr-defined]
    rendered = policy.render(
        envelope,
        message=current_question,
        base_prompt=base_prompt,
    )
    return UnifiedContextLoad(envelope=envelope, rendered=rendered)


async def load_unified_stateful_context(
    *,
    owner_key: str,
    session_id: str,
    current_question: str,
    current_goal: str,
    repository: ContextRepository | None = None,
    active_attachment_refs: list[dict[str, Any]] | None = None,
    token_budget: int = DEFAULT_VIEW_TOKEN_BUDGET,
    history_limit: int = 200,
) -> StatefulContext:
    """Compatibility adapter used by focused integration tests."""

    loaded = await prepare_unified_context(
        owner_key=owner_key,
        session_id=session_id,
        current_question=current_question,
        current_goal=current_goal,
        repository=repository,
        active_attachment_refs=active_attachment_refs,
        token_budget=token_budget,
        history_limit=history_limit,
        stateful=True,
        run_id=session_id,
    )
    return StatefulContext(
        state=loaded.state,
        view=loaded.rendered.view,
        recent_messages=[item.to_dict() for item in loaded.rendered.history_messages],
        source=str(loaded.envelope.source),
        warnings=list(loaded.envelope.warnings),
    )


async def load_unified_envelope(
    *,
    owner_key: str,
    session_id: str,
    repository: ContextRepository | None = None,
) -> ContextEnvelope:
    repository = repository or build_default_context_repository()
    return await repository.load_envelope(owner_key, session_id)


def render_unified_envelope(
    envelope: ContextEnvelope,
    *,
    message: str = "",
    stateful: bool | None = None,
    base_prompt: str | None = None,
) -> RenderedContext:
    return select_render_policy(stateful=stateful).render(
        envelope,
        message=message,
        base_prompt=base_prompt,
    )


def build_completion_committer(
    *,
    owner_key: str,
    session_id: str,
    commit_id: str,
    user_message: str,
    user_context: str = "",
    attachment_refs: list[dict[str, Any]] | None = None,
    repository: ContextRepository | None = None,
    run_id: str = "",
):
    """Return the sole terminal persistence callback for unified mode."""

    repository = repository or build_default_context_repository()
    recovery_run_id = run_id or commit_id

    async def _commit(event: dict[str, Any], context_state: AgentContextState | None):
        if not unified_context_repository_enabled():
            return None
        events = list(event.get("events") or [])
        suggested_actions = [
            action
            for action in (event.get("suggested_actions") or [])
            if isinstance(action, dict) and str(action.get("id") or "").strip()
        ]
        if suggested_actions:
            events.append(
                {
                    "type": "decision_event",
                    "stage": "suggested_actions",
                    "actions": suggested_actions,
                }
            )
        result = await repository.commit_completed_turn(
            CompletedTurnCommit(
                owner_key=owner_key,
                session_id=session_id,
                commit_id=commit_id,
                user_message=user_message,
                user_context=user_context,
                attachment_refs=list(attachment_refs or []),
                assistant_answer=str(event.get("answer") or ""),
                route=str(event.get("route") or ""),
                case_id=str(event.get("case_id") or ""),
                events=events,
                projection=context_state,
            )
        )
        if result.committed:
            run_ids = {recovery_run_id}
            if context_state is not None and context_state._runtime_rehydrated_run_id:
                run_ids.add(context_state._runtime_rehydrated_run_id)
            for completed_run_id in run_ids:
                result.warnings.extend(
                    await repository.delete_inflight(
                        owner_key=owner_key,
                        session_id=session_id,
                        run_id=completed_run_id,
                    )
                )
        return result

    return _commit


async def persist_runtime_state(
    state: AgentContextState,
    *,
    store: Any | None = None,
    repository: ContextRepository | None = None,
    run_id: str = "",
    base_projection_version: int | None = None,
    last_flushed_version: int | None = None,
) -> tuple[list[str], int | None]:
    """Persist a stage to inflight Redis only, coalesced by state version."""

    if not unified_context_repository_enabled():
        from app.agent.context.integration import persist_stateful_context

        warnings = await persist_stateful_context(
            state,
            store=store or build_default_store(),
            persist_snapshot=True,
        )
        return warnings, int(state.version or 0)

    version = int(state.version or 0)
    if last_flushed_version is not None and version <= last_flushed_version:
        return [], last_flushed_version
    if not bool(getattr(config, "harness_checkpoint_enabled", False)):
        return [], version
    repository = repository or build_default_context_repository()
    warnings = await repository.save_inflight(
        owner_key=state.owner_key,
        session_id=state.session_id,
        run_id=run_id or state._runtime_run_id or state.session_id,
        state=state,
        base_projection_version=(
            int(base_projection_version)
            if base_projection_version is not None
            else int(state._runtime_base_projection_version or 0)
        ),
    )
    return warnings, version


__all__ = [
    "UnifiedContextLoad",
    "build_completion_committer",
    "inflight_context_ref",
    "inflight_run_id",
    "load_unified_envelope",
    "load_unified_stateful_context",
    "persist_runtime_state",
    "prepare_unified_context",
    "render_unified_envelope",
    "unified_context_repository_enabled",
]
