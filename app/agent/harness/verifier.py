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

# required_evidence phrase keywords → tools that can satisfy the class.
# Matching is intentionally broad so unknown phrases do not over-punish.
_EVIDENCE_CLASS_TOOLS: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (
        ("指标", "告警", "曲线", "prometheus", "metric", "cpu", "内存", "磁盘"),
        frozenset(
            {
                "query_prometheus_alerts",
                "check_redis_health",
                "delegate_to_expert",
            }
        ),
    ),
    (
        ("日志", "堆栈", "log", "trace", "聚类"),
        frozenset(
            {
                "delegate_to_expert",
                "search_app_logs",
                "query_logs",
                "analyze_logs",
            }
        ),
    ),
    (
        ("变更", "发布", "回滚", "deploy", "change"),
        frozenset({"query_recent_changes", "delegate_to_expert"}),
    ),
    (
        ("知识", "runbook", "经验", "文档", "检索", "前提"),
        frozenset(
            {
                "retrieve_knowledge",
                "recall_experience",
                "lookup_service_knowledge",
                "delegate_to_expert",
            }
        ),
    ),
    (
        ("缺口", "说明"),
        frozenset(),  # meta requirement — satisfied by any successful tool or explicit gap text
    ),
)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: str
    summary: str
    confidence: str
    evidence_count: int
    failed_evidence_count: int
    gaps: list[str] = field(default_factory=list)


def _successful_tool_names(timeline_events: Sequence[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for event in timeline_events:
        if event.get("type") != "tool_event" or event.get("status") != "completed":
            continue
        name = str(event.get("tool") or event.get("tool_name") or "").strip()
        if name:
            names.add(name)
    return names


def _tools_for_evidence_phrase(phrase: str) -> frozenset[str] | None:
    """Return tool set for a required_evidence phrase, or None if unknown."""
    text = (phrase or "").strip().lower()
    if not text:
        return None
    for keywords, tools in _EVIDENCE_CLASS_TOOLS:
        if any(key.lower() in text for key in keywords):
            return tools
    return None


def missing_required_evidence_gaps(
    *,
    required_evidence: Sequence[str],
    successful_tools: set[str],
) -> list[str]:
    """Return gap strings for required evidence classes not covered by tools."""
    gaps: list[str] = []
    for phrase in required_evidence:
        label = str(phrase or "").strip()
        if not label:
            continue
        tools = _tools_for_evidence_phrase(label)
        if tools is None:
            # Unknown class: only require *some* successful tool overall.
            if not successful_tools:
                gaps.append(f"缺少证据类型：{label}")
            continue
        if not tools:
            # Meta "缺口说明" — no specific tool required.
            continue
        if successful_tools.isdisjoint(tools):
            gaps.append(f"缺少证据类型：{label}")
    return gaps


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
        successful_tools = _successful_tool_names(timeline_events)
        gaps: list[str] = []
        if not evidence_events:
            gaps.append("未产生成功工具证据")
        if failed_events:
            gaps.append(f"{len(failed_events)} 个工具调用失败或被降级")
        if not answer.strip():
            gaps.append("最终回答为空")
        if plan and plan.required_evidence:
            if not evidence_events:
                gaps.append(
                    f"计划要求的证据尚未满足：{'、'.join(plan.required_evidence)}"
                )
            elif bool(getattr(config, "harness_evidence_match_enabled", True)):
                gaps.extend(
                    missing_required_evidence_gaps(
                        required_evidence=plan.required_evidence,
                        successful_tools=successful_tools,
                    )
                )

        if not answer.strip() or (failed_events and not evidence_events):
            status = "failed"
            confidence = "low"
            summary = "自检未通过：缺少可用证据或最终回答为空"
        elif failed_events and evidence_events:
            status = "degraded"
            confidence = "medium"
            summary = "自检通过主要证据，但存在部分工具失败或降级"
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
        # 模型分层：自检是证据/置信度判断任务，调用 reasoner(深度模型) 保证质量。
        # 空字符串表示退回 LLMClient 默认模型，兼容老配置。
        reasoner_model = str(getattr(config, "llm_reasoner_model", "") or "") or None
        response = await llm_client.complete(
            [
                ChatMessage(role="system", content=_VERIFIER_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0,
            model=reasoner_model,
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
