import pytest

from app.agent.agent_loop import stream_tool_results
from app.core.llm_client import ChatMessage
from app.core.tool_calling import ToolExecutionResult


@pytest.mark.asyncio
async def test_stream_tool_results_includes_processed_result_in_payload() -> None:
    messages: list[ChatMessage] = []
    result = ToolExecutionResult(
        call_id="call-1",
        tool_name="search_app_logs",
        content='{"status": "success", "logs": [], "total": 0}',
        success=True,
        raw={"status": "success", "logs": [], "total": 0},
    )

    events = [
        event
        async for event in stream_tool_results(
            [result],
            messages=messages,
            args_by_id={"call-1": {"keyword": "redis", "level": "WARN"}},
            agent_label="harness",
            trace_id="trace-1",
        )
    ]

    tool_event = events[0]
    assert tool_event["type"] == "tool_event"
    assert tool_event["payload"]["arguments"] == {"keyword": "redis", "level": "WARN"}
    assert tool_event["payload"]["result"] == '{"status": "success", "logs": [], "total": 0}'
    assert messages[0].content == tool_event["payload"]["result"]
