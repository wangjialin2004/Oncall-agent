"""Provider-neutral tool calling helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.llm_client import ToolDefinition
from app.core.runtime_tools import RuntimeTool


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
