"""Triage Agent for structuring raw OnCall incident descriptions."""

from __future__ import annotations

import re
import json
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, LLMClientConfig

from .events import make_agent_event


class Incident(BaseModel):
    incident_type: Literal[
        "cpu",
        "memory",
        "disk",
        "slow_response",
        "service_unavailable",
        "error_rate",
        "unknown",
    ] = Field(description="Primary incident category.")
    service_name: str = Field(default="", description="Affected service name if known.")
    time_window: str = Field(default="recent", description="Time window to inspect.")
    severity: str = Field(default="P2", description="P0/P1/P2/P3 severity.")
    symptoms: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    evidence_needs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0)


def _detect_incident_type(text: str) -> str:
    lowered = text.lower()
    if "cpu" in lowered:
        return "cpu"
    if any(token in lowered for token in ("memory", "内存", "oom")):
        return "memory"
    if any(token in lowered for token in ("disk", "磁盘")):
        return "disk"
    if any(token in lowered for token in ("500", "error", "错误率", "报错")):
        return "error_rate"
    if any(token in lowered for token in ("unavailable", "不可用", "挂了", "down")):
        return "service_unavailable"
    if any(token in lowered for token in ("slow", "latency", "响应慢", "转圈", "超时")):
        return "slow_response"
    return "unknown"


def _extract_service_name(text: str) -> str:
    match = re.search(r"([A-Za-z][A-Za-z0-9_-]*(?:-api|-service|_api|_service)?)", text)
    return match.group(1) if match else ""


def build_minimal_incident(input_text: str) -> dict[str, Any]:
    service_name = _extract_service_name(input_text)
    return {
        "incident_type": _detect_incident_type(input_text),
        "service_name": service_name,
        "time_window": "recent",
        "severity": "P2",
        "symptoms": [input_text.strip()] if input_text.strip() else [],
        "missing_fields": ["service_name"] if not service_name else [],
        "evidence_needs": ["metrics", "logs", "knowledge"],
        "confidence": 0.4,
    }


def _parse_incident_response(content: str) -> Incident:
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

    return Incident.model_validate(payload)


async def generate_incident(input_text: str, llm_client: Any | None = None) -> dict[str, Any]:
    owns_client = llm_client is None
    client = llm_client or LLMClient(LLMClientConfig.from_settings(config))
    try:
        response = await client.complete(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "你是 OnCall 告警分诊智能体。将用户描述的故障转换为结构化 JSON。"
                        "仅在用户未提供时使用 unknown 填充字段。"
                        "evidence_needs 从 metrics、logs、knowledge 中选取。只返回 JSON，不要其他内容。"
                    ),
                ),
                ChatMessage(role="user", content=input_text),
            ],
            temperature=0,
        )
    finally:
        if owns_client:
            await client.aclose()
    return _parse_incident_response(response.content).model_dump()


async def triage(state: dict[str, Any]) -> dict[str, Any]:
    input_text = str(state.get("input", ""))
    try:
        incident = await generate_incident(input_text)
        status = "completed"
        summary = f"Structured incident as {incident.get('incident_type', 'unknown')}"
    except Exception as exc:
        logger.warning(f"Triage Agent degraded to minimal incident: {exc}")
        incident = build_minimal_incident(input_text)
        status = "degraded"
        summary = f"Used minimal incident fallback as {incident['incident_type']}"

    event = make_agent_event(
        agent="triage",
        stage="triage",
        status=status,
        summary=summary,
        payload={"incident": incident},
    )
    return {"incident": incident, "events": list(state.get("events", [])) + [event]}
