"""Diagnosis Agent and loop routing for OnCall workflows."""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config

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


async def generate_diagnosis(state: dict[str, Any]) -> dict[str, Any]:
    model = ChatQwen(
        model=config.rag_model,
        api_key=config.dashscope_api_key,
        temperature=0,
        streaming=False,
    )
    classifier = model.with_structured_output(DiagnosisResult)
    result = await classifier.ainvoke(
        [
            SystemMessage(
                content=(
                    "You are an OnCall diagnosis agent. Decide whether evidence is sufficient. "
                    "Return evidence_insufficient when important evidence is missing. "
                    "Return root_cause_ready only when evidence supports the conclusion."
                )
            ),
            HumanMessage(content=f"Incident: {state.get('incident', {})}"),
            HumanMessage(content=f"Evidence: {state.get('evidence', [])}"),
            HumanMessage(content=f"Past steps: {state.get('past_steps', [])}"),
        ]
    )
    if isinstance(result, DiagnosisResult):
        return result.model_dump()
    return DiagnosisResult.model_validate(result).model_dump()


async def diagnosis(state: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await generate_diagnosis(state)
    except Exception as exc:
        logger.warning(f"Diagnosis Agent degraded to insufficient evidence: {exc}")
        result = {
            "status": "evidence_insufficient",
            "root_cause_candidates": [],
            "missing_evidence": ["Diagnosis model was unavailable."],
            "next_focus": "collect metrics, logs, and relevant runbook evidence",
            "confidence": 0.0,
        }

    next_iteration = int(state.get("iteration", 0)) + 1
    summary = (
        "Root cause is ready."
        if result.get("status") == "root_cause_ready"
        else result.get("next_focus") or "More evidence is needed."
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
