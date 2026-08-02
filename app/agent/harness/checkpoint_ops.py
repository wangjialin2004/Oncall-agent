from __future__ import annotations

import asyncio
import copy
from collections.abc import Sequence
from typing import Any

from app.agent.context.integration import stamp_recent_turns
from app.agent.context.state import AgentContextState
from app.agent.harness.state import HarnessState
from app.agent.stream_common import TIMELINE_EVENT_TYPES
from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, get_default_llm_client
from app.services.attachment_reference_service import strip_attachment_wrapper
from app.services.harness_checkpoint import CheckpointResume


class HarnessCheckpointOpsMixin:
    """Checkpoint resume/save and stateful context rebuild helpers."""

    async def _new_llm_client(self) -> LLMClient:
        """Return the shared process-level LLM client.

        Returns the singleton from ``get_default_llm_client``; tests can still
        inject a custom client via ``HarnessService(llm_client=...)``.
        """
        return await get_default_llm_client()

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
        from app.agent.context.unified import inflight_context_ref

        return inflight_context_ref(context_state._runtime_run_id or context_state.session_id)

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
        existing: list[dict[str, Any]] = []
        for raw_turn in context_state.conversation.recent_turns or []:
            if not isinstance(raw_turn, dict):
                continue
            turn = dict(raw_turn)
            if str(turn.get("role") or "").strip().lower() == "user":
                if "content" in turn:
                    turn["content"] = strip_attachment_wrapper(str(turn.get("content") or ""))
                elif "text" in turn:
                    turn["text"] = strip_attachment_wrapper(str(turn.get("text") or ""))
            existing.append(turn)
        # The API runtime message may contain a full attachment wrapper.  The
        # canonical conversation DB already stores attachment refs/summary, so
        # the whiteboard history keeps only the user-visible question.
        user_text = strip_attachment_wrapper(user_message)
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
        """Decide whether resume continues the tool loop from ``next_step``.

        Checkpoint resume never re-executes historical ``tool_calls``; it only
        restores state/context and continues from the next incomplete step.
        Therefore past tool names must not block interrupt recovery.

        Policy:

        * default / ``replay_override=True`` / config replay true → continue
          (return True). Historical whitelist is ignored.
        * ``replay_override=False`` only → force close-only (return False),
          used when the caller explicitly wants a no-tool finalization.

        ``resume`` remains a parameter for API stability and future filters.
        """
        del resume  # historical tools are not a resume gate
        if replay_override is False:
            return False
        if replay_override is True:
            return True
        # Config flag is retained for compatibility. Both true and false now
        # mean "continue from next_step"; only request override False closes.
        _ = bool(getattr(config, "harness_checkpoint_replay", False))
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
        if store is None or not store.is_enabled() or not state.owner_key:
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
        snapshot_messages = copy.deepcopy(list(messages))
        snapshot_state = copy.deepcopy(state)
        key = (state.owner_key, state.session_id, state.trace_id)
        self._checkpoint_sessions_scheduled.add(key)
        self._track_checkpoint_task(
            key,
            store.save_step(
                owner_key=state.owner_key,
                session_id=state.session_id,
                state=snapshot_state,
                messages=snapshot_messages,
                step_index=step_index,
                step_payload=step_payload,
                context_version=(int(context_state.version) if context_state is not None else None),
                context_snapshot_ref=self._context_snapshot_ref(context_state),
                persist_messages=context_state is None,
            ),
        )

    def _schedule_checkpoint_completed(self, *, state: HarnessState) -> None:
        store = self.checkpoint_store
        if store is None or not store.is_enabled() or not state.owner_key:
            return
        key = (state.owner_key, state.session_id, state.trace_id)
        if key not in self._checkpoint_sessions_scheduled:
            return
        self._checkpoint_sessions_scheduled.discard(key)
        self._track_checkpoint_task(
            key,
            store.mark_completed(
                owner_key=state.owner_key,
                session_id=state.session_id,
                state=state,
            ),
        )

    def _track_checkpoint_task(
        self,
        key: tuple[str, str, str],
        coroutine: Any,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coroutine.close()
            return
        task = loop.create_task(coroutine)
        tasks = self._checkpoint_tasks.setdefault(key, set())
        tasks.add(task)

        def _discard(completed: asyncio.Task[Any]) -> None:
            scoped = self._checkpoint_tasks.get(key)
            if scoped is None:
                return
            scoped.discard(completed)
            if not scoped:
                self._checkpoint_tasks.pop(key, None)

        task.add_done_callback(_discard)

    async def _drain_checkpoint_tasks(
        self,
        *,
        owner_key: str,
        session_id: str,
        trace_id: str,
    ) -> None:
        key = (owner_key, session_id, trace_id)
        try:
            while True:
                tasks = set(self._checkpoint_tasks.get(key) or ())
                if not tasks:
                    return
                await asyncio.gather(*tasks, return_exceptions=True)
                scoped = self._checkpoint_tasks.get(key)
                if scoped is not None:
                    scoped.difference_update(tasks)
                    if not scoped:
                        self._checkpoint_tasks.pop(key, None)
        finally:
            self._checkpoint_sessions_scheduled.discard(key)
