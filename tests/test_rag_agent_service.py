import pytest
from pydantic import BaseModel

from app.core.llm_client import LLMResponse, ToolCall
from app.services.rag_agent_service import RagAgentService


class EchoArgs(BaseModel):
    query: str


class EchoTool:
    name = "echo_tool"
    description = "Echo query."
    args_schema = EchoArgs

    async def ainvoke(self, args):
        return f"tool result for {args['query']}"


@pytest.mark.asyncio
async def test_query_uses_custom_llm_client_without_initializing_langgraph_agent():
    class FakeLLMClient:
        def __init__(self):
            self.messages = None
            self.temperature = None

        async def complete(self, messages, *, temperature, **kwargs):
            self.messages = messages
            self.temperature = temperature
            return LLMResponse(content="custom answer", raw={})

    llm_client = FakeLLMClient()
    service = RagAgentService(streaming=False, llm_client=llm_client)

    async def fail_if_agent_initializes():
        raise AssertionError("query should not initialize the LangGraph agent")

    service._initialize_agent = fail_if_agent_initializes

    answer = await service.query("how do I check slow response?", session_id="s1")

    assert answer == "custom answer"
    assert llm_client.temperature == 0.7
    assert llm_client.messages[0].role == "system"
    assert llm_client.messages[1].role == "user"
    assert llm_client.messages[1].content == "how do I check slow response?"


@pytest.mark.asyncio
async def test_query_stream_uses_custom_llm_client_without_langgraph_agent():
    class FakeLLMClient:
        async def complete(self, messages, *, temperature, **kwargs):
            return LLMResponse(content="streamed answer", raw={})

    service = RagAgentService(streaming=True, llm_client=FakeLLMClient())

    async def fail_if_agent_initializes():
        raise AssertionError("query_stream should not initialize the LangGraph agent")

    service._initialize_agent = fail_if_agent_initializes

    events = [
        event async for event in service.query_stream("how do I check errors?", session_id="s1")
    ]

    assert events == [
        {"type": "content", "data": "streamed answer", "node": "llm"},
        {"type": "complete"},
    ]


@pytest.mark.asyncio
async def test_query_runs_tool_calls_with_custom_llm_client():
    class FakeLLMClient:
        def __init__(self):
            self.calls = []

        async def complete(self, messages, *, temperature, tools=None, **kwargs):
            self.calls.append({"messages": messages, "tools": tools})
            if len(self.calls) == 1:
                return LLMResponse(
                    content="",
                    raw={},
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            name="echo_tool",
                            arguments={"query": "checkout-api"},
                        )
                    ],
                )
            assert messages[-1].role == "tool"
            assert messages[-1].tool_call_id == "call-1"
            assert messages[-1].content == "tool result for checkout-api"
            assert messages[-2].tool_calls[0]["function"]["arguments"] == '{"query": "checkout-api"}'
            return LLMResponse(content="final answer from tool result", raw={})

    llm_client = FakeLLMClient()
    service = RagAgentService(streaming=False, llm_client=llm_client)
    service.tools = [EchoTool()]
    service.mcp_tools = []

    answer = await service.query("check checkout-api", session_id="s1")

    assert answer == "final answer from tool result"
    assert llm_client.calls[0]["tools"][0].name == "echo_tool"
