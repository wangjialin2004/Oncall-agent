"""Provider-neutral LLM client for OpenAI-compatible chat APIs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from loguru import logger


ChatRole = Literal["system", "user", "assistant", "tool"]


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: str
    tool_call_id: str | None = None
    # raw OpenAI-format tool_calls array, used on the assistant turn after a tool call
    tool_calls: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LLMClientConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0
    max_retries: int = 2
    retry_base_delay: float = 0.5
    default_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Any) -> "LLMClientConfig":
        provider = _clean(getattr(settings, "llm_provider", "")) or "dashscope"
        base_url = _clean(getattr(settings, "llm_base_url", "")) or (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        api_key = _clean(getattr(settings, "llm_api_key", "")) or _clean(
            getattr(settings, "dashscope_api_key", "")
        )
        model = _clean(getattr(settings, "llm_model", "")) or _clean(
            getattr(settings, "dashscope_model", "")
        )
        timeout = float(getattr(settings, "llm_timeout", 60.0))
        return cls(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_retries=int(getattr(settings, "llm_max_retries", 2)),
            retry_base_delay=float(getattr(settings, "llm_retry_base_delay", 0.5)),
        )


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    raw: dict[str, Any]
    model: str | None = None
    finish_reason: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


class LLMClientError(RuntimeError):
    """Base error for custom LLM client failures."""


class LLMAuthenticationError(LLMClientError):
    """Raised when a provider rejects the configured API key."""


class LLMResponseError(LLMClientError):
    """Raised when a provider response cannot be parsed."""


class LLMClient:
    """Small OpenAI-compatible chat completions client.

    This client intentionally owns URL, API key, request payload, and response parsing
    so application code can avoid provider-specific LangChain chat wrappers.
    """

    def __init__(
        self,
        config: LLMClientConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=config.timeout)

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        stream: bool = False,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": [self._message_to_payload(message) for message in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = [self._tool_to_payload(tool) for tool in tools]
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if extra_body:
            payload.update(extra_body)

        data = await self._post_with_retry(payload)
        return self._parse_response(data)

    async def _post_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST chat completions with exponential backoff on transient failures.

        Retries on network errors and HTTP 429/5xx. Authentication errors (401)
        and other 4xx are not retried.
        """
        attempts = max(0, self.config.max_retries) + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.post(
                    self._chat_completions_url(),
                    headers=self._headers(),
                    json=payload,
                )
            except httpx.HTTPError as exc:
                last_error = LLMClientError(f"{self.config.provider} LLM request error: {exc}")
                if attempt < attempts - 1:
                    await self._sleep_backoff(attempt, reason=str(exc))
                    continue
                raise last_error from exc

            if response.status_code == 401:
                raise LLMAuthenticationError(self._format_provider_error(response))
            if response.status_code == 429 or response.status_code >= 500:
                last_error = LLMClientError(self._format_provider_error(response))
                if attempt < attempts - 1:
                    await self._sleep_backoff(attempt, reason=f"HTTP {response.status_code}")
                    continue
                raise last_error
            if response.status_code >= 400:
                raise LLMClientError(self._format_provider_error(response))

            return response.json()

        # Unreachable, but keeps type checkers satisfied.
        raise last_error or LLMClientError("LLM request failed")

    async def _sleep_backoff(self, attempt: int, *, reason: str) -> None:
        delay = self.config.retry_base_delay * (2**attempt)
        logger.warning(
            f"{self.config.provider} LLM request failed ({reason}); "
            f"retrying in {delay:.2f}s (attempt {attempt + 1}/{self.config.max_retries})"
        )
        await asyncio.sleep(delay)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
        else:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.config.default_headers)
        return headers

    def _chat_completions_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    @staticmethod
    def _message_to_payload(message: ChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            payload["tool_calls"] = message.tool_calls
        return payload

    @staticmethod
    def _tool_to_payload(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    def _format_provider_error(self, response: httpx.Response) -> str:
        provider = self.config.provider
        try:
            payload = response.json()
        except ValueError:
            return f"{provider} LLM request failed with HTTP {response.status_code}: {response.text}"

        message = payload
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error
            elif isinstance(error, str):
                message = error
            elif payload.get("message"):
                message = payload["message"]

        return f"{provider} LLM request failed with HTTP {response.status_code}: {message}"

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMResponseError("LLM response did not include choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LLMResponseError("LLM response choice is not an object")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise LLMResponseError("LLM response choice did not include a message")

        content = self._normalize_content(message.get("content"))
        raw_usage = data.get("usage")
        usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
        return LLMResponse(
            content=content,
            raw=data,
            model=data.get("model") if isinstance(data.get("model"), str) else None,
            finish_reason=(
                first_choice.get("finish_reason")
                if isinstance(first_choice.get("finish_reason"), str)
                else None
            ),
            tool_calls=self._parse_tool_calls(message.get("tool_calls")),
            usage=usage,
        )

    @staticmethod
    def _parse_tool_calls(raw_tool_calls: Any) -> list[ToolCall]:
        if not isinstance(raw_tool_calls, list):
            return []

        tool_calls: list[ToolCall] = []
        for item in raw_tool_calls:
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            raw_arguments = function.get("arguments") or "{}"
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {}
            elif isinstance(raw_arguments, dict):
                arguments = raw_arguments
            else:
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=str(item.get("id") or ""),
                    name=name,
                    arguments=arguments,
                )
            )
        return tool_calls

    @staticmethod
    def _normalize_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        if content is None:
            return ""
        return str(content)
