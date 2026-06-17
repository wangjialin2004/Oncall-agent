"""RAG Agent service backed by the application-owned LLM client."""

from collections.abc import AsyncGenerator, Sequence
import json
from typing import Annotated, Any

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from loguru import logger
from typing_extensions import TypedDict

from app.agent.mcp_client import (
    format_exception_chain,
    get_mcp_client_with_retry,
    load_mcp_tools_safe,
    suggest_mcp_transport,
)
from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, LLMClientConfig, ToolCall
from app.core.tool_calling import execute_tool_calls, tool_result_messages, tool_to_definition
from app.services.checkpoint_service import (
    aclose_checkpointer,
    close_checkpointer,
    create_sqlite_checkpointer,
    create_sync_sqlite_checkpointer,
    setup_checkpointer,
)
from app.tools import DEFAULT_LOCAL_AGENT_TOOLS


def _tool_call_payload(tool_call: ToolCall) -> dict[str, Any]:
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
        },
    }

class AgentState(TypedDict):
    """Agent 状态"""
    messages: Annotated[Sequence[BaseMessage], add_messages]


def trim_messages_middleware(state: AgentState) -> dict[str, Any] | None:
    """
    修剪消息历史，只保留最近的几条消息以适应上下文窗口

    策略：
    - 保留第一条系统消息（System Message）
    - 保留最近的 6 条消息（3 轮对话）
    - 当消息少于等于 7 条时，不做修剪

    Args:
        state: Agent 状态

    Returns:
        包含修剪后消息的字典，如果无需修剪则返回 None
    """
    messages = state["messages"]

    # 如果消息数量较少，无需修剪
    if len(messages) <= 7:
        return None

    # 提取第一条系统消息
    first_msg = messages[0]

    # 保留最近的 6 条消息（确保包含完整的对话轮次）
    recent_messages = messages[-6:] if len(messages) % 2 == 0 else messages[-7:]

    # 构建新的消息列表
    new_messages = [first_msg] + list(recent_messages)

    logger.debug(f"修剪消息历史: {len(messages)} -> {len(new_messages)} 条")

    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *new_messages
        ]
    }


class RagAgentService:
    """RAG Agent service backed by the application LLM client."""

    def __init__(
        self,
        streaming: bool = True,
        checkpoint_db_path: str | None = None,
        checkpointer: Any | None = None,
        llm_client: Any | None = None,
    ):
        """初始化 RAG Agent 服务

        Args:
            streaming: 是否启用流式输出，默认为 True
        """
        self.model_name = config.rag_model
        self.streaming = streaming
        self.system_prompt = self._build_system_prompt()


        self.llm_client = llm_client
        self.model = None

        # 定义基础工具（与 AIOps Planner/Executor 使用同一套默认本地工具）
        self.tools = list(DEFAULT_LOCAL_AGENT_TOOLS)

        # MCP 客户端（延迟初始化，使用全局管理）
        self.mcp_tools: list = []

        # SQLite 检查点在异步入口中懒加载，避免模块导入阶段没有 running loop。
        self.checkpoint_db_path = checkpoint_db_path or config.checkpoint_db_path
        self.checkpointer = checkpointer

        # Agent 初始化（会在异步方法中完成）
        self.agent = None
        self._agent_initialized = False

        logger.info(
            f"RAG Agent service initialized, model={self.model_name}, streaming={streaming}"
        )

    @staticmethod
    def _thread_id(session_id: str) -> str:
        return f"rag:{session_id}"

    async def _initialize_agent(self):
        """异步初始化 Agent（包括 MCP 工具）"""
        if self._agent_initialized:
            return
        if self.checkpointer is None:
            self.checkpointer = create_sqlite_checkpointer(self.checkpoint_db_path)
        await setup_checkpointer(self.checkpointer)

        for name, server in config.mcp_servers.items():
            hint = suggest_mcp_transport(
                str(server.get("url", "")),
                str(server.get("transport", "")),
            )
            if hint:
                logger.warning(f"MCP 配置 [{name}]: {hint}")

        mcp_client = await get_mcp_client_with_retry()
        mcp_tools, mcp_err = await load_mcp_tools_safe(mcp_client)
        if mcp_err:
            logger.warning(
                f"MCP 工具加载失败，将仅使用本地工具继续运行:\n{mcp_err}"
            )
            self.mcp_tools = []
        else:
            self.mcp_tools = mcp_tools
            logger.info(f"成功加载 {len(mcp_tools)} 个 MCP 工具")

        self._agent_initialized = True

        all_tools = self.tools + self.mcp_tools

        if all_tools:
            tool_names = [tool.name if hasattr(tool, "name") else str(tool) for tool in all_tools]
            logger.info(f"可用工具列表: {', '.join(tool_names)}")

    def _build_system_prompt(self) -> str:
        """
        构建系统提示词

        注意：LangChain 框架会自动将工具信息传递给 LLM，
        因此系统提示词中无需列举具体的工具列表。

        Returns:
            str: 系统提示词
        """
        from textwrap import dedent

        return dedent("""
            你是一个专业的AI助手，能够使用多种工具来帮助用户解决问题。

            工作原则:
            1. 理解用户需求，选择合适的工具来完成任务
            2. 当需要获取实时信息或专业知识时，主动使用相关工具
            3. 基于工具返回的结果提供准确、专业的回答
            4. 如果工具无法提供足够信息，请诚实地告知用户

            回答要求:
            - 保持友好、专业的语气
            - 回答简洁明了，重点突出
            - 基于事实，不编造信息
            - 如有不确定的地方，明确说明

            请根据用户的问题，灵活使用可用工具，提供高质量的帮助。
        """).strip()

    async def _complete_with_tools(self, question: str) -> str:
        if self.llm_client is None:
            self.llm_client = LLMClient(LLMClientConfig.from_settings(config))
        llm_client = self.llm_client
        messages = [
            ChatMessage(role="system", content=self.system_prompt),
            ChatMessage(role="user", content=question),
        ]
        tools = self.tools + self.mcp_tools

        response = await llm_client.complete(
            messages,
            temperature=0.7,
            tools=[tool_to_definition(tool) for tool in tools],
            tool_choice="auto" if tools else None,
        )
        if not response.tool_calls:
            return response.content

        messages.append(
            ChatMessage(
                role="assistant",
                content=response.content,
                tool_calls=[_tool_call_payload(tool_call) for tool_call in response.tool_calls],
            )
        )
        tool_results = await execute_tool_calls(response.tool_calls, tools)
        messages.extend(tool_result_messages(tool_results))
        final_response = await llm_client.complete(messages, temperature=0.7)
        return final_response.content

    async def query(
        self,
        question: str,
        session_id: str,
    ) -> str:
        """
        非流式处理用户问题（一次性返回完整答案）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）

        Returns:
            str: 完整答案
        """
        try:
            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（非流式）: {question}")

            answer = await self._complete_with_tools(question)
            logger.info(f"[会话 {session_id}] RAG Agent 查询完成（非流式）")
            return answer

        except Exception as e:
            logger.error(
                f"[会话 {session_id}] RAG Agent 查询失败（非流式）: "
                f"{format_exception_chain(e)}"
            )
            raise

    async def query_stream(
        self,
        question: str,
        session_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        流式处理用户问题（逐步返回答案片段）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）

        Yields:
            Dict[str, Any]: 包含流式数据的字典
                - type: "content" | "tool_call" | "complete" | "error"
                - data: 具体内容
        """
        try:
            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（流式）: {question}")

            answer = await self._complete_with_tools(question)
            if answer:
                yield {
                    "type": "content",
                    "data": answer,
                    "node": "llm",
                }

            logger.info(f"[会话 {session_id}] RAG Agent 查询完成（流式）")
            yield {"type": "complete"}

        except Exception as e:
            detail = format_exception_chain(e)
            logger.error(
                f"[会话 {session_id}] RAG Agent 查询失败（流式）: {detail}"
            )
            yield {"type": "error", "data": detail}

    def get_session_history(self, session_id: str) -> list:
        """
        获取会话历史（从 MemorySaver checkpointer 中读取）

        Args:
            session_id: 会话ID（即 thread_id）

        Returns:
            list: 消息历史列表 [{"role": "user|assistant", "content": "...", "timestamp": "..."}]
        """
        try:
            # 使用 checkpointer 的 get 方法获取最新的检查点
            config = {"configurable": {"thread_id": self._thread_id(session_id)}}

            # 获取该 thread 的最新检查点
            reader = create_sync_sqlite_checkpointer(self.checkpoint_db_path)
            try:
                checkpoint_tuple = reader.get(config)
            finally:
                close_checkpointer(reader)

            if not checkpoint_tuple:
                logger.info(f"获取会话历史: {session_id}, 消息数量: 0")
                return []

            # checkpoint_tuple 可能是命名元组或普通元组，安全地提取 checkpoint
            # 通常第一个元素是 checkpoint 数据
            if hasattr(checkpoint_tuple, 'checkpoint'):
                checkpoint_data = checkpoint_tuple.checkpoint  # type: ignore
            else:
                # 如果是普通元组，第一个元素是 checkpoint
                checkpoint_data = checkpoint_tuple[0] if checkpoint_tuple else {}

            # 从检查点中提取消息
            messages = checkpoint_data.get("channel_values", {}).get("messages", [])

            # 转换为前端需要的格式
            history = []
            for msg in messages:
                # 跳过系统消息
                if isinstance(msg, SystemMessage):
                    continue

                role = "user" if isinstance(msg, HumanMessage) else "assistant"
                content = msg.content if hasattr(msg, 'content') else str(msg)

                # 提取时间戳（如果有的话）
                timestamp = getattr(msg, 'timestamp', None)
                if timestamp:
                    history.append({
                        "role": role,
                        "content": content,
                        "timestamp": timestamp
                    })
                else:
                    from datetime import datetime
                    history.append({
                        "role": role,
                        "content": content,
                        "timestamp": datetime.now().isoformat()
                    })

            logger.info(f"获取会话历史: {session_id}, 消息数量: {len(history)}")
            return history

        except Exception as e:
            logger.error(f"获取会话历史失败: {session_id}, 错误: {e}")
            return []

    def clear_session(self, session_id: str) -> bool:
        """
        清空会话历史（从 MemorySaver checkpointer 中删除）

        Args:
            session_id: 会话ID（即 thread_id）

        Returns:
            bool: 是否成功
        """
        try:
            # 使用 checkpointer 的 delete_thread 方法删除该 thread 的所有检查点
            writer = create_sync_sqlite_checkpointer(self.checkpoint_db_path)
            try:
                writer.delete_thread(self._thread_id(session_id))
            finally:
                close_checkpointer(writer)

            logger.info(f"已清除会话历史: {session_id}")
            return True

        except Exception as e:
            logger.error(f"清空会话历史失败: {session_id}, 错误: {e}")
            return False

    async def cleanup(self):
        """清理资源"""
        try:
            logger.info("清理 RAG Agent 服务资源...")
            # MCP 客户端由全局管理器统一管理，无需手动清理
            if self.checkpointer is not None:
                await aclose_checkpointer(self.checkpointer)
            logger.info("RAG Agent 服务资源已清理")
        except Exception as e:
            logger.error(f"清理资源失败: {e}")


# 全局单例 - 启用流式输出
rag_agent_service = RagAgentService(streaming=True)
