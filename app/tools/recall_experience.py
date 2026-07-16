from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.config import config
from app.core.runtime_tools import make_runtime_tool
from app.services.experience_memory_service import experience_memory_service


class RecallExperienceArgs(BaseModel):
    query: str = Field(description="Incident symptom or diagnostic question")
    session_id: str = Field(default="", description="Scoped session id for hit-count dedupe")


_BLOCKQUOTE_PREFIX_RE = re.compile(r"^(?:>\s*)+")
_MARKDOWN_PREFIX_RE = re.compile(r"^(?:(?:[-*+#])\s+)+")
_LEGACY_NOISE_MARKERS = (
    "鈿",
    "璇佹嵁",
    "to=multi_tool_use",
    "to=functions.",
    '"tool_uses"',
    '"recipient_name"',
)


def _clean_recalled_text(value: object, *, first_line_only: bool = False) -> str:
    """Return readable recalled text without legacy protocol or mojibake lines."""

    cleaned_lines: list[str] = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any("\ue000" <= char <= "\uf8ff" for char in line):
            continue
        if any(marker.lower() in line.lower() for marker in _LEGACY_NOISE_MARKERS):
            continue
        line = _BLOCKQUOTE_PREFIX_RE.sub("", line).strip()
        line = _MARKDOWN_PREFIX_RE.sub("", line).strip()
        if not line:
            continue
        cleaned_lines.append(line)
        if first_line_only:
            break

    return "；".join(cleaned_lines) if cleaned_lines else "未提供"


def _recall_experience(query: str, session_id: str = "") -> str:
    memories = experience_memory_service.recall(
        query=query,
        project_id=config.project_id,
        top_k=config.experience_memory_top_k,
        session_id=session_id,
    )
    if not memories:
        return "未命中可复用的历史诊断经验。"
    lines = ["历史经验仅供参考，必须先用当前证据验证后再采信。"]
    for item in memories:
        conflict = item.get("conflict_count", 0)
        is_anti = bool(item.get("is_anti_pattern")) or str(item.get("source_type") or "") == "anti_pattern"
        if is_anti:
            lines.extend(
                [
                    f"- [反模式/勿重复] experience_id: {_clean_recalled_text(item['experience_id'], first_line_only=True)}",
                    f"  confidence: {item.get('confidence', 0):.2f}",
                    f"  similarity: {item.get('similarity', 0):.2f}",
                    f"  symptoms: {_clean_recalled_text(item['symptoms'], first_line_only=True)}",
                    f"  dead_path: {_clean_recalled_text(item['root_cause'])}",
                    f"  guidance: {_clean_recalled_text(item['resolution'])}",
                    f"  evidence_summary: {_clean_recalled_text(item['evidence_summary'])}",
                ]
            )
            continue
        lines.extend(
            [
                f"- experience_id: {_clean_recalled_text(item['experience_id'], first_line_only=True)}",
                f"  confidence: {item.get('confidence', 0):.2f}",
                f"  similarity: {item.get('similarity', 0):.2f}",
                f"  symptoms: {_clean_recalled_text(item['symptoms'], first_line_only=True)}",
                f"  verified_root_cause: {_clean_recalled_text(item['root_cause'])}",
                f"  effective_resolution: {_clean_recalled_text(item['resolution'])}",
                f"  evidence_summary: {_clean_recalled_text(item['evidence_summary'])}",
            ]
        )
        if conflict:
            lines.append(f"  注意：存在 {conflict} 条冲突经验，请谨慎采信。")
    return "\n".join(lines)


recall_experience = make_runtime_tool(
    name="recall_experience",
    description="Recall verified historical diagnosis experience. Results are reference evidence only and must be verified.",
    func=_recall_experience,
    args_schema=RecallExperienceArgs,
)
