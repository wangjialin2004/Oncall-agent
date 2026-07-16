"""M3 W9: HITL suggested actions, escalation block, agent metrics, confirm API."""

from __future__ import annotations

from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessState
from app.api import hitl
from app.core import metrics as metrics_mod


def test_build_suggested_actions_and_flag_off(monkeypatch):
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t", session_id="s", owner_key="o")
    state.route = "diagnosis"
    monkeypatch.setattr(
        "app.agent.harness.loop.config.hitl_suggested_actions_enabled",
        True,
        raising=False,
    )
    actions = svc._build_suggested_actions(state, "只读建议")
    assert actions
    assert all(a.get("requires_confirm") for a in actions)
    assert all("已执行" not in a.get("title", "") for a in actions)
    monkeypatch.setattr(
        "app.agent.harness.loop.config.hitl_suggested_actions_enabled",
        False,
        raising=False,
    )
    assert svc._build_suggested_actions(state, "x") == []


def test_escalation_block_configured_and_empty(monkeypatch):
    svc = HarnessService.__new__(HarnessService)
    monkeypatch.setattr(
        "app.agent.harness.loop.config.oncall_escalation_contacts",
        "",
        raising=False,
    )
    empty = svc._build_escalation_block()
    assert empty["configured"] is False
    assert "未配置" in empty["text"]
    monkeypatch.setattr(
        "app.agent.harness.loop.config.oncall_escalation_contacts",
        "Alice|pager;Bob|slack",
        raising=False,
    )
    filled = svc._build_escalation_block()
    assert filled["configured"] is True
    assert len(filled["contacts"]) == 2
    assert "Alice" in filled["text"]


def test_observe_agent_run_does_not_raise():
    metrics_mod.observe_agent_run(
        status="completed",
        latency_seconds=12.5,
        re_evidence_rounds=1,
        replan_times=1,
        parallel_delegations=1,
    )
    metrics_mod.observe_agent_run(status="weird-status")


def test_hitl_confirm_audit_only():
    hitl.clear_audit_log()

    import asyncio

    async def _call():
        from app.api.hitl import ConfirmSuggestionRequest, confirm_suggestion

        resp = await confirm_suggestion(
            ConfirmSuggestionRequest(
                SessionId="s1",
                ActionId="review_metrics",
                Note="ack",
            )
        )
        return resp

    resp = asyncio.run(_call())
    assert resp["data"]["executed"] is False
    assert resp["data"]["accepted"] is True
    log = hitl.get_audit_log()
    assert len(log) == 1
    assert log[0]["executed"] is False
