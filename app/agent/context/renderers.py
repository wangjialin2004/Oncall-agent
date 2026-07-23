"""Render policies that consume only a ContextEnvelope.

Structured and legacy policies must not import conversation/snapshot/Redis
services. They receive a pre-loaded envelope and return prompt fragments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.agent.context.envelope import ContextEnvelope
from app.agent.context.views import (
    DEFAULT_VIEW_TOKEN_BUDGET,
    render_context_view,
)
from app.agent.harness.context import HARNESS_SYSTEM_PROMPT
from app.config import config
from app.core.llm_client import ChatMessage


@dataclass(slots=True)
class RenderedContext:
    """Prompt fragments produced from a ContextEnvelope."""

    system_prompt: str
    history_messages: list[ChatMessage]
    view: str = ""
    source: str = "fresh"
    warnings: list[str] | None = None
    active_attachment_refs: list[dict[str, Any]] | None = None


class ContextRenderPolicy(Protocol):
    def render(
        self,
        envelope: ContextEnvelope,
        *,
        message: str = "",
        base_prompt: str | None = None,
    ) -> RenderedContext:
        """Render an envelope into system/history fragments."""


def _turns_to_chat_messages(turns: list[dict[str, Any]]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for turn in turns:
        user = str(turn.get("user_message") or turn.get("content") or "").strip()
        assistant = str(turn.get("assistant_answer") or "").strip()
        role = str(turn.get("role") or "").strip()
        if role == "user" and user:
            messages.append(ChatMessage(role="user", content=user))
            continue
        if role == "assistant" and (assistant or user):
            messages.append(ChatMessage(role="assistant", content=assistant or user))
            continue
        if user:
            messages.append(ChatMessage(role="user", content=user))
        if assistant:
            messages.append(ChatMessage(role="assistant", content=assistant))
    return messages


class StructuredRenderPolicy:
    """Whiteboard view renderer (stateful path)."""

    def __init__(self, *, token_budget: int | None = None) -> None:
        self.token_budget = int(
            token_budget
            if token_budget is not None
            else getattr(config, "harness_context_view_token_budget", DEFAULT_VIEW_TOKEN_BUDGET)
        )

    def render(
        self,
        envelope: ContextEnvelope,
        *,
        message: str = "",
        base_prompt: str | None = None,
    ) -> RenderedContext:
        del message  # current user message is appended by harness, not by renderer
        view = render_context_view(envelope.projection, token_budget=self.token_budget)
        history = _turns_to_chat_messages(envelope.turn_window)
        base = base_prompt or HARNESS_SYSTEM_PROMPT
        view_body = view.strip() or "（当前问题由最后一条 user message 提供）"
        system_prompt = f"{base}\n\n# 当前会话白板\n{view_body}"
        return RenderedContext(
            system_prompt=system_prompt,
            history_messages=history,
            view=view,
            source=envelope.source,
            warnings=list(envelope.warnings),
            active_attachment_refs=list(
                envelope.active_attachment_refs
                or envelope.projection.conversation.active_attachment_refs
                or []
            ),
        )


class LegacyRenderPolicy:
    """Summary/window renderer (legacy ContextBuilder semantics, no DB access)."""

    def __init__(
        self,
        *,
        history_max_turns: int | None = None,
        history_message_max_chars: int | None = None,
    ) -> None:
        self.history_max_turns = int(
            history_max_turns
            if history_max_turns is not None
            else getattr(config, "harness_history_max_turns", 6)
        )
        self.history_message_max_chars = int(
            history_message_max_chars
            if history_message_max_chars is not None
            else getattr(config, "harness_history_message_max_chars", 0)
        )

    def render(
        self,
        envelope: ContextEnvelope,
        *,
        message: str = "",
        base_prompt: str | None = None,
    ) -> RenderedContext:
        del message
        turns = list(envelope.turn_window or [])
        if self.history_max_turns <= 0:
            turns = []
        else:
            turns = turns[-self.history_max_turns :]
        history = _turns_to_chat_messages(turns)
        if self.history_message_max_chars > 0:
            clipped: list[ChatMessage] = []
            for item in history:
                content = item.content
                if len(content) > self.history_message_max_chars:
                    content = content[: self.history_message_max_chars] + "…"
                clipped.append(ChatMessage(role=item.role, content=content))
            history = clipped

        sections = [base_prompt or HARNESS_SYSTEM_PROMPT]
        if envelope.rolling_summary.strip():
            sections.append("# 历史摘要\n" + envelope.rolling_summary.strip())
        system_prompt = "\n\n".join(sections)
        return RenderedContext(
            system_prompt=system_prompt,
            history_messages=history,
            view="",
            source=envelope.source,
            warnings=list(envelope.warnings),
            active_attachment_refs=list(envelope.active_attachment_refs or []),
        )


def select_render_policy(*, stateful: bool | None = None) -> ContextRenderPolicy:
    """Choose renderer from ``HARNESS_STATEFUL_CONTEXT_ENABLED`` when unified."""
    enabled = (
        bool(getattr(config, "harness_stateful_context_enabled", True))
        if stateful is None
        else bool(stateful)
    )
    if enabled:
        return StructuredRenderPolicy()
    return LegacyRenderPolicy()


__all__ = [
    "ContextRenderPolicy",
    "LegacyRenderPolicy",
    "RenderedContext",
    "StructuredRenderPolicy",
    "select_render_policy",
]
