"""M3 W10: semi-automatic distill draft → confirm / reject."""

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
    db = tmp_path / "w10_mem.db"
    svc = ExperienceMemoryService(db_path=db, index_service=None)
    monkeypatch.setattr(
        "app.services.experience_memory_service.experience_memory_service",
        svc,
        raising=False,
    )
    return svc


def test_draft_pending_not_recalled_until_confirm(mem_svc: ExperienceMemoryService, monkeypatch):
    monkeypatch.setattr(
        "app.services.experience_memory_service.config.project_id",
        "p1",
        raising=False,
    )
    exp_id = mem_svc.create_draft_from_run(
        project_id="p1",
        session_id="s1",
        user_message="payment 服务 CPU 飙高",
        assistant_answer="根因疑似线程池打满，建议只读复核指标。",
        events=[
            {
                "type": "tool_event",
                "tool": "query_prometheus_alerts",
                "status": "completed",
                "summary": "cpu high",
                "evidence_id": "e1",
            }
        ],
        owner_key="admin",
        auto_activate=False,
    )
    draft = mem_svc.get(exp_id)
    assert draft is not None
    assert draft["status"] == "pending"
    assert draft["enabled"] is False
    assert draft["source_type"] == "auto_distill"

    # Pending must not appear in enabled list / recall.
    assert mem_svc.list(project_id="p1", enabled=True, status="active") == []
    recalled = mem_svc.recall(query="payment CPU", project_id="p1", top_k=3)
    assert all(r["experience_id"] != exp_id for r in recalled)

    confirmed = mem_svc.confirm_draft(exp_id, owner_key="admin")
    assert confirmed is not None
    assert confirmed["status"] == "active"
    assert confirmed["enabled"] is True

    recalled2 = mem_svc.recall(query="payment CPU 飙高", project_id="p1", top_k=3)
    assert any(r["experience_id"] == exp_id for r in recalled2)


def test_reject_draft(mem_svc: ExperienceMemoryService):
    exp_id = mem_svc.create_draft_from_run(
        project_id="p1",
        session_id="s2",
        user_message="磁盘告警",
        assistant_answer="可能是日志膨胀",
        events=[
            {
                "type": "tool_event",
                "tool": "search_app_logs",
                "status": "completed",
                "summary": "log volume",
            }
        ],
        auto_activate=False,
    )
    rejected = mem_svc.reject_draft(exp_id, owner_key="admin")
    assert rejected is not None
    assert rejected["status"] == "rejected"
    assert rejected["enabled"] is False


def test_auto_activate_when_confirm_not_required(mem_svc: ExperienceMemoryService):
    exp_id = mem_svc.create_draft_from_run(
        project_id="p1",
        session_id="s3",
        user_message="mem high",
        assistant_answer="OOM risk",
        events=[
            {
                "type": "tool_event",
                "tool": "query_prometheus_alerts",
                "status": "completed",
            }
        ],
        auto_activate=True,
    )
    row = mem_svc.get(exp_id)
    assert row is not None
    assert row["status"] == "active"
    assert row["enabled"] is True


def test_pii_redacted_in_draft(mem_svc: ExperienceMemoryService):
    exp_id = mem_svc.create_draft_from_run(
        project_id="p1",
        session_id="s4",
        user_message="leak",
        assistant_answer="token=supersecret password=abc 联系 13800138000",
        events=[
            {
                "type": "tool_event",
                "tool": "query_prometheus_alerts",
                "status": "completed",
            }
        ],
        auto_activate=False,
    )
    row = mem_svc.get(exp_id)
    assert row is not None
    blob = f"{row['root_cause']} {row['evidence_summary']} {row['resolution']}"
    assert "supersecret" not in blob
    assert "13800138000" not in blob
    assert "[REDACTED" in blob or "REDACTED" in blob


def test_maybe_distill_hook_default_pending(mem_svc: ExperienceMemoryService, monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.loop.config.long_term_memory_distill_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.long_term_memory_auto_distill",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.long_term_memory_distill_require_confirm",
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
    state = HarnessState(trace_id="t", session_id="s-distill", owner_key="o")
    state.route = "diagnosis"
    state.append_answer("只读结论：CPU 高，建议复核。")
    state.timeline_events = [
        {
            "type": "tool_event",
            "tool": "query_prometheus_alerts",
            "status": "completed",
            "summary": "firing",
        }
    ]
    verification = VerificationResult(
        status="completed",
        summary="ok",
        confidence="high",
        evidence_count=1,
        failed_evidence_count=0,
        gaps=[],
    )
    draft = svc._maybe_distill_experience(
        state=state,
        message="cpu high on svc",
        owner_key="admin",
        verification=verification,
    )
    assert draft is not None
    assert draft["status"] == "pending"
    assert draft["requires_confirm"] is True
    row = mem_svc.get(draft["experience_id"])
    assert row is not None
    assert row["enabled"] is False


def test_maybe_distill_skips_knowledge_only(mem_svc: ExperienceMemoryService, monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.loop.config.long_term_memory_distill_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.long_term_memory_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.experience_memory_service.experience_memory_service",
        mem_svc,
        raising=False,
    )
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t", session_id="s-k", owner_key="o")
    state.route = "knowledge"
    state.append_answer("如何排查 CPU：先看 top。")
    state.timeline_events = [
        {
            "type": "tool_event",
            "tool": "retrieve_knowledge",
            "status": "completed",
            "summary": "doc hit",
        }
    ]
    verification = VerificationResult(
        status="completed",
        summary="ok",
        confidence="high",
        evidence_count=1,
        failed_evidence_count=0,
        gaps=[],
    )
    assert (
        svc._maybe_distill_experience(
            state=state,
            message="如何排查 CPU",
            owner_key="admin",
            verification=verification,
        )
        is None
    )


def test_distill_disabled_writes_nothing(mem_svc: ExperienceMemoryService, monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.loop.config.long_term_memory_distill_enabled",
        False,
        raising=False,
    )
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t", session_id="s-off", owner_key="o")
    state.route = "diagnosis"
    state.append_answer("x")
    state.timeline_events = [
        {"type": "tool_event", "tool": "query_prometheus_alerts", "status": "completed"}
    ]
    assert (
        svc._maybe_distill_experience(
            state=state,
            message="m",
            owner_key="a",
            verification=VerificationResult(
                status="completed",
                summary="ok",
                confidence="high",
                evidence_count=1,
                failed_evidence_count=0,
            ),
        )
        is None
    )
