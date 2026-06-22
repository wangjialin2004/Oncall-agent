"""Provider-neutral LLM client for OpenAI-compatible chat APIs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from collections.abc import AsyncGenerator
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


@dataclass(frozen=True, slots=True)
class LLMStreamChunk:
    content: str = ""
    response: LLMResponse | None = None


class LLMClientError(RuntimeError):
    """Base error for custom LLM client failures."""


class LLMAuthenticationError(LLMClientError):
    """Raised when a provider rejects the configured API key."""


class LLMResponseError(LLMClientError):
    """Raised when a provider response cannot be parsed."""


class LLMClient:
    """Small OpenAI-compatible chat completions client.

    This client intentionally owns URL, API key, request payload, and response parsing
    so application code can avoid provider-specific chat wrappers.
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

    async def stream_complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        extra_body: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream final assistant content from an OpenAI-compatible SSE response.

        This intentionally supports content-only final answers. Tool-call turns still
        use ``complete()`` so callers receive fully parsed tool call objects.
        """

        async for event in self.stream_chat(
            messages,
            model=model,
            temperature=temperature,
            extra_body=extra_body,
        ):
            if event.content:
                yield event.content

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """Stream a chat turn while preserving the final parsed response.

        Text deltas are yielded immediately as ``content`` chunks. After the SSE
        stream finishes, one final event carries the assembled ``LLMResponse`` so
        tool-call loops can continue without falling back to blocking
        ``complete()``.
        """

        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": [self._message_to_payload(message) for message in messages],
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = [self._tool_to_payload(tool) for tool in tools]
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if extra_body:
            payload.update(extra_body)

        content_parts: list[str] = []
        raw_chunks: list[dict[str, Any]] = []
        tool_call_parts: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish_reason: str | None = None
        response_model: str | None = None

        async for data in self._stream_with_retry(payload):
            raw_chunks.append(data)
            if isinstance(data.get("model"), str):
                response_model = data["model"]
            raw_usage = data.get("usage")
            if isinstance(raw_usage, dict):
                usage = dict(raw_usage)

            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                continue
            if isinstance(first_choice.get("finish_reason"), str):
                finish_reason = first_choice["finish_reason"]
            delta = first_choice.get("delta")
            if not isinstance(delta, dict):
                continue

            chunk = self._normalize_content(delta.get("content"))
            if chunk:
                content_parts.append(chunk)
                yield LLMStreamChunk(content=chunk)
            self._accumulate_stream_tool_calls(delta.get("tool_calls"), tool_call_parts)

        raw_tool_calls = self._tool_call_parts_to_payload(tool_call_parts)
        response = LLMResponse(
            content="".join(content_parts),
            raw={"stream_chunks": raw_chunks},
            model=response_model,
            finish_reason=finish_reason,
            tool_calls=self._parse_tool_calls(raw_tool_calls),
            usage=usage,
        )
        yield LLMStreamChunk(response=response)

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

    async def _stream_with_retry(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        attempts = max(0, self.config.max_retries) + 1
        for attempt in range(attempts):
            try:
                async with self._client.stream(
                    "POST",
                    self._chat_completions_url(),
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    if response.status_code == 401:
                        raise LLMAuthenticationError(await self._format_provider_error_async(response))
                    if response.status_code == 429 or response.status_code >= 500:
                        error = LLMClientError(await self._format_provider_error_async(response))
                        if attempt < attempts - 1:
                            await self._sleep_backoff(
                                attempt, reason=f"HTTP {response.status_code}"
                            )
                            continue
                        raise error
                    if response.status_code >= 400:
                        raise LLMClientError(await self._format_provider_error_async(response))

                    async for payload in self._iter_sse_payloads(response):
                        yield payload
                    return
            except (LLMAuthenticationError, LLMClientError):
                raise
            except httpx.HTTPError as exc:
                error = LLMClientError(f"{self.config.provider} LLM stream error: {exc}")
                if attempt < attempts - 1:
                    await self._sleep_backoff(attempt, reason=str(exc))
                    continue
                raise error from exc

    async def _iter_sse_payloads(
        self, response: httpx.Response
    ) -> AsyncGenerator[dict[str, Any], None]:
        data_lines: list[str] = []
        async for raw_line in response.aiter_lines():
            line = raw_line.strip()
            if not line:
                if data_lines:
                    raw_data = "\n".join(data_lines).strip()
                    data_lines = []
                    if raw_data == "[DONE]":
                        return
                    try:
                        payload = json.loads(raw_data)
                    except json.JSONDecodeError as exc:
                        raise LLMResponseError(f"Invalid LLM stream payload: {raw_data}") from exc
                    if isinstance(payload, dict):
                        yield payload
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                value = line[5:]
                data_lines.append(value[1:] if value.startswith(" ") else value)

        if data_lines:
            raw_data = "\n".join(data_lines).strip()
            if raw_data and raw_data != "[DONE]":
                try:
                    payload = json.loads(raw_data)
                except json.JSONDecodeError as exc:
                    raise LLMResponseError(f"Invalid LLM stream payload: {raw_data}") from exc
                if isinstance(payload, dict):
                    yield payload

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

    async def _format_provider_error_async(self, response: httpx.Response) -> str:
        content = await response.aread()
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return (
                f"{self.config.provider} LLM request failed with HTTP "
                f"{response.status_code}: {content.decode('utf-8', errors='replace')}"
            )
        return self._format_provider_error(
            httpx.Response(response.status_code, json=payload, request=response.request)
        )

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

    @classmethod
    def _parse_stream_content(cls, data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return ""
        delta = first_choice.get("delta")
        if not isinstance(delta, dict):
            return ""
        return cls._normalize_content(delta.get("content"))

    @staticmethod
    def _accumulate_stream_tool_calls(
        raw_tool_calls: Any, tool_call_parts: dict[int, dict[str, Any]]
    ) -> None:
        if not isinstance(raw_tool_calls, list):
            return
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, dict):
                continue
            raw_index = raw_call.get("index", len(tool_call_parts))
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                index = len(tool_call_parts)
            part = tool_call_parts.setdefault(
                index,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if raw_call.get("id"):
                part["id"] = str(raw_call["id"])
            if raw_call.get("type"):
                part["type"] = str(raw_call["type"])
            function = raw_call.get("function")
            if not isinstance(function, dict):
                continue
            part_function = part.setdefault("function", {"name": "", "arguments": ""})
            if function.get("name"):
                part_function["name"] = f"{part_function.get('name', '')}{function['name']}"
            if function.get("arguments"):
                part_function["arguments"] = (
                    f"{part_function.get('arguments', '')}{function['arguments']}"
                )

    @staticmethod
    def _tool_call_parts_to_payload(
        tool_call_parts: dict[int, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [tool_call_parts[index] for index in sorted(tool_call_parts)]

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


def new_llm_client() -> LLMClient:
    """Create an application-configured LLM client from global settings.

    Single construction point for the OpenAI-compatible client so callers don't
    repeat ``LLMClient(LLMClientConfig.from_settings(config))`` everywhere.
    """
    from app.config import config

    return LLMClient(LLMClientConfig.from_settings(config))
