"""MCP client management using the native MCP SDK."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger
from mcp import types as mcp_types
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from app.config import config
from app.core.runtime_tools import RuntimeTool

DEFAULT_MCP_SERVERS = config.mcp_servers
_mcp_client: NativeMCPClient | None = None


def format_exception_chain(exc: BaseException) -> str:
    """Expand ExceptionGroup/TaskGroup errors for readable logs."""

    sub_exceptions = getattr(exc, "exceptions", None)
    if sub_exceptions is not None:
        lines = [str(exc)]
        for i, sub in enumerate(sub_exceptions):
            lines.append(f"  [{i}] {format_exception_chain(sub)}")
        return "\n".join(lines)
    msg = f"{type(exc).__name__}: {exc}"
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        return f"{msg}\n  caused by: {format_exception_chain(cause)}"
    return msg


class NativeMCPClient:
    """Small native MCP client that returns application RuntimeTool objects."""

    def __init__(
        self,
        servers: dict[str, dict[str, Any]],
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self.servers = servers
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def get_tools(self, *, server_name: str | None = None) -> list[RuntimeTool]:
        if server_name is not None:
            return await self._get_server_tools(server_name)

        tasks = [asyncio.create_task(self._get_server_tools(name)) for name in self.servers]
        tool_groups = await asyncio.gather(*tasks)
        return [tool for group in tool_groups for tool in group]

    async def _get_server_tools(self, server_name: str) -> list[RuntimeTool]:
        if server_name not in self.servers:
            raise ValueError(
                f"Couldn't find MCP server {server_name!r}; expected one of {list(self.servers)}"
            )

        async with self.session(server_name) as session:
            result = await session.list_tools()

        return [
            RuntimeTool(
                name=tool.name,
                description=tool.description or "",
                parameters=tool.inputSchema,
                handler=self._make_tool_handler(server_name, tool.name),
            )
            for tool in result.tools
        ]

    def _make_tool_handler(self, server_name: str, tool_name: str):
        async def handler(arguments: dict[str, Any]) -> mcp_types.CallToolResult:
            return await self.call_tool(server_name, tool_name, arguments)

        return handler

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> mcp_types.CallToolResult:
        last_error: BaseException | None = None
        for attempt in range(self.max_retries):
            try:
                logger.info(
                    f"Calling MCP tool: {tool_name} "
                    f"(server={server_name}, attempt={attempt + 1}/{self.max_retries})"
                )
                async with self.session(server_name) as session:
                    result = await session.call_tool(tool_name, arguments or {})
                logger.info(f"MCP tool {tool_name} call succeeded")
                return result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    f"MCP tool {tool_name} call failed "
                    f"(attempt={attempt + 1}/{self.max_retries}): {format_exception_chain(exc)}"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (2**attempt))

        error_msg = (
            f"Tool {tool_name} failed after {self.max_retries} retries: {last_error}"
        )
        logger.error(error_msg)
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=error_msg)],
            isError=True,
        )

    @asynccontextmanager
    async def session(self, server_name: str):
        if server_name not in self.servers:
            raise ValueError(
                f"Couldn't find MCP server {server_name!r}; expected one of {list(self.servers)}"
            )

        server = self.servers[server_name]
        transport = str(server.get("transport", "")).replace("_", "-")
        headers = server.get("headers")
        timeout = float(server.get("timeout", 30))

        if transport == "streamable-http":
            url = _required_url(server_name, server)
            async with streamablehttp_client(
                url,
                headers=headers,
                timeout=timeout,
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
            return

        if transport == "sse":
            url = _required_url(server_name, server)
            async with sse_client(url, headers=headers, timeout=timeout) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
            return

        if transport == "stdio":
            params = StdioServerParameters(
                command=str(server.get("command", "")),
                args=[str(arg) for arg in server.get("args", [])],
                env=server.get("env"),
                cwd=server.get("cwd"),
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
            return

        raise ValueError(f"Unsupported MCP transport for {server_name}: {transport!r}")


def _required_url(server_name: str, server: dict[str, Any]) -> str:
    url = str(server.get("url", "")).strip()
    if not url:
        raise ValueError(f"MCP server {server_name!r} requires a URL")
    return url


async def load_mcp_tools_safe(
    client: NativeMCPClient,
) -> tuple[list[RuntimeTool], str | None]:
    """Load MCP tools; return a readable error instead of raising."""

    try:
        tools = await client.get_tools()
        return tools, None
    except BaseException as e:
        return [], format_exception_chain(e)


async def get_mcp_client(
    servers: dict[str, dict[str, Any]] | None = None,
    tool_interceptors: list | None = None,
    force_new: bool = False,
) -> NativeMCPClient:
    """Get or initialize the global native MCP client."""

    if tool_interceptors:
        logger.warning("Native MCP client ignores LangChain-style tool_interceptors")

    global _mcp_client
    if force_new:
        logger.info("Creating a new native MCP client instance")
        return _create_mcp_client(servers or DEFAULT_MCP_SERVERS)

    if _mcp_client is None:
        logger.info("Initializing global native MCP client...")
        _mcp_client = _create_mcp_client(servers or DEFAULT_MCP_SERVERS)
        logger.info("Global native MCP client initialized")

    return _mcp_client


async def get_mcp_client_with_retry(
    servers: dict[str, dict[str, Any]] | None = None,
    tool_interceptors: list | None = None,
    force_new: bool = False,
) -> NativeMCPClient:
    """Compatibility wrapper; retry behavior is built into NativeMCPClient."""

    return await get_mcp_client(
        servers=servers,
        tool_interceptors=tool_interceptors,
        force_new=force_new,
    )


def _create_mcp_client(servers: dict[str, dict[str, Any]]) -> NativeMCPClient:
    return NativeMCPClient(servers)


def suggest_mcp_transport(url: str, transport: str) -> str | None:
    """Return a warning when URL and transport are obviously mismatched."""

    lower_url = url.lower()
    normalized_transport = transport.replace("_", "-")
    if "/sse" in lower_url and normalized_transport in ("streamable-http", "http"):
        return (
            f"MCP URL contains /sse/ but transport={transport!r}; "
            "hosted MCP endpoints usually require transport=sse"
        )
    if normalized_transport == "sse" and "/mcp" in lower_url and "/sse" not in lower_url:
        return (
            f"MCP URL looks like a local FastMCP path but transport={transport!r}; "
            "local servers usually use transport=streamable-http"
        )
    return None


def mcp_result_to_text(result: Any) -> str:
    """Convert native MCP CallToolResult objects into text for chat messages."""

    if isinstance(result, mcp_types.CallToolResult):
        if result.structuredContent is not None:
            return json.dumps(result.structuredContent, ensure_ascii=False, default=str)
        return "\n".join(_content_item_to_text(item) for item in result.content)
    return str(result)


def _content_item_to_text(item: Any) -> str:
    if isinstance(item, mcp_types.TextContent):
        return item.text
    if hasattr(item, "model_dump"):
        return json.dumps(item.model_dump(mode="json"), ensure_ascii=False, default=str)
    return str(item)
