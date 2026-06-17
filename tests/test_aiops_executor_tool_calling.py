from pydantic import BaseModel
import pytest

from app.agent.aiops.executor import execute_step_with_tools
from app.core.llm_client import LLMResponse, ToolCall


class EchoArgs(BaseModel):
    query: str


class EchoTool:
    name = "echo_tool"
    description = "Echo query."
    args_schema = EchoArgs

    async def ainvoke(self, args):
        return f"tool evidence for {args['query']}"


@pytest.mark.asyncio
async def test_execute_step_with_tools_runs_tool_calls_and_summarizes_result():
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
            assert messages[-1].content == "tool evidence for checkout-api"
            return LLMResponse(content="final diagnosis evidence", raw={})

    result, evidence_records = await execute_step_with_tools(
        state={"input": "diagnose checkout-api"},
        task={"step_id": "plan-1", "description": "check checkout-api"},
        tools=[EchoTool()],
        llm_client=FakeLLMClient(),
    )

    assert result == "final diagnosis evidence"
    assert evidence_records[0]["tool_name"] == "echo_tool"
    assert evidence_records[0]["evidence_id"] == "call-1"
    assert evidence_records[0]["success"] is True
    assert evidence_records[0]["summary"] == "tool evidence for checkout-api"
    assert evidence_records[0]["source"] == "tool_call"
    assert evidence_records[0]["arguments"] == {"query": "checkout-api"}
