"""Diagnosis Agent and loop routing for OnCall workflows."""

from __future__ import annotations

import json
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from app.config import config
from app.core.llm_client import ChatMessage, LLMClient, LLMClientConfig

from .events import make_decision_event


class RootCauseCandidate(BaseModel):
    cause: str
    confidence: float = 0.0
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class DiagnosisResult(BaseModel):
    status: Literal["evidence_insufficient", "root_cause_ready"]
    root_cause_candidates: list[RootCauseCandidate] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    next_focus: str = ""
    confidence: float = 0.0


def route_after_diagnosis(state: dict[str, Any]) -> str:
    diagnosis = state.get("diagnosis", {})
    status = diagnosis.get("status")
    iteration = int(state.get("iteration", 0))
    max_iterations = int(state.get("max_iterations", 2))
    if status == "root_cause_ready":
        return "reporter"
    if iteration >= max_iterations:
        return "reporter"
    return "planner"


def _parse_diagnosis_response(content: str) -> DiagnosisResult:
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

    return DiagnosisResult.model_validate(payload)


async def generate_diagnosis(
    state: dict[str, Any],
    llm_client: Any | None = None,
) -> dict[str, Any]:
    owns_client = llm_client is None
    client = llm_client or LLMClient(LLMClientConfig.from_settings(config))
    try:
        response = await client.complete(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "你是 OnCall 诊断智能体。判断当前证据是否充分。"
                        "缺少关键证据时返回 evidence_insufficient。"
                        "只有证据支持结论时才返回 root_cause_ready。"
                        "root_cause_candidates、missing_evidence 和 next_focus 必须使用中文。"
                        "Return only JSON."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        f"Incident: {state.get('incident', {})}\n"
                        f"Evidence: {state.get('evidence', [])}\n"
                        f"Past steps: {state.get('past_steps', [])}"
                    ),
                ),
            ],
            temperature=0,
        )
    finally:
        if owns_client:
            await client.aclose()
    return _parse_diagnosis_response(response.content).model_dump()


async def diagnosis(state: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await generate_diagnosis(state)
    except Exception as exc:
        logger.warning(f"Diagnosis Agent degraded to insufficient evidence: {exc}")
        result = {
            "status": "evidence_insufficient",
            "root_cause_candidates": [],
            "missing_evidence": ["诊断模型不可用。"],
            "next_focus": "收集指标、日志和相关预案证据",
            "confidence": 0.0,
        }

    next_iteration = int(state.get("iteration", 0)) + 1
    summary = (
        "根因判断已就绪。"
        if result.get("status") == "root_cause_ready"
        else result.get("next_focus") or "仍需补充更多证据。"
    )
    event = make_decision_event(
        agent="diagnosis",
        status=str(result.get("status", "evidence_insufficient")),
        summary=summary,
        payload={"diagnosis": result, "iteration": next_iteration},
    )
    return {
        "diagnosis": result,
        "iteration": next_iteration,
        "events": list(state.get("events", [])) + [event],
    }
