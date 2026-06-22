"""Application-owned tool runtime.

This module defines the small protocol used by AIOps/RAG agents when exposing
tools to the LLM and executing model-requested tool calls. It intentionally does
not depend on external tool wrappers.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(slots=True)
class RuntimeTool:
    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    handler: ToolHandler | None = None

    async def run(self, arguments: dict[str, Any] | None = None) -> Any:
        if self.handler is None:
            raise RuntimeError(f"Tool {self.name!r} has no handler")
        result = self.handler(arguments or {})
        if inspect.isawaitable(result):
            return await result
        return result


def make_runtime_tool(
    *,
    name: str,
    description: str,
    func: Callable[..., Any],
    args_schema: Any | None = None,
) -> RuntimeTool:
    """Create a RuntimeTool from a normal Python function."""

    parameters = {"type": "object", "properties": {}}
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        parameters = args_schema.model_json_schema()

    async def handler(arguments: dict[str, Any]) -> Any:
        result = func(**arguments)
        if inspect.isawaitable(result):
            return await result
        return result

    return RuntimeTool(
        name=name,
        description=description.strip(),
        parameters=parameters,
        handler=handler,
    )


async def run_tool(tool: RuntimeTool, arguments: dict[str, Any] | None = None) -> Any:
    return await tool.run(arguments or {})
