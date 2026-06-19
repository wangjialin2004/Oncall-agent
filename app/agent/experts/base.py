"""Reusable streaming tool-calling loop shared by single-domain experts.

Each expert is a focused agent: a scoped tool set + a domain system prompt + the
generic multi-round tool-calling loop implemented here. ``run`` is an async
generator yielding normalized timeline events (``agent_event`` / ``tool_event``)
and ``content`` chunks, so the router can stream them straight to the frontend.

The comprehensive-diagnosis expert does NOT use this loop (it wraps the existing
LangGraph pipeline); see ``diagnosis.py``.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, Sequence
from typing import Any, Protocol

from loguru import logger

from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, LLMClientConfig, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.core.tool_calling import execute_tool_calls, tool_to_definition
from app.agent.events import make_agent_event, make_tool_event

ExpertEvent = dict[str, Any]

# Cap tool-calling rounds so a misbehaving model can't loop forever.
DEFAULT_MAX_TOOL_ROUNDS = 3


def estimate_tokens(text: str) -> int:
    """Cheap, provider-neutral token estimate.

    CJK-heavy operational text averages well under 2 chars/token, but ~2.5 is a
    safe upper bound for budgeting. We only need an order-of-magnitude gate.
    """
    return max(1, int(len(text) / 2.5))


class ExpertAgent(Protocol):
    """Protocol implemented by every expert registered with the router."""

    agent_label: str
    display_name: str

    def run(
        self, *, message: str, session_id: str, trace_id: str, context: str = ""
    ) -> AsyncGenerator[ExpertEvent, None]: ...


def _tool_call_payload(tool_call: ToolCall) -> dict[str, Any]:
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
        },
    }


def _merge_usage(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + int(value)


class ToolCallingExpert:
    """Base class for single-domain experts using the generic tool-calling loop."""

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

    def _new_llm_client(self) -> LLMClient:
        return LLMClient(LLMClientConfig.from_settings(config))

    async def run(
        self, *, message: str, session_id: str, trace_id: str, context: str = ""
    ) -> AsyncGenerator[ExpertEvent, None]:
        span_id = f"{self.agent_label}:{trace_id}"
        started = time.perf_counter()
        usage_total: dict[str, int] = {}

        yield make_agent_event(
            agent=self.agent_label,
            stage="start",
            status="in_progress",
            summary=f"{self.display_name}开始处理",
            trace_id=trace_id,
            span_id=span_id,
        )

        client = self._new_llm_client()
        try:
            tools = await self.get_tools()
            tool_defs = [tool_to_definition(tool) for tool in tools]
            system_content = self.system_prompt
            if context:
                system_content = f"{system_content}\n\n{context}"
            messages: list[ChatMessage] = [
                ChatMessage(role="system", content=system_content),
                ChatMessage(role="user", content=self.build_user_message(message)),
            ]

            answer = ""
            extra_events: list[ExpertEvent] = []
            for round_index in range(self.max_tool_rounds):
                response = await client.complete(
                    messages,
                    tools=tool_defs or None,
                    tool_choice="auto" if tool_defs else None,
                    temperature=self.temperature,
                )
                _merge_usage(usage_total, response.usage)

                if not response.tool_calls:
                    answer = response.content
                    break

                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=[_tool_call_payload(tc) for tc in response.tool_calls],
                    )
                )

                tool_results = await execute_tool_calls(response.tool_calls, tools)
                args_by_id = {tc.id: tc.arguments for tc in response.tool_calls}
                for result in tool_results:
                    tool_started = time.perf_counter()
                    content = result.content
                    try:
                        content = await self.transform_tool_result(
                            tool_name=result.tool_name,
                            content=content,
                            raw=result.raw,
                            events_sink=extra_events,
                            trace_id=trace_id,
                            llm_client=client,
                        )
                    except Exception as exc:  # transform must never break the loop
                        logger.warning(f"{self.agent_label} 结果后处理失败: {exc}")

                    for ev in extra_events:
                        yield ev
                    extra_events.clear()

                    yield make_tool_event(
                        agent=self.agent_label,
                        tool=result.tool_name,
                        status="completed" if result.success else "failed",
                        evidence_id=result.call_id,
                        summary=_summarize(content),
                        payload={"arguments": args_by_id.get(result.call_id, {})},
                        trace_id=trace_id,
                        span_id=f"tool:{result.call_id}",
                        duration_ms=(time.perf_counter() - tool_started) * 1000,
                    )
                    messages.append(
                        ChatMessage(role="tool", content=content, tool_call_id=result.call_id)
                    )
            else:
                # Exhausted rounds without a final answer: ask once more, no tools.
                response = await client.complete(messages, temperature=self.temperature)
                _merge_usage(usage_total, response.usage)
                answer = response.content

            if answer:
                yield {"type": "content", "data": answer, "agent": self.agent_label}

            yield make_agent_event(
                agent=self.agent_label,
                stage="complete",
                status="completed",
                summary=f"{self.display_name}处理完成",
                payload={"answer_chars": len(answer)},
                trace_id=trace_id,
                span_id=span_id,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=usage_total or None,
            )
        except Exception as exc:
            logger.error(f"{self.agent_label} 执行失败: {exc}", exc_info=True)
            yield make_agent_event(
                agent=self.agent_label,
                stage="error",
                status="degraded",
                summary=f"{self.display_name}执行出错：{exc}",
                payload={"error": str(exc)},
                trace_id=trace_id,
                span_id=span_id,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=usage_total or None,
            )
            yield {
                "type": "content",
                "data": f"抱歉，{self.display_name}在处理时出现问题：{exc}。请稍后重试或补充信息。",
                "agent": self.agent_label,
            }
        finally:
            await client.aclose()


def _summarize(text: str, limit: int = 300) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:limit]


async def collect_tools(
    local_tools: Sequence[RuntimeTool],
    *,
    mcp_server: str | Sequence[str] | None = None,
) -> list[RuntimeTool]:
    """Combine scoped local tools with one or more MCP servers' tools (best-effort).

    ``mcp_server`` may be a single server name or a sequence of names (the
    comprehensive-diagnosis expert loads both ``monitor`` and ``cls``). Each
    server is loaded independently so one failure never blocks the others.
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

    for server in servers:
        try:
            mcp_tools = await client.get_tools(server_name=server)
            tools.extend(mcp_tools)
            logger.info(f"专家加载 MCP[{server}] 工具 {len(mcp_tools)} 个")
        except Exception as exc:
            logger.warning(
                f"加载 MCP[{server}] 工具失败，跳过该 server 继续: {format_exception_chain(exc)}"
            )
    return tools
