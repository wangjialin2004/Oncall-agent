from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import config
from app.core.runtime_tools import make_runtime_tool
from app.services.experience_memory_service import experience_memory_service


class RecallExperienceArgs(BaseModel):
    query: str = Field(description="Incident symptom or diagnostic question")
    session_id: str = Field(default="", description="Scoped session id for hit-count dedupe")


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
        lines.extend(
            [
                f"- experience_id: {item['experience_id']}",
                f"  confidence: {item.get('confidence', 0):.2f}",
                f"  similarity: {item.get('similarity', 0):.2f}",
                f"  symptoms: {item['symptoms']}",
                f"  verified_root_cause: {item['root_cause']}",
                f"  effective_resolution: {item['resolution']}",
                f"  evidence_summary: {item['evidence_summary']}",
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
