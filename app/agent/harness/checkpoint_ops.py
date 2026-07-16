from __future__ import annotations

from app.config import config

import asyncio
from collections.abc import Sequence
from typing import Any

from app.agent.context.integration import stamp_recent_turns
from app.agent.context.operations import merge_active_attachment_refs
from app.agent.context.state import AgentContextState
from app.agent.harness.state import HarnessState
from app.agent.stream_common import TIMELINE_EVENT_TYPES
from app.core.llm_client import ChatMessage, LLMClient, get_default_llm_client
from app.core.runtime_tools import RuntimeTool
from app.services.harness_checkpoint import CheckpointResume

class HarnessCheckpointOpsMixin:
    """Checkpoint resume/save and stateful context rebuild helpers."""

    async def _new_llm_client(self) -> LLMClient:
        """Return the shared process-level LLM client.

        Returns the singleton from ``get_default_llm_client``; tests can still
        inject a custom client via ``HarnessService(llm_client=...)``.
        """
        return await get_default_llm_client()

    async def _rebuild_context_state_from_legacy(
        self,
        *,
        owner_key: str,
        session_id: str,
        message: str,
        tools: Sequence[RuntimeTool],
        focus_hint: str,
        llm_client: Any,
    ) -> AgentContextState:
        """Cold-start ContextState from the legacy ContextBuilder.

        This is only used after Redis and DB snapshot both miss. The normal
        stateful path must not call this method, because it may read
        conversation turns and rolling summary data.
        """
        legacy = await self.context_builder.abuild(
            message=message,
            owner_key=owner_key,
            session_id=session_id,
            tools=tools,
            focus_hint=focus_hint,
            llm_client=llm_client,
        )
        rebuilt = AgentContextState(owner_key=owner_key, session_id=session_id)
        stamp_recent_turns(
            rebuilt,
            turns=[
                {"role": item.role, "content": item.content}
                for item in legacy.history_messages
                if item.content
            ],
            last_turn_index=len(legacy.history_messages),
            history_limit=int(getattr(config, "harness_context_patch_history_limit", 200)),
        )
        legacy_refs = [
            ref.to_dict()
            for ref in getattr(legacy, "active_attachments", ()) or ()
            if hasattr(ref, "to_dict")
        ]
        if legacy_refs:
            merge_active_attachment_refs(
                rebuilt,
                legacy_refs,
                history_limit=int(getattr(config, "harness_context_patch_history_limit", 200)),
            )
        return rebuilt

    @staticmethod
    def _recent_dicts_to_messages(items: Sequence[dict[str, Any]]) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for item in items or []:
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or item.get("text") or "").strip()
            if role in {"system", "user", "assistant", "tool"} and content:
                messages.append(ChatMessage(role=role, content=content))
        return messages

    @staticmethod
    def _context_snapshot_ref(context_state: AgentContextState | None) -> str | None:
        if context_state is None:
            return None
        return (
            f"{context_state.owner_key}:{context_state.session_id}:"
            f"v{int(context_state.version)}"
        )

    @staticmethod
    def _stamp_current_turn(
        context_state: AgentContextState,
        *,
        user_message: str,
        assistant_answer: str,
        max_turns: int = 12,
        answer_max_chars: int = 600,
    ) -> None:
        """Append this turn's Q/A into conversation.recent_turns (happy path)."""
        history_limit = int(getattr(config, "harness_context_patch_history_limit", 200))
        existing = list(context_state.conversation.recent_turns or [])
        user_text = (user_message or "").strip()
        assistant_text = (assistant_answer or "").strip()
        if len(assistant_text) > answer_max_chars:
            assistant_text = f"{assistant_text[:answer_max_chars]}..."
        new_turns: list[dict[str, Any]] = []
        if user_text:
            new_turns.append({"role": "user", "content": user_text})
        if assistant_text:
            new_turns.append({"role": "assistant", "content": assistant_text})
        if not new_turns:
            return
        merged = existing + new_turns
        # Keep a short tail so the whiteboard stays within view budget.
        if len(merged) > max_turns * 2:
            merged = merged[-(max_turns * 2) :]
        next_index = int(context_state.conversation.last_turn_index or 0) + 1
        stamp_recent_turns(
            context_state,
            turns=merged,
            last_turn_index=next_index,
            history_limit=history_limit,
        )

    @staticmethod
    def _should_replay_resume(
        resume: CheckpointResume,
        *,
        replay_override: bool | None = None,
    ) -> bool:
        """Conservative mode (default): only replay if every committed step
        used a tool from the idempotent whitelist; otherwise we still allow
        the resume event but treat the run as finished (no further tool calls).
        Aggressive mode (``harness_checkpoint_replay=True``) replays verbatim.

        ``replay_override`` is the per-request flag forwarded from the HTTP
        layer: ``True`` forces replay regardless of config or whitelist,
        ``False`` forces conservative close (treats every non-whitelist step
        as a no-replay trigger, even if the config would allow replay),
        ``None`` falls back to the config flag.
        """
        config_replay = bool(getattr(config, "harness_checkpoint_replay", False))
        effective_replay = config_replay if replay_override is None else bool(replay_override)

        if effective_replay:
            return True

        whitelist_obj = resume.idempotent_tools
        whitelist = set(whitelist_obj or [])
        for step in resume.steps or []:
            tool_calls = step.get("tool_calls") or []
            names = [
                str((call.get("function") or {}).get("name") or "")
                for call in tool_calls
                if isinstance(call, dict)
            ]
            # Empty tool_names (no-call steps like closing) are fine.
            if names and any(name not in whitelist for name in names):
                return False
        return True

    @staticmethod
    def _restore_state_from_resume(
        resume: CheckpointResume, fallback: HarnessState
    ) -> HarnessState:
        """Rebuild a ``HarnessState`` from the checkpoint, falling back to the
        fresh state for any field that did not survive serialization.
        """
        fields = dict(resume.state_fields or {})
        # Restore simple scalars; collections are kept as-is from JSON.
        return HarnessState(
            trace_id=str(fields.get("trace_id") or fallback.trace_id),
            session_id=str(fields.get("session_id") or fallback.session_id),
            owner_key=str(fields.get("owner_key") or fallback.owner_key),
            route=str(fields.get("route") or fallback.route),
            route_reason=str(fields.get("route_reason") or fallback.route_reason),
            case_id=str(fields.get("case_id") or fallback.case_id),
            step=int(fields.get("step") or resume.next_step - 1),
            answer_parts=list(fields.get("answer_parts") or []),
            timeline_events=list(fields.get("timeline_events_tail") or []),
            usage_total=dict(fields.get("usage_total") or {}),
            token_estimate=int(fields.get("token_estimate") or 0),
        )

    def _schedule_checkpoint_save(
        self,
        *,
        state: HarnessState,
        messages: Sequence[ChatMessage],
        step_index: int,
        tool_calls: list[dict[str, Any]],
        context_state: AgentContextState | None = None,
    ) -> None:
        """Fire-and-forget Redis write so the SSE response is never blocked."""
        store = self.checkpoint_store
        if store is None or not state.owner_key:
            return
        if step_index <= 0:
            return
        step_payload = {
            "step": int(step_index),
            "tool_calls": [dict(call) for call in tool_calls],
            "events": [
                event
                for event in (state.timeline_events or [])[-30:]
                if event.get("type") in TIMELINE_EVENT_TYPES
            ],
            "completed": True,
        }
        snapshot_messages = list(messages)
        snapshot_state = state
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            store.save_step(
                owner_key=state.owner_key,
                session_id=state.session_id,
                state=snapshot_state,
                messages=snapshot_messages,
                step_index=step_index,
                step_payload=step_payload,
                context_version=(
                    int(context_state.version) if context_state is not None else None
                ),
                context_snapshot_ref=self._context_snapshot_ref(context_state),
                persist_messages=context_state is None,
            )
        )

    def _schedule_checkpoint_completed(self, *, state: HarnessState) -> None:
        store = self.checkpoint_store
        if store is None or not state.owner_key:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            store.mark_completed(
                owner_key=state.owner_key,
                session_id=state.session_id,
                state=state,
            )
        )
