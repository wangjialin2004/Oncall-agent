

import json
from textwrap import dedent
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, LLMClientConfig
from app.services.experience_memory_service import experience_memory_service
from app.tools import DEFAULT_LOCAL_AGENT_TOOLS, retrieve_knowledge

from .events import make_agent_event
from .plan_utils import normalize_plan_steps
from .state import OnCallState
from .utils import format_tools_description


class Plan(BaseModel):
    """计划的输出格式"""
    steps: list[str] = Field(
        description="完成任务所需的不同步骤。这些步骤应该按顺序执行，每一步都建立在前一步的基础上。"
    )


# Planner 提示词
def load_experience_context(input_text: str) -> str:
    try:
        experiences = experience_memory_service.search_relevant_experiences(
            query=input_text,
            project_id=config.project_id,
            top_k=config.experience_memory_top_k,
        )
    except Exception as exc:
        logger.warning(f"experience memory recall failed: {exc}")
        return ""
    return format_experience_context(experiences)


def format_experience_context(experiences: list[dict[str, Any]]) -> str:
    if not experiences:
        return ""

    sections = [
        "## 相关历史经验",
        "",
        "历史经验仅供参考，不代表当前事实。",
        "若相似度与置信度均较高，优先验证历史根因；验证失败则继续正常排查。",
        "",
    ]
    for item in experiences:
        sections.extend(
            [
                f"[{item['experience_id']}]",
                f"相似度：{item.get('similarity', 0):.2f}",
                f"置信度：{item.get('confidence', 0):.2f}",
                f"历史症状：{item['symptoms']}",
                f"已验证根因：{item['root_cause']}",
                f"有效处置方案：{item['resolution']}",
                f"关键证据：{item['evidence_summary']}",
                f"来源案例：{', '.join(item.get('source_case_ids', []))}",
                "",
            ]
        )
    return "\n".join(sections).strip()


def format_diagnosis_feedback(
    diagnosis: dict[str, Any] | None,
    evidence: list[dict[str, Any]] | None,
) -> str:
    """再规划时，把诊断反馈渲染成定向补证据的提示。

    首轮（无诊断结论）返回空串，规划行为不变；只有诊断判定证据不足并要求回到
    Planner 时，才注入 next_focus / missing_evidence 与已收集证据摘要，引导只补缺口。
    """

    if not diagnosis:
        return ""

    next_focus = str(diagnosis.get("next_focus") or "").strip()
    missing_evidence = [str(item) for item in diagnosis.get("missing_evidence") or [] if item]
    if not next_focus and not missing_evidence:
        return ""

    lines = [
        "## 上一轮诊断反馈（用于本轮定向补证据）",
        "",
        "上一轮已取证但诊断判定证据不足。请**只规划填补下列缺口的步骤**，",
        "不要重复已完成的取证，避免生成与此前相同的步骤。",
        "",
    ]
    if next_focus:
        lines.append(f"- 下一步重点：{next_focus}")
    if missing_evidence:
        lines.append("- 缺失证据：")
        lines.extend(f"  - {item}" for item in missing_evidence)

    collected = [str(item.get("summary") or "").strip() for item in evidence or []]
    collected = [item for item in collected if item]
    if collected:
        lines.append("- 已收集证据摘要（不要重复获取）：")
        lines.extend(f"  - {item}" for item in collected[:8])

    return "\n".join(lines).strip()


def _parse_plan_response(content: str) -> Plan:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or start > end:
            raise
        payload = json.loads(text[start : end + 1])

    return Plan.model_validate(payload)


def _build_planner_system_prompt(
    tools_description: str,
    experience_context: str,
    diagnosis_feedback: str,
) -> str:
    return dedent(f"""
        你是经验丰富的 OnCall 规划智能体。请将故障拆解为有序、可执行的排查步骤。

        可用工具：
        {tools_description}

        {experience_context}

        {diagnosis_feedback}

        只返回如下格式的紧凑 JSON，不要包含其他内容：
        {{"steps":["第一步","第二步"]}}

        每个步骤应具体、有序，并在适当时指明可用工具或期望证据。不要执行工具，只生成计划。
    """).strip()


async def generate_plan_steps(
    *,
    input_text: str,
    tools_description: str,
    experience_context: str,
    diagnosis_feedback: str,
    llm_client: Any | None = None,
) -> list[str]:
    owns_client = llm_client is None
    client = llm_client or LLMClient(LLMClientConfig.from_settings(config))
    try:
        response = await client.complete(
            [
                ChatMessage(
                    role="system",
                    content=_build_planner_system_prompt(
                        tools_description,
                        experience_context,
                        diagnosis_feedback,
                    ),
                ),
                ChatMessage(role="user", content=input_text),
            ],
            temperature=0,
        )
    finally:
        if owns_client:
            await client.aclose()
    return _parse_plan_response(response.content).steps


async def planner(state: OnCallState) -> dict[str, Any]:
    """
    规划节点：根据用户输入生成执行计划

    流程：
    1. 先查询内部文档，获取相关经验和最佳实践
    2. 若为再规划（诊断判定证据不足后回到本节点），消费诊断反馈做定向补证据
    3. 基于经验文档、诊断反馈和可用工具制定执行计划
    """
    logger.info("=== Planner：制定执行计划 ===")

    input_text = state.get("input", "")
    logger.info(f"用户输入: {input_text}")

    # 再规划时消费上一轮诊断反馈（首轮为空，行为不变）
    diagnosis_feedback = format_diagnosis_feedback(
        state.get("diagnosis"), state.get("evidence")
    )
    if diagnosis_feedback:
        logger.info(f"再规划：消费诊断反馈（iteration={state.get('iteration', 0)}）")

    try:
        # 步骤1: 查询内部文档获取相关经验
        logger.info("查询内部文档，寻找相关经验...")
        memory_context = load_experience_context(input_text)
        experience_docs = ""
        try:
            # retrieve_knowledge 使用 response_format="content_and_artifact"
            # ainvoke() 只返回 content（字符串），不是元组
            context_str = await retrieve_knowledge.ainvoke({"query": input_text})
            if context_str and context_str.strip():
                experience_docs = context_str
                logger.info(f"找到相关经验文档，长度: {len(experience_docs)}")
            else:
                logger.info("未找到相关经验文档")
        except Exception as e:
            logger.warning(f"查询内部文档失败: {e}")

        # 步骤2: 获取可用工具列表
        # 获取本地工具
        local_tools = list(DEFAULT_LOCAL_AGENT_TOOLS)

        # 获取 MCP 工具
        mcp_client = await get_mcp_client_with_retry()
        mcp_tools = await mcp_client.get_tools()

        # 合并所有工具
        all_tools = local_tools + mcp_tools
        logger.info(f"可用工具数量: 本地 {len(local_tools)} + MCP {len(mcp_tools)}")

        # 格式化工具描述
        tools_description = format_tools_description(all_tools)

        # 步骤3: 格式化经验文档上下文
        if experience_docs:
            experience_context = dedent(f"""
                ## 相关经验文档

                以下是从知识库中检索到的相关经验和最佳实践，请参考这些经验制定执行计划：

                {experience_docs}

                ---
            """).strip()
        else:
            experience_context = ""
        if memory_context:
            context_blocks = [memory_context]
            if experience_context:
                context_blocks.append(experience_context)
            experience_context = "\n\n---\n\n".join(context_blocks)

        # 步骤4: 调用 LLM 生成计划
        plan_steps = await generate_plan_steps(
            input_text=input_text,
            tools_description=tools_description,
            experience_context=experience_context,
            diagnosis_feedback=diagnosis_feedback,
        )

        logger.info(f"计划已生成，共 {len(plan_steps)} 个步骤")
        for i, step in enumerate(plan_steps, 1):
            logger.info(f"  步骤{i}: {step}")

        structured_steps = normalize_plan_steps(plan_steps)
        event = make_agent_event(
            agent="planner",
            stage="planning",
            status="completed",
            summary=f"Generated {len(structured_steps)} investigation steps.",
            payload={"plan": structured_steps},
        )
        return {"plan": structured_steps, "events": list(state.get("events", [])) + [event]}

    except Exception as e:
        logger.error(f"生成计划失败: {e}", exc_info=True)
        # 返回一个默认计划
        fallback_steps = normalize_plan_steps(
            [
                {
                    "description": "采集受影响服务或系统的当前指标。",
                    "tool_category": "monitor",
                    "expected_evidence": "CPU、内存、延迟、错误率或磁盘异常摘要。",
                },
                {
                    "description": "检索与故障相关的近期应用日志中的错误信息。",
                    "tool_category": "logs",
                    "expected_evidence": "错误消息、异常堆栈或超时记录。",
                },
                {
                    "description": "获取与当前故障类型相关的 Runbook 知识。",
                    "tool_category": "knowledge",
                    "expected_evidence": "已知原因及推荐处置步骤。",
                },
            ]
        )
        event = make_agent_event(
            agent="planner",
            stage="planning",
            status="degraded",
            summary="Generated fallback investigation plan.",
            payload={"plan": fallback_steps, "error": str(e)},
        )
        return {"plan": fallback_steps, "events": list(state.get("events", [])) + [event]}
