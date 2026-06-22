"""Evidence verification for harness answers.

The default verifier is deterministic: it inspects tool evidence, the final
answer and the plan's required evidence. When ``harness_llm_verify_enabled`` is
on and an LLM client is available, one extra call refines the status / gaps;
evidence counts always come from the deterministic pass. Any failure falls back
to the rule-based result.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.agent.harness.planner import HarnessPlan, _extract_json
from app.config import config
from app.core.llm_client import ChatMessage

_VERIFIER_SYSTEM = (
    "你是只读 OnCall 排查的证据自检助手。基于回答内容和已获得的工具证据，判断结论是否被证据支撑。"
    "只返回 JSON，对象包含字段："
    '"status"（completed|degraded|failed）、"confidence"（low|medium|high）、'
    '"gaps"（缺口字符串数组，可为空）、"summary"（一句话结论）。不要输出 JSON 以外的内容。'
)

_STATUS_VALUES = {"completed", "degraded", "failed"}
_CONFIDENCE_VALUES = {"low", "medium", "high"}


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: str
    summary: str
    confidence: str
    evidence_count: int
    failed_evidence_count: int
    gaps: list[str] = field(default_factory=list)


class EvidenceVerifier:
    """Check whether the answer is backed by usable evidence."""

    def verify(
        self,
        *,
        answer: str,
        timeline_events: Sequence[dict[str, Any]],
        plan: HarnessPlan | None,
    ) -> VerificationResult:
        evidence_events = [
            event
            for event in timeline_events
            if event.get("type") == "tool_event" and event.get("status") == "completed"
        ]
        failed_events = [
            event
            for event in timeline_events
            if event.get("type") == "tool_event" and event.get("status") != "completed"
        ]
        gaps: list[str] = []
        if not evidence_events:
            gaps.append("未产生成功工具证据")
        if failed_events:
            gaps.append(f"{len(failed_events)} 个工具调用失败或被降级")
        if not answer.strip():
            gaps.append("最终回答为空")
        if plan and plan.required_evidence and not evidence_events:
            gaps.append(f"计划要求的证据尚未满足：{'、'.join(plan.required_evidence)}")

        if not answer.strip() or (failed_events and not evidence_events):
            status = "failed"
            confidence = "low"
            summary = "自检未通过：缺少可用证据或最终回答为空"
        elif gaps:
            status = "degraded"
            confidence = "low"
            summary = "自检发现证据缺口，回答需按低置信度处理"
        else:
            status = "completed"
            confidence = "medium"
            summary = "自检通过：回答已关联成功工具证据"

        return VerificationResult(
            status=status,
            summary=summary,
            confidence=confidence,
            evidence_count=len(evidence_events),
            failed_evidence_count=len(failed_events),
            gaps=gaps,
        )

    async def averify(
        self,
        *,
        answer: str,
        timeline_events: Sequence[dict[str, Any]],
        plan: HarnessPlan | None,
        llm_client: Any | None = None,
    ) -> VerificationResult:
        base = self.verify(answer=answer, timeline_events=timeline_events, plan=plan)
        if not getattr(config, "harness_llm_verify_enabled", False) or llm_client is None:
            return base
        try:
            refined = await self._llm_verify(answer=answer, base=base, llm_client=llm_client)
        except Exception as exc:  # verification must never break the loop
            logger.warning(f"harness LLM 自检失败，回退规则版：{exc}")
            return base
        return refined or base

    async def _llm_verify(
        self,
        *,
        answer: str,
        base: VerificationResult,
        llm_client: Any,
    ) -> VerificationResult | None:
        user_prompt = (
            f"回答内容：\n{answer}\n\n"
            f"成功工具证据数：{base.evidence_count}；失败/降级工具数：{base.failed_evidence_count}\n"
            f"规则版初判：status={base.status}, confidence={base.confidence}, "
            f"gaps={base.gaps}\n"
            "请复核并返回 JSON 自检结论。"
        )
        response = await llm_client.complete(
            [
                ChatMessage(role="system", content=_VERIFIER_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0,
        )
        data = _extract_json(response.content)
        if not data:
            return None
        status = str(data.get("status") or base.status).strip().lower()
        if status not in _STATUS_VALUES:
            status = base.status
        confidence = str(data.get("confidence") or base.confidence).strip().lower()
        if confidence not in _CONFIDENCE_VALUES:
            confidence = base.confidence
        gaps = [str(item).strip() for item in data.get("gaps", []) if str(item).strip()]
        summary = str(data.get("summary") or base.summary).strip() or base.summary
        return VerificationResult(
            status=status,
            summary=summary,
            confidence=confidence,
            evidence_count=base.evidence_count,
            failed_evidence_count=base.failed_evidence_count,
            gaps=gaps or base.gaps,
        )
