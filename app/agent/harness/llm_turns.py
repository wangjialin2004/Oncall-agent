from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any

from loguru import logger

from app.agent.agent_loop import estimate_tokens
from app.config import config
from app.core.llm_client import ChatMessage


class HarnessLlmTurnsMixin:
    """LLM turn helpers: truncate, stream chat/final, model tiers."""

    _planner_model_warned: bool = False

    def _truncate_messages_for_model(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Shrink an oversized prompt before sending it to the model.

        The loop appends every tool result to ``messages`` and never drops them,
        so a long multi-step run (or the no-tool closing call that re-sends the
        whole history) can exceed the model context window and fail the request.
        This is a per-request safety net: it compacts the *content* of the oldest
        history / tool messages — never removing a message — so assistant
        ``tool_calls`` stay paired with their ``tool`` results. The system prompt,
        the most recent user question, and the latest assistant/tool tail (newest
        evidence) are always preserved verbatim.
        """
        budget = self.message_token_budget
        if budget <= 0 or not messages:
            return messages
        total = sum(estimate_tokens(item.content or "") for item in messages)
        if total <= budget:
            return messages

        protected: set[int] = set()
        if messages[0].role == "system":
            protected.add(0)
        last_user = max(
            (index for index, item in enumerate(messages) if item.role == "user"),
            default=-1,
        )
        if last_user >= 0:
            protected.add(last_user)
        last_assistant = max(
            (index for index, item in enumerate(messages) if item.role == "assistant"),
            default=-1,
        )
        if last_assistant >= 0:
            protected.update(range(last_assistant, len(messages)))

        stub = "[早期上下文已压缩以控制 token 预算]"
        stub_tokens = estimate_tokens(stub)
        trimmed = list(messages)
        for index, item in enumerate(trimmed):
            if total <= budget:
                break
            if index in protected:
                continue
            original = item.content or ""
            if estimate_tokens(original) <= stub_tokens:
                continue
            total -= estimate_tokens(original) - stub_tokens
            trimmed[index] = replace(item, content=stub)
        return trimmed

    async def _stream_final_answer(
        self,
        *,
        client: Any,
        messages: list[ChatMessage],
        temperature: float,
        model: str | None = None,
    ) -> AsyncGenerator[str, None]:
        messages = self._truncate_messages_for_model(messages)
        stream_complete = getattr(client, "stream_complete", None)
        if stream_complete is None:
            response = await client.complete(messages, temperature=temperature, model=model)
            yield response.content
            return
        async for chunk in stream_complete(messages, temperature=temperature, model=model):
            yield str(chunk)

    async def _stream_chat_turn(
        self,
        *,
        client: Any,
        messages: list[ChatMessage],
        temperature: float,
        tools: list[Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        messages = self._truncate_messages_for_model(messages)
        stream_chat = getattr(client, "stream_chat", None)
        if stream_chat is None:
            response = await client.complete(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                model=model,
            )
            yield {"response": response}
            return
        async for event in stream_chat(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            model=model,
        ):
            content = str(getattr(event, "content", "") or "")
            if content:
                yield {"content": content}
            response = getattr(event, "response", None)
            if response is not None:
                yield {"response": response}

    async def _collect_chat_turn(
        self,
        *,
        client: Any,
        messages: list[ChatMessage],
        temperature: float,
        tools: list[Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        model: str | None = None,
    ) -> tuple[Any | None, str, tuple[str, ...]]:
        """Collect one tool-capable decision turn without exposing its text.

        Some providers emit planner narration or even textual tool protocol in
        ``content`` before the final structured response is known. Callers must
        inspect the completed response before deciding whether any text is safe
        for the user-visible answer.
        """

        response = None
        content_parts: list[str] = []
        async for event in self._stream_chat_turn(
            client=client,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            model=model,
        ):
            content = event.get("content")
            if content:
                content_parts.append(str(content))
            if event.get("response") is not None:
                response = event["response"]
        return response, "".join(content_parts), tuple(content_parts)

    def _planner_model(self) -> str | None:
        """Return the model used for planning and next-action decisions."""
        return str(getattr(config, "llm_planner_model", "") or "") or None

    def _reasoner_model(self) -> str | None:
        """Return the model used for final reasoning and verification."""
        return str(getattr(config, "llm_reasoner_model", "") or "") or None

    def _warn_if_planner_model_unset(self) -> None:
        """Log once when planner model is empty (falls back to default llm_model)."""
        if HarnessLlmTurnsMixin._planner_model_warned:
            return
        if str(getattr(config, "llm_planner_model", "") or "").strip():
            return
        HarnessLlmTurnsMixin._planner_model_warned = True
        logger.warning(
            "LLM_PLANNER_MODEL is empty; harness planner/step calls fall back to "
            "the default LLM_MODEL. Set a lighter planner model to improve P50."
        )
