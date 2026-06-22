"""Tool registry for the unified harness."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.agent.experts.base import collect_tools
from app.agent.harness.subagent import create_delegate_tool
from app.config import config
from app.core.runtime_tools import RuntimeTool
from app.tools import (
    CHANGE_LOCAL_TOOLS,
    DIAGNOSIS_LOCAL_TOOLS,
    KNOWLEDGE_LOCAL_TOOLS,
    LOG_LOCAL_TOOLS,
    METRIC_LOCAL_TOOLS,
)


@dataclass(frozen=True, slots=True)
class ToolMetadata:
    name: str
    source: str
    permission: str = "read"


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    tools: list[RuntimeTool]
    metadata: dict[str, ToolMetadata]


class HarnessToolRegistry:
    def __init__(self, tools: list[RuntimeTool] | None = None) -> None:
        self._tools_override = tools

    async def collect(
        self,
        *,
        route: str,
        session_id: str,
        trace_id: str,
        context_getter: Callable[[], str],
    ) -> ToolCatalog:
        if self._tools_override is not None:
            tools = list(self._tools_override)
            metadata = {
                tool.name: ToolMetadata(name=tool.name, source="test_override") for tool in tools
            }
            return ToolCatalog(tools=tools, metadata=metadata)

        local_tools, mcp_server = _tools_for_route(route)
        if not config.harness_mcp_enabled:
            mcp_server = None
        tools = await collect_tools(local_tools, mcp_server=mcp_server)
        metadata = {
            tool.name: ToolMetadata(name=tool.name, source="local") for tool in tools
        }

        if config.harness_delegation_enabled and route == "diagnosis":
            delegate_tool = create_delegate_tool(
                session_id=session_id,
                trace_id=trace_id,
                context_getter=context_getter,
            )
            tools.append(delegate_tool)
            metadata[delegate_tool.name] = ToolMetadata(
                name=delegate_tool.name,
                source="subagent",
                permission="read_delegate",
            )

        return ToolCatalog(tools=tools, metadata=metadata)


def _tools_for_route(route: str) -> tuple[tuple[RuntimeTool, ...], str | tuple[str, ...] | None]:
    """Return the same scoped tool surface used by the classic experts."""

    if route == "knowledge":
        return KNOWLEDGE_LOCAL_TOOLS, None
    if route == "metric":
        return METRIC_LOCAL_TOOLS, "monitor"
    if route == "log":
        return LOG_LOCAL_TOOLS, "cls"
    if route == "change":
        return CHANGE_LOCAL_TOOLS, None
    return DIAGNOSIS_LOCAL_TOOLS, ("monitor", "cls")
