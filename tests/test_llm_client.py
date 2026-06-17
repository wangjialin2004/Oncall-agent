import json
from types import SimpleNamespace

import httpx
import pytest

from app.core.llm_client import (
    ChatMessage,
    LLMAuthenticationError,
    LLMClient,
    LLMClientConfig,
    ToolDefinition,
)


@pytest.mark.asyncio
async def test_llm_client_posts_openai_compatible_chat_request():
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "vendor-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "hello from vendor",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    transport = httpx.MockTransport(handler)
    client = LLMClient(
        LLMClientConfig(
            provider="custom",
            base_url="https://vendor.example.com/openai/v1/",
            api_key="test-key",
            model="vendor-model",
        ),
        http_client=httpx.AsyncClient(transport=transport),
    )

    response = await client.complete(
        [
            ChatMessage(role="system", content="You are concise."),
            ChatMessage(role="user", content="Say hello."),
        ],
        temperature=0.2,
    )

    assert captured["url"] == "https://vendor.example.com/openai/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"] == {
        "model": "vendor-model",
        "messages": [
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": "Say hello."},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    assert response.content == "hello from vendor"
    assert response.raw["usage"]["total_tokens"] == 5

    await client.aclose()


def test_llm_client_config_prefers_generic_llm_settings():
    settings = SimpleNamespace(
        llm_provider="deepseek",
        llm_base_url="https://api.deepseek.com/v1",
        llm_api_key="deepseek-key",
        llm_model="deepseek-chat",
        llm_timeout=12.5,
        dashscope_api_key="dashscope-key",
        dashscope_model="qwen-max",
    )

    config = LLMClientConfig.from_settings(settings)

    assert config.provider == "deepseek"
    assert config.base_url == "https://api.deepseek.com/v1"
    assert config.api_key == "deepseek-key"
    assert config.model == "deepseek-chat"
    assert config.timeout == 12.5


def test_llm_client_config_falls_back_to_dashscope_settings():
    settings = SimpleNamespace(
        llm_provider="",
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        dashscope_api_key="dashscope-key",
        dashscope_model="qwen-max",
    )

    config = LLMClientConfig.from_settings(settings)

    assert config.provider == "dashscope"
    assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert config.api_key == "dashscope-key"
    assert config.model == "qwen-max"


@pytest.mark.asyncio
async def test_llm_client_sends_tools_and_parses_tool_calls():
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_current_time",
                                        "arguments": '{"timezone":"Asia/Shanghai"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    client = LLMClient(
        LLMClientConfig(
            provider="tool-vendor",
            base_url="https://tool.example.com/v1",
            api_key="test-key",
            model="tool-model",
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    response = await client.complete(
        [ChatMessage(role="user", content="What time is it?")],
        tools=[
            ToolDefinition(
                name="get_current_time",
                description="Get current time.",
                parameters={
                    "type": "object",
                    "properties": {"timezone": {"type": "string"}},
                },
            )
        ],
    )

    assert captured["payload"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "Get current time.",
                "parameters": {
                    "type": "object",
                    "properties": {"timezone": {"type": "string"}},
                },
            },
        }
    ]
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "get_current_time"
    assert response.tool_calls[0].arguments == {"timezone": "Asia/Shanghai"}

    await client.aclose()


@pytest.mark.asyncio
async def test_llm_client_raises_authentication_error_with_vendor_message():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "Incorrect API key provided.",
                    "code": "invalid_api_key",
                }
            },
        )

    client = LLMClient(
        LLMClientConfig(
            provider="dashscope",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="bad-key",
            model="qwen-max",
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(LLMAuthenticationError) as exc_info:
        await client.complete([ChatMessage(role="user", content="hello")])

    assert "dashscope" in str(exc_info.value)
    assert "Incorrect API key provided." in str(exc_info.value)

    await client.aclose()


@pytest.mark.asyncio
async def test_llm_client_parses_content_parts_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "first"},
                                {"type": "text", "text": "second"},
                            ],
                        }
                    }
                ]
            },
        )

    client = LLMClient(
        LLMClientConfig(
            provider="parts-vendor",
            base_url="https://parts.example.com/v1",
            api_key="test-key",
            model="parts-model",
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    response = await client.complete([ChatMessage(role="user", content="hello")])

    assert response.content == "first\nsecond"

    await client.aclose()
