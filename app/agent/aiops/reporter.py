"""Report Agent for final OnCall diagnosis reports."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_qwq import ChatQwen
from loguru import logger

from app.config import config

from .events import make_agent_event


def _format_evidence(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "- No tool evidence was collected."
    lines = []
    for item in evidence:
        evidence_id = item.get("evidence_id", "")
        status = item.get("status", "")
        summary = item.get("summary", "")
        lines.append(f"- `{evidence_id}` [{status}] {summary}".strip())
    return "\n".join(lines)


def _format_candidates(diagnosis: dict[str, Any]) -> str:
    candidates = diagnosis.get("root_cause_candidates") or []
    if not candidates:
        return "- Root cause is not confirmed with current evidence."
    lines = []
    for item in candidates:
        cause = item.get("cause", "")
        confidence = item.get("confidence", 0)
        lines.append(f"- {cause} (confidence: {confidence})")
    return "\n".join(lines)


def build_fallback_report(state: dict[str, Any]) -> str:
    incident = state.get("incident", {})
    diagnosis = state.get("diagnosis", {})
    evidence = state.get("evidence", [])
    service_name = incident.get("service_name") or "unknown service"
    incident_type = incident.get("incident_type") or "unknown"
    confidence = diagnosis.get("confidence", "unknown")

    return "\n".join(
        [
            "# OnCall Diagnosis Report",
            "",
            "## Incident Summary",
            f"- Service: {service_name}",
            f"- Type: {incident_type}",
            f"- Diagnosis status: {diagnosis.get('status', 'unknown')}",
            f"- Confidence: {confidence}",
            "",
            "## Evidence",
            _format_evidence(evidence),
            "",
            "## Root Cause Candidates",
            _format_candidates(diagnosis),
            "",
            "## Missing Evidence",
            "\n".join(f"- {item}" for item in diagnosis.get("missing_evidence", [])) or "- None recorded.",
            "",
            "## Recommended Actions",
            "- Review the evidence above before applying any remediation.",
            "- Collect missing evidence when confidence is low.",
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
                "You are an OnCall report agent. Produce a concise Markdown report. "
                "Separate confirmed facts, inferred causes, missing evidence, and recommended actions. "
                "Do not invent evidence."
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
        summary = "Generated final OnCall diagnosis report"
    except Exception as exc:
        logger.warning(f"Report Agent degraded to fallback report: {exc}")
        report = build_fallback_report(state)
        status = "degraded"
        summary = "Generated fallback OnCall diagnosis report"

    event = make_agent_event(
        agent="report",
        stage="reporting",
        status=status,
        summary=summary,
        payload={"report_length": len(report)},
    )
    return {"response": report, "events": list(state.get("events", [])) + [event]}
