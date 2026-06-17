from pydantic import BaseModel
import pytest

from app.core.llm_client import ChatMessage, ToolCall
from app.core.tool_calling import (
    execute_tool_calls,
    tool_result_messages,
    tool_to_definition,
)


class EchoArgs(BaseModel):
    text: str


class FakeTool:
    name = "echo"
    description = "Echo text."
    args_schema = EchoArgs

    def __init__(self):
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return f"echo:{args['text']}"


def test_tool_to_definition_reads_name_description_and_schema():
    definition = tool_to_definition(FakeTool())

    assert definition.name == "echo"
    assert definition.description == "Echo text."
    assert definition.parameters["properties"]["text"]["type"] == "string"
    assert definition.parameters["required"] == ["text"]


@pytest.mark.asyncio
async def test_execute_tool_calls_invokes_matching_tools_and_builds_tool_messages():
    tool = FakeTool()

    results = await execute_tool_calls(
        [ToolCall(id="call-1", name="echo", arguments={"text": "hello"})],
        [tool],
    )

    assert tool.calls == [{"text": "hello"}]
    assert results[0].tool_name == "echo"
    assert results[0].content == "echo:hello"
    assert results[0].success is True
    assert tool_result_messages(results) == [
        ChatMessage(role="tool", content="echo:hello", tool_call_id="call-1")
    ]
