"""M3 W10: failure anti-pattern capture + recall demotion text."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessState
from app.agent.harness.verifier import VerificationResult
from app.services.experience_memory_service import ExperienceMemoryService
from app.services.memory_cache import reset_default_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_default_cache()
    yield
    reset_default_cache()


@pytest.fixture
def mem_svc(tmp_path: Path, monkeypatch) -> ExperienceMemoryService:
    svc = ExperienceMemoryService(db_path=tmp_path / "anti.db", index_service=None)
    monkeypatch.setattr(
        "app.services.experience_memory_service.experience_memory_service",
        svc,
        raising=False,
    )
    return svc


def test_create_anti_pattern_and_recall_flags(mem_svc: ExperienceMemoryService):
    exp_id = mem_svc.create_anti_pattern(
        project_id="p1",
        session_id="s1",
        user_message="prometheus 挂了还在查指标",
        failed_tools=["query_prometheus_alerts", "query_metrics"],
        assistant_answer="无法获取实时指标",
        owner_key="admin",
    )
    row = mem_svc.get(exp_id)
    assert row is not None
    assert row["source_type"] == "anti_pattern"
    assert row["is_anti_pattern"] is True
    assert row["enabled"] is True
    assert "勿重复" in row["resolution"]

    recalled = mem_svc.recall(query="prometheus 挂了还在查指标", project_id="p1", top_k=3)
    assert any(r.get("is_anti_pattern") for r in recalled)
    joined = "\n".join(
        f"[反模式/勿重复] {item['root_cause']}"
        for item in recalled
        if item.get("is_anti_pattern")
    )
    assert "反模式" in joined or "勿重复" in joined
    assert "query_prometheus_alerts" in joined or "无效" in joined


def test_maybe_capture_anti_pattern_on_failed_tools(mem_svc: ExperienceMemoryService, monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_anti_pattern_capture_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.long_term_memory_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.project_id",
        "p1",
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.experience_memory_service.experience_memory_service",
        mem_svc,
        raising=False,
    )
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t", session_id="s-anti", owner_key="o")
    state.route = "diagnosis"
    state.append_answer("无法获取实时指标，存在证据缺口。")
    state.timeline_events = [
        {
            "type": "tool_event",
            "tool": "query_prometheus_alerts",
            "status": "failed",
            "summary": "connection refused",
        },
        {
            "type": "tool_event",
            "tool": "retrieve_knowledge",
            "status": "completed",
            "summary": "doc",
        },
    ]
    out = svc._maybe_capture_anti_pattern(
        state=state,
        message="查 CPU",
        owner_key="admin",
        verification=VerificationResult(
            status="degraded",
            summary="gap",
            confidence="low",
            evidence_count=0,
            failed_evidence_count=1,
            gaps=["无实时指标"],
        ),
    )
    assert out is not None
    assert out["source_type"] == "anti_pattern"
    assert "query_prometheus_alerts" in out["failed_tools"]
    row = mem_svc.get(out["experience_id"])
    assert row is not None
    assert row["source_type"] == "anti_pattern"


def test_anti_pattern_flag_off(mem_svc: ExperienceMemoryService, monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_anti_pattern_capture_enabled",
        False,
        raising=False,
    )
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t", session_id="s-off", owner_key="o")
    state.append_answer("无法")
    state.timeline_events = [
        {"type": "tool_event", "tool": "query_prometheus_alerts", "status": "failed"}
    ]
    assert (
        svc._maybe_capture_anti_pattern(
            state=state,
            message="m",
            owner_key="a",
            verification=None,
        )
        is None
    )
