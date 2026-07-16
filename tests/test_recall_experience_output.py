from __future__ import annotations

from app.services.experience_memory_service import experience_memory_service
from app.tools.recall_experience import _recall_experience


def test_recall_experience_reports_no_reusable_memory(monkeypatch) -> None:
    monkeypatch.setattr(experience_memory_service, "recall", lambda **_: [])

    assert _recall_experience("未知问题") == "未命中可复用的历史诊断经验。"


def test_recall_experience_drops_legacy_mojibake_and_markdown_noise(monkeypatch) -> None:
    monkeypatch.setattr(
        experience_memory_service,
        "recall",
        lambda **_: [
            {
                "experience_id": "exp-1",
                "confidence": 0.8,
                "similarity": 0.6,
                "symptoms": (
                    "payment-service 内存使用率超过 85%\n"
                    "> 鈿狅笍 璇佹嵁鑷检锛氱疆淇″害 medium\n"
                    'to=multi_tool_use.parallel {"tool_uses": []}'
                ),
                "root_cause": "> - 堆内存持续增长",
                "resolution": "- 补充实时指标并核对 OOM 日志",
                "evidence_summary": "# 历史指标和日志一致",
            }
        ],
    )

    result = _recall_experience("OOM")

    assert "鈿" not in result
    assert "璇佹嵁" not in result
    assert "tool_uses" not in result
    assert "symptoms: payment-service 内存使用率超过 85%" in result
    assert "verified_root_cause: 堆内存持续增长" in result
    assert "effective_resolution: 补充实时指标并核对 OOM 日志" in result
    assert "evidence_summary: 历史指标和日志一致" in result


def test_recall_experience_keeps_anti_pattern_shape_after_cleaning(monkeypatch) -> None:
    monkeypatch.setattr(
        experience_memory_service,
        "recall",
        lambda **_: [
            {
                "experience_id": "anti-1",
                "confidence": 0.7,
                "similarity": 0.5,
                "symptoms": "checkout-api 延迟升高",
                "root_cause": "- 仅凭单点指标直接判定数据库故障",
                "resolution": "> 应先核对调用链和数据库指标",
                "evidence_summary": "[] 污染行",
                "is_anti_pattern": True,
            }
        ],
    )

    result = _recall_experience("延迟")

    assert "[反模式/勿重复] experience_id: anti-1" in result
    assert "dead_path: 仅凭单点指标直接判定数据库故障" in result
    assert "guidance: 应先核对调用链和数据库指标" in result
    assert "evidence_summary: 未提供" in result
    assert "\uE000" not in result
