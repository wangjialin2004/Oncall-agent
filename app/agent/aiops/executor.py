"""
Executor 节点：执行单个步骤
使用 LLMClient 实现，不依赖特定 LLM 提供商
"""

import json
from typing import Any

from loguru import logger

from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, LLMClientConfig, ToolCall
from app.core.tool_calling import execute_tool_calls, tool_result_messages, tool_to_definition
from app.services.diagnosis_memory_service import diagnosis_memory_service
from app.tools import DEFAULT_LOCAL_AGENT_TOOLS

from .evidence import append_evidence_summary
from .events import make_tool_event
from .plan_utils import plan_step_text, pop_next_plan_step
from .state import OnCallState

_EXECUTOR_SYSTEM_PROMPT = """你是一个能力强大的助手，负责执行具体的任务步骤。

你可以使用各种工具来完成任务。对于每个步骤：
1. 理解步骤的目标
2. 选择合适的工具，如果已经指定了工具，则使用指定的工具
3. 调用工具获取信息
4. 返回执行结果

注意：
- 如果工具调用失败，请说明失败原因
- 不要编造数据，只返回实际获取的信息
- 执行结果要清晰、准确
- 专注于当前步骤，不要考虑其他任务"""


def route_after_executor(state: dict[str, Any]) -> str:
    """计划仍有剩余步骤则回到 Executor 继续取证，否则进入诊断。"""
    return "executor" if state.get("plan") else "diagnosis"


def build_step_task_message(state: dict[str, Any], step: Any) -> str:
    """构造执行步骤的提示，注入 incident、步骤元数据与已收集证据。"""
    lines = [f"请执行以下任务: {plan_step_text(step)}"]

    incident = state.get("incident") or {}
    if isinstance(incident, dict) and incident:
        service = incident.get("service_name") or "未知"
        incident_type = incident.get("incident_type") or "未知"
        lines.append(f"\n## 故障背景\n- 受影响服务：{service}\n- 故障类型：{incident_type}")
        symptoms = [str(item) for item in incident.get("symptoms") or [] if item]
        if symptoms:
            lines.append("- 症状：" + "；".join(symptoms[:5]))

    if isinstance(step, dict):
        tool_category = str(step.get("tool_category") or "").strip()
        expected_evidence = str(step.get("expected_evidence") or "").strip()
        if tool_category and tool_category != "unknown":
            lines.append(f"\n## 建议工具类别\n{tool_category}")
        if expected_evidence:
            lines.append(f"\n## 期望产出证据\n{expected_evidence}")

    collected = [
        str(item.get("summary") or "").strip()
        for item in state.get("evidence") or []
        if isinstance(item, dict) and item.get("summary")
    ]
    if collected:
        lines.append(
            "\n## 已收集证据（避免重复获取）\n"
            + "\n".join(f"- {item}" for item in collected[:8])
        )

    return "\n".join(lines)


def _step_id(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("step_id") or step.get("description") or "step")
    return str(step)


def build_success_step_update(
    *,
    state: dict[str, Any],
    step: Any,
    remaining_plan: list[Any],
    result: str,
    evidence_records: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_items = []
    events = list(state.get("events", []))
    for record in evidence_records:
        evidence = {
            "step_id": _step_id(step),
            "tool_name": record.get("tool_name", ""),
            "evidence_id": record.get("evidence_id", ""),
            "status": "completed" if record.get("success", True) else "failed",
            "summary": record.get("summary", ""),
            "source": record.get("source", ""),
        }
        evidence_items.append(evidence)
        events.append(
            make_tool_event(
                agent="evidence_collector",
                tool=evidence["tool_name"],
                status=evidence["status"],
                evidence_id=evidence["evidence_id"],
                summary=evidence["summary"],
                payload=evidence,
            )
        )

    if not evidence_items:
        evidence_items.append(
            {
                "step_id": _step_id(step),
                "tool_name": "",
                "evidence_id": "",
                "status": "completed",
                "summary": result,
                "source": "llm",
            }
        )

    return {
        "plan": remaining_plan,
        "past_steps": [
            {
                "step_id": _step_id(step),
                "description": plan_step_text(step),
                "status": "completed",
                "result": result,
            }
        ],
        "evidence": evidence_items,
        "events": events,
    }


def build_failed_step_update(
    *,
    state: dict[str, Any],
    step: Any,
    remaining_plan: list[Any],
    error: Exception,
) -> dict[str, Any]:
    summary = f"步骤执行失败：{error}"
    evidence = {
        "step_id": _step_id(step),
        "tool_name": "",
        "evidence_id": "",
        "status": "failed",
        "summary": summary,
        "source": "executor",
    }
    event = make_tool_event(
        agent="evidence_collector",
        tool="",
        status="failed",
        evidence_id="",
        summary=summary,
        payload=evidence,
    )
    return {
        "plan": remaining_plan,
        "past_steps": [
            {
                "step_id": _step_id(step),
                "description": plan_step_text(step),
                "status": "failed",
                "result": summary,
            }
        ],
        "evidence": [evidence],
        "events": list(state.get("events", [])) + [event],
    }


def _tool_call_payload(tool_call: ToolCall) -> dict[str, Any]:
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
        },
    }


async def execute_step_with_tools(
    *,
    state: dict[str, Any],
    task: Any,
    tools: list[Any],
    llm_client: Any,
) -> tuple[str, list[dict[str, Any]]]:
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=_EXECUTOR_SYSTEM_PROMPT),
        ChatMessage(role="user", content=build_step_task_message(state, task)),
    ]

    response = await llm_client.complete(
        messages,
        tools=[tool_to_definition(tool) for tool in tools],
        tool_choice="auto",
        temperature=0,
    )
    logger.info(f"LLM 响应，工具调用数：{len(response.tool_calls)}")
    if not response.tool_calls:
        return response.content, []

    messages.append(
        ChatMessage(
            role="assistant",
            content=response.content,
            tool_calls=[_tool_call_payload(tool_call) for tool_call in response.tool_calls],
        )
    )

    tool_results = await execute_tool_calls(response.tool_calls, tools)
    messages.extend(tool_result_messages(tool_results))

    final_response = await llm_client.complete(messages, temperature=0)
    arguments_by_id = {tool_call.id: tool_call.arguments for tool_call in response.tool_calls}
    evidence_records = [
        {
            "tool_name": result.tool_name,
            "tool_call_id": result.call_id,
            "evidence_id": result.call_id,
            "source": "tool_call",
            "success": result.success,
            "summary": result.content[:300],
            "arguments": arguments_by_id.get(result.call_id, {}),
            "raw_result": result.content,
        }
        for result in tool_results
    ]
    return final_response.content, evidence_records


async def executor(state: OnCallState) -> dict[str, Any]:
    """执行节点：执行计划中的下一个步骤。"""
    logger.info("=== Executor：执行步骤 ===")

    plan = state.get("plan", [])
    if not plan:
        logger.info("计划为空，跳过执行")
        return {}

    task, remaining_plan = pop_next_plan_step(plan)
    if task is None:
        logger.info("Plan is empty; executor skipped")
        return {}
    logger.info(f"当前任务: {task}")

    try:
        # 收集本地工具和 MCP 工具
        local_tools = list(DEFAULT_LOCAL_AGENT_TOOLS)
        mcp_client = await get_mcp_client_with_retry()
        mcp_tools = await mcp_client.get_tools()
        all_tools = local_tools + mcp_tools
        logger.info(f"可用工具数量: 本地 {len(local_tools)} + MCP {len(mcp_tools)}")

        client = LLMClient(LLMClientConfig.from_settings(config))
        try:
            result, evidence_records = await execute_step_with_tools(
                state=state,
                task=task,
                tools=all_tools,
                llm_client=client,
            )
        finally:
            await client.aclose()

        # 将工具证据摘要追加到步骤结果
        result = append_evidence_summary(result, evidence_records)
        logger.info(f"步骤执行完成，结果长度: {len(result)}")

        # 持久化证据记录
        if state.get("case_id") and evidence_records:
            diagnosis_memory_service.record_tool_evidence(
                case_id=state["case_id"],
                session_id=state.get("session_id", "default"),
                evidence_records=evidence_records,
            )

        return build_success_step_update(
            state=state,
            step=task,
            remaining_plan=remaining_plan,
            result=result,
            evidence_records=evidence_records,
        )

    except Exception as e:
        logger.error(f"执行步骤失败: {e}", exc_info=True)
        return build_failed_step_update(
            state=state,
            step=task,
            remaining_plan=remaining_plan,
            error=e,
        )
