"""Provider-neutral tool calling helpers."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any

from app.core.llm_client import ChatMessage, ToolCall, ToolDefinition


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    call_id: str
    tool_name: str
    content: str
    success: bool
    raw: Any = None


def tool_to_definition(tool: Any) -> ToolDefinition:
    name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "")).strip()
    description = str(getattr(tool, "description", "") or getattr(tool, "__doc__", "") or "").strip()
    args_schema = getattr(tool, "args_schema", None)
    parameters = {"type": "object", "properties": {}}
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        parameters = args_schema.model_json_schema()
    return ToolDefinition(name=name, description=description, parameters=parameters)


async def execute_tool_calls(
    tool_calls: list[ToolCall],
    tools: list[Any],
) -> list[ToolExecutionResult]:
    tool_by_name = {
        str(getattr(tool, "name", "") or getattr(tool, "__name__", "")): tool for tool in tools
    }
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
            raw = await _invoke_tool(tool, tool_call.arguments)
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


async def _invoke_tool(tool: Any, arguments: dict[str, Any]) -> Any:
    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(arguments)
    if hasattr(tool, "invoke"):
        result = tool.invoke(arguments)
    elif hasattr(tool, "func"):
        result = tool.func(**arguments)
    else:
        result = tool(**arguments)
    if inspect.isawaitable(result):
        return await result
    return result


def _stringify_tool_result(raw: Any) -> str:
    if isinstance(raw, tuple) and raw:
        return _stringify_tool_result(raw[0])
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False, default=str)
