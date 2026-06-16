"""Report Agent for final OnCall diagnosis reports."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_qwq import ChatQwen
from loguru import logger

from app.config import config

from .events import make_agent_event


STATUS_LABELS = {
    "completed": "已完成",
    "failed": "失败",
    "degraded": "降级",
    "evidence_insufficient": "证据不足",
    "root_cause_ready": "根因已就绪",
    "unknown": "未知",
}


def _format_status(status: Any) -> str:
    value = str(status or "unknown")
    return STATUS_LABELS.get(value, value)


def _format_evidence(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "- 未收集到工具证据。"
    lines = []
    for item in evidence:
        evidence_id = item.get("evidence_id", "")
        status = _format_status(item.get("status", ""))
        summary = item.get("summary", "")
        lines.append(f"- `{evidence_id}` [{status}] {summary}".strip())
    return "\n".join(lines)


def _format_candidates(diagnosis: dict[str, Any]) -> str:
    candidates = diagnosis.get("root_cause_candidates") or []
    if not candidates:
        return "- 当前证据不足，暂未确认根因。"
    lines = []
    for item in candidates:
        cause = item.get("cause", "")
        confidence = item.get("confidence", 0)
        lines.append(f"- {cause}（置信度：{confidence}）")
    return "\n".join(lines)


def build_fallback_report(state: dict[str, Any]) -> str:
    incident = state.get("incident", {})
    diagnosis = state.get("diagnosis", {})
    evidence = state.get("evidence", [])
    service_name = incident.get("service_name") or "未知服务"
    incident_type = incident.get("incident_type") or "未知"
    confidence = diagnosis.get("confidence", "未知")

    return "\n".join(
        [
            "# OnCall 诊断报告",
            "",
            "## 事件摘要",
            f"- 服务：{service_name}",
            f"- 类型：{incident_type}",
            f"- 诊断状态：{_format_status(diagnosis.get('status', 'unknown'))}",
            f"- 置信度：{confidence}",
            "",
            "## 证据",
            _format_evidence(evidence),
            "",
            "## 根因候选",
            _format_candidates(diagnosis),
            "",
            "## 缺失证据",
            "\n".join(f"- {item}" for item in diagnosis.get("missing_evidence", [])) or "- 暂无记录。",
            "",
            "## 建议操作",
            "- 执行任何修复前，请先复核以上证据。",
            "- 当置信度较低时，优先补齐缺失证据。",
        ]
    )


async def generate_report(state: dict[str, Any]) -> str:
    model = ChatQwen(
        model=config.rag_model,
        api_key=config.dashscope_api_key,
        temperature=0,
        streaming=False,
    )
    prompt = [
        SystemMessage(
            content=(
                "你是 OnCall 报告智能体。请输出简洁的中文 Markdown 报告。"
                "明确区分已确认事实、推断根因、缺失证据和建议操作。"
                "不要编造证据。"
            )
        ),
        HumanMessage(content=f"Incident: {state.get('incident', {})}"),
        HumanMessage(content=f"Evidence: {state.get('evidence', [])}"),
        HumanMessage(content=f"Diagnosis: {state.get('diagnosis', {})}"),
    ]
    result = await model.ainvoke(prompt)
    return result.content if hasattr(result, "content") else str(result)


async def reporter(state: dict[str, Any]) -> dict[str, Any]:
    try:
        report = await generate_report(state)
        status = "completed"
        summary = "已生成最终 OnCall 诊断报告"
    except Exception as exc:
        logger.warning(f"Report Agent degraded to fallback report: {exc}")
        report = build_fallback_report(state)
        status = "degraded"
        summary = "已生成降级版 OnCall 诊断报告"

    event = make_agent_event(
        agent="report",
        stage="reporting",
        status=status,
        summary=summary,
        payload={"report_length": len(report)},
    )
    return {"response": report, "events": list(state.get("events", [])) + [event]}
