from __future__ import annotations

import json

import pytest

from app.core.llm_client import ChatMessage, LLMClient, LLMClientConfig


class FakeStreamResponse:
    status_code = 200
    request = None

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeAsyncClient:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.requests = []

    def stream(self, method: str, url: str, **kwargs):
        self.requests.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeStreamResponse(self.lines)

    async def aclose(self) -> None:
        pass


def sse_payload(content: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": content}}]})


def sse_raw(payload: dict) -> str:
    return "data: " + json.dumps(payload)


@pytest.mark.asyncio
async def test_llm_client_stream_complete_yields_delta_content_chunks():
    http_client = FakeAsyncClient(
        [
            sse_payload("实时"),
            "",
            sse_payload("输出"),
            "",
            "data: [DONE]",
            "",
        ]
    )
    client = LLMClient(
        LLMClientConfig(provider="fake", base_url="http://llm.test/v1", api_key="k", model="m"),
        http_client=http_client,
    )

    chunks = [
        chunk
        async for chunk in client.stream_complete(
            [ChatMessage(role="user", content="hello")], temperature=0.2
        )
    ]

    assert chunks == ["实时", "输出"]
    request = http_client.requests[0]
    assert request["method"] == "POST"
    assert request["kwargs"]["json"]["stream"] is True
    assert request["kwargs"]["json"]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_llm_client_stream_chat_reassembles_tool_call_delta():
    http_client = FakeAsyncClient(
        [
            sse_raw(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "echo_tool",
                                            "arguments": '{"text"',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            "",
            sse_raw(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "arguments": ': "hello"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"total_tokens": 7},
                }
            ),
            "",
            "data: [DONE]",
            "",
        ]
    )
    client = LLMClient(
        LLMClientConfig(provider="fake", base_url="http://llm.test/v1", api_key="k", model="m"),
        http_client=http_client,
    )

    events = [
        event async for event in client.stream_chat([ChatMessage(role="user", content="hello")])
    ]

    response = events[-1].response
    assert response is not None
    assert response.finish_reason == "tool_calls"
    assert response.usage == {"total_tokens": 7}
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "echo_tool"
    assert response.tool_calls[0].arguments == {"text": "hello"}
    assert http_client.requests[0]["kwargs"]["json"]["stream"] is True
