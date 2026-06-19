"""Provider-neutral tool calling helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.llm_client import ChatMessage, ToolCall, ToolDefinition
from app.core.runtime_tools import RuntimeTool, run_tool


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    call_id: str
    tool_name: str
    content: str
    success: bool
    raw: Any = None


def tool_to_definition(tool: RuntimeTool) -> ToolDefinition:
    name = tool.name.strip()
    description = tool.description.strip()
    parameters = tool.parameters or {"type": "object", "properties": {}}
    return ToolDefinition(name=name, description=description, parameters=parameters)


async def execute_tool_calls(
    tool_calls: list[ToolCall],
    tools: list[RuntimeTool],
) -> list[ToolExecutionResult]:
    tool_by_name = {tool.name: tool for tool in tools}
    results: list[ToolExecutionResult] = []
    for tool_call in tool_calls:
        tool = tool_by_name.get(tool_call.name)
        if tool is None:
            results.append(
                ToolExecutionResult(
                    call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=f"Tool not found: {tool_call.name}",
                    success=False,
                )
            )
            continue
        try:
            raw = await run_tool(tool, tool_call.arguments)
            results.append(
                ToolExecutionResult(
                    call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=_stringify_tool_result(raw),
                    success=True,
                    raw=raw,
                )
            )
        except Exception as exc:
            results.append(
                ToolExecutionResult(
                    call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=f"Tool execution failed: {exc}",
                    success=False,
                )
            )
    return results


def tool_result_messages(results: list[ToolExecutionResult]) -> list[ChatMessage]:
    return [
        ChatMessage(role="tool", content=result.content, tool_call_id=result.call_id)
        for result in results
    ]


def _stringify_tool_result(raw: Any) -> str:
    if isinstance(raw, tuple) and raw:
        return _stringify_tool_result(raw[0])
    if isinstance(raw, str):
        return raw
    structured_content = getattr(raw, "structuredContent", None)
    if structured_content is not None:
        return json.dumps(structured_content, ensure_ascii=False, default=str)
    content = getattr(raw, "content", None)
    if isinstance(content, list):
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif hasattr(item, "model_dump"):
                parts.append(json.dumps(item.model_dump(mode="json"), ensure_ascii=False, default=str))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if hasattr(raw, "model_dump"):
        return json.dumps(raw.model_dump(mode="json"), ensure_ascii=False, default=str)
    return json.dumps(raw, ensure_ascii=False, default=str)
