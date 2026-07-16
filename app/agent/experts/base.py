"""Reusable streaming tool-calling loop shared by single-domain experts.

Each expert is a focused agent: a scoped tool set + a domain system prompt.
``run`` delegates to the shared sub-harness kernel
(``app.agent.harness.sub_harness``) so experts and harness delegation share one
multi-round model→tools implementation (M2 W7 / WP-B2).

The comprehensive-diagnosis expert does NOT bypass this loop; see ``diagnosis.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from typing import Any, Protocol

from loguru import logger

from app.config import config
from app.core.llm_client import LLMClient, get_default_llm_client
from app.core.runtime_tools import RuntimeTool

ExpertEvent = dict[str, Any]

# Cap tool-calling rounds so a misbehaving model can't loop forever.
DEFAULT_MAX_TOOL_ROUNDS = 3


class ExpertAgent(Protocol):
    """Protocol implemented by every expert registered with the router."""

    agent_label: str
    display_name: str

    def run(
        self,
        *,
        message: str,
        session_id: str,
        trace_id: str,
        context: str = "",
        max_tool_rounds: int | None = None,
        close_after_tools: bool = False,
    ) -> AsyncGenerator[ExpertEvent, None]: ...


class ToolCallingExpert:
    """Base class for single-domain experts using the shared sub-harness kernel."""

    agent_label: str = "expert"
    display_name: str = "专家"
    system_prompt: str = ""
    temperature: float = 0.3
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS

    async def get_tools(self) -> list[RuntimeTool]:
        """Return the scoped tool set for this expert. Subclasses override."""
        return []

    async def transform_tool_result(
        self,
        *,
        tool_name: str,
        content: str,
        raw: Any,
        events_sink: list[ExpertEvent],
        trace_id: str,
        llm_client: LLMClient,
    ) -> str:
        """Hook to post-process a tool result before it re-enters the LLM context.

        Default is a no-op. The log expert overrides this to run the large-log
        pipeline (filter → cluster → map-reduce summarize) so tens of thousands of
        lines never hit the model context. ``events_sink`` lets the hook emit extra
        timeline events.
        """
        return content

    def build_user_message(self, message: str) -> str:
        return message

    async def _new_llm_client(self) -> LLMClient:
        """Return the shared process-level LLM client (avoids repeated TLS handshakes).

        Tests can still inject a custom client via a subclass override.
        """
        return await get_default_llm_client()

    def _make_result_processor(self, *, client: LLMClient, trace_id: str):
        """Adapt ``transform_tool_result`` to the shared kernel's result processor."""

        async def process(result: Any):
            content = result.content
            extra: list[ExpertEvent] = []
            try:
                content = await self.transform_tool_result(
                    tool_name=result.tool_name,
                    content=content,
                    raw=result.raw,
                    events_sink=extra,
                    trace_id=trace_id,
                    llm_client=client,
                )
            except Exception as exc:  # transform must never break the loop
                logger.warning(f"{self.agent_label} 结果后处理失败: {exc}")
            return content, extra, []

        return process

    async def run(
        self,
        *,
        message: str,
        session_id: str,
        trace_id: str,
        context: str = "",
        max_tool_rounds: int | None = None,
        close_after_tools: bool = False,
    ) -> AsyncGenerator[ExpertEvent, None]:
        """Run via shared ``run_sub_harness`` (M2 W7).

        ``HARNESS_SHARED_KERNEL_DELEGATION`` is observational: the loop body always
        lives in ``sub_harness`` so there is a single implementation to maintain.
        Setting the flag false only tags events via the kernel payload helper.

        Import of ``sub_harness`` is deferred to avoid circular import through
        ``harness.loop`` → ``experts.registry`` → ``experts.base``.
        """
        from app.agent.harness.sub_harness import SubHarnessConfig, run_sub_harness

        client = await self._new_llm_client()
        tools = await self.get_tools()
        round_limit = max(1, int(max_tool_rounds or self.max_tool_rounds))
        # Flag kept for ops rollback documentation; body is always shared kernel.
        _ = bool(getattr(config, "harness_shared_kernel_delegation", True))
        cfg = SubHarnessConfig(
            agent_label=self.agent_label,
            display_name=self.display_name,
            system_prompt=self.system_prompt,
            tools=tools,
            max_steps=round_limit,
            temperature=self.temperature,
            close_after_tools=close_after_tools,
            process_result=self._make_result_processor(client=client, trace_id=trace_id),
        )
        async for event in run_sub_harness(
            cfg,
            message=self.build_user_message(message),
            session_id=session_id,
            trace_id=trace_id,
            context=context,
            llm_client=client,
        ):
            yield event


async def collect_tools(
    local_tools: Sequence[RuntimeTool],
    *,
    mcp_server: str | Sequence[str] | None = None,
) -> list[RuntimeTool]:
    """Combine scoped local tools with one or more MCP servers' tools (best-effort).

    ``mcp_server`` may be a single server name or a sequence of names (the
    comprehensive-diagnosis expert loads both ``monitor`` and ``cls``). Each
    server is loaded independently so one failure never blocks the others.
    Servers are loaded in parallel via ``asyncio.gather`` so the cumulative
    cold-start cost is the slowest single server, not the sum of all servers.
    """
    tools = list(local_tools)
    if not mcp_server:
        return tools

    servers = [mcp_server] if isinstance(mcp_server, str) else list(mcp_server)
    try:
        from app.agent.mcp_client import format_exception_chain, get_mcp_client_with_retry

        client = await get_mcp_client_with_retry()
    except Exception as exc:
        logger.warning(f"加载 MCP 客户端失败，仅用本地工具继续: {format_exception_chain(exc)}")
        return tools

    async def _load(server: str) -> tuple[str, list[RuntimeTool]]:
        try:
            return server, await client.get_tools(server_name=server)
        except Exception as exc:
            logger.warning(
                f"加载 MCP[{server}] 工具失败，跳过该 server 继续: {format_exception_chain(exc)}"
            )
            return server, []

    results = await asyncio.gather(*[_load(server) for server in servers])
    for server, mcp_tools in results:
        if mcp_tools:
            tools.extend(mcp_tools)
            logger.info(f"专家加载 MCP[{server}] 工具 {len(mcp_tools)} 个")
    return tools
