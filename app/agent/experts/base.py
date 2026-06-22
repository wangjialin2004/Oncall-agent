"""Reusable streaming tool-calling loop shared by single-domain experts.

Each expert is a focused agent: a scoped tool set + a domain system prompt + the
generic multi-round tool-calling loop implemented here. ``run`` is an async
generator yielding normalized timeline events (``agent_event`` / ``tool_event``)
and ``content`` chunks, so the router can stream them straight to the frontend.

The loop's primitives (token/usage helpers, the guarded tool executor, the
per-result event streaming) live in the shared kernel ``app.agent.agent_loop``,
which the unified harness uses too — experts and harness share one kernel.

The comprehensive-diagnosis expert does NOT bypass this loop; see ``diagnosis.py``.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Sequence
from typing import Any, Protocol

from loguru import logger

from app.agent.agent_loop import (
    GuardedToolExecutor,
    merge_usage,
    stream_tool_results,
    tool_call_payload,
)
from app.agent.events import make_agent_event
from app.core.llm_client import ChatMessage, LLMClient, new_llm_client
from app.core.runtime_tools import RuntimeTool
from app.core.tool_calling import tool_to_definition

ExpertEvent = dict[str, Any]

# Cap tool-calling rounds so a misbehaving model can't loop forever.
DEFAULT_MAX_TOOL_ROUNDS = 3


class ExpertAgent(Protocol):
    """Protocol implemented by every expert registered with the router."""

    agent_label: str
    display_name: str

    def run(
        self, *, message: str, session_id: str, trace_id: str, context: str = ""
    ) -> AsyncGenerator[ExpertEvent, None]: ...


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
        return new_llm_client()

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
        # max_output_chars=0: keep raw tool output so transform_tool_result hooks
        # (e.g. the log pipeline) see full content instead of a truncated prefix.
        executor = GuardedToolExecutor(max_output_chars=0)
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
            answer_streamed = False
            for _round_index in range(self.max_tool_rounds):
                if not tool_defs:
                    async for chunk in self._stream_final_answer(
                        client=client,
                        messages=messages,
                        temperature=self.temperature,
                    ):
                        answer += chunk
                        yield {"type": "content", "data": chunk, "agent": self.agent_label}
                    answer_streamed = True
                    break

                response = None
                async for event in self._stream_chat_turn(
                    client=client,
                    messages=messages,
                    tools=tool_defs or None,
                    tool_choice="auto" if tool_defs else None,
                    temperature=self.temperature,
                ):
                    chunk = event.get("content")
                    if chunk:
                        answer += str(chunk)
                        answer_streamed = True
                        yield {"type": "content", "data": str(chunk), "agent": self.agent_label}
                    if event.get("response") is not None:
                        response = event["response"]
                if response is None:
                    break
                merge_usage(usage_total, response.usage)

                if not response.tool_calls:
                    if not answer:
                        answer = response.content
                    break

                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=[tool_call_payload(tc) for tc in response.tool_calls],
                    )
                )

                tool_results = await executor.execute(response.tool_calls, tools)
                args_by_id = {tc.id: tc.arguments for tc in response.tool_calls}
                async for event in stream_tool_results(
                    tool_results,
                    messages=messages,
                    agent_label=self.agent_label,
                    trace_id=trace_id,
                    args_by_id=args_by_id,
                    process_result=self._make_result_processor(client=client, trace_id=trace_id),
                    measure_duration=True,
                ):
                    yield event
            else:
                # Exhausted rounds without a final answer: ask once more, no tools.
                async for chunk in self._stream_final_answer(
                    client=client,
                    messages=messages,
                    temperature=self.temperature,
                ):
                    answer += chunk
                    yield {"type": "content", "data": chunk, "agent": self.agent_label}
                answer_streamed = True

            if answer and not answer_streamed:
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

    async def _stream_final_answer(
        self,
        *,
        client: Any,
        messages: list[ChatMessage],
        temperature: float,
    ) -> AsyncGenerator[str, None]:
        stream_complete = getattr(client, "stream_complete", None)
        if stream_complete is None:
            response = await client.complete(messages, temperature=temperature)
            yield response.content
            return
        async for chunk in stream_complete(messages, temperature=temperature):
            yield str(chunk)

    async def _stream_chat_turn(
        self,
        *,
        client: Any,
        messages: list[ChatMessage],
        temperature: float,
        tools: list[Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        stream_chat = getattr(client, "stream_chat", None)
        if stream_chat is None:
            response = await client.complete(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
            )
            yield {"response": response}
            return
        async for event in stream_chat(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
        ):
            content = str(getattr(event, "content", "") or "")
            if content:
                yield {"content": content}
            response = getattr(event, "response", None)
            if response is not None:
                yield {"response": response}


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
