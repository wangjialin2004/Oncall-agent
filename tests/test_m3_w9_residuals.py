"""M3 W9: parallel seed, replan-on-primary-fail, simulate fault inject, soft-close helpers."""

from __future__ import annotations

import pytest

from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessState
from app.core.runtime_tools import RuntimeTool


def _tool(name: str, ok: bool = True) -> RuntimeTool:
    async def handler(arguments: dict) -> dict:
        if not ok:
            raise RuntimeError(f"{name} failed")
        return {"success": True, "tool": name}

    return RuntimeTool(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )


def test_looks_cross_domain_parallel_and_dual_signal():
    svc = HarnessService.__new__(HarnessService)
    assert svc._looks_cross_domain(
        message="checkout-api 同时出现 CPU 告警和错误日志，请并行排查",
        route="diagnosis",
        aux_routes=(),
    )
    assert svc._looks_cross_domain(
        message="请看指标",
        route="diagnosis",
        aux_routes=("metric", "log"),
    )
    assert not svc._looks_cross_domain(
        message="什么是 OOM？",
        route="knowledge",
        aux_routes=(),
    )


def test_should_force_seed_parallel_respects_flags(monkeypatch):
    svc = HarnessService.__new__(HarnessService)
    tools = [_tool("delegate_parallel"), _tool("delegate_to_expert")]
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_force_parallel_on_cross_domain",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_parallel_delegation_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_delegation_enabled",
        True,
        raising=False,
    )
    assert svc._should_force_seed_parallel(
        message="CPU 和错误日志一起暴增，请并行取证",
        route="diagnosis",
        tools=tools,
        aux_routes=(),
        prefer_parallel=None,
    )
    assert svc._should_force_seed_parallel(
        message="随便",
        route="diagnosis",
        tools=tools,
        aux_routes=(),
        prefer_parallel=True,
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_force_parallel_on_cross_domain",
        False,
        raising=False,
    )
    assert not svc._should_force_seed_parallel(
        message="CPU 和错误日志一起暴增，请并行取证",
        route="diagnosis",
        tools=tools,
        aux_routes=(),
        prefer_parallel=True,
    )


def test_should_replan_on_primary_fail_even_with_weak_success(monkeypatch):
    from app.agent.harness.state import HarnessLimits

    svc = HarnessService.__new__(HarnessService)
    svc.limits = HarnessLimits(max_steps=6, token_budget=80000, timeout_seconds=240.0)
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_replan_enabled", True, raising=False
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_replan_max_times", 1, raising=False
    )
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_replan_on_primary_fail",
        True,
        raising=False,
    )
    state = HarnessState(trace_id="t", session_id="s", owner_key="o")
    state.route = "metric"
    state.step = 2
    state.timeline_events = [
        {
            "type": "tool_event",
            "tool": "query_prometheus_alerts",
            "status": "failed",
            "summary": "prometheus down",
        },
        {
            "type": "tool_event",
            "tool": "retrieve_knowledge",
            "status": "completed",
            "summary": "kb hit",
        },
    ]
    assert svc._should_replan(
        replan_times_used=0,
        resume_close_only=False,
        state=state,
        verification_gaps=None,
        force_after_re_evidence=False,
        aux_routes=(),
    )


def test_apply_simulate_faults_wraps_prometheus_tools():
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t", session_id="s", owner_key="o")
    tools = [
        _tool("query_prometheus_alerts"),
        _tool("retrieve_knowledge"),
        _tool("query_cpu_metrics"),
    ]
    wrapped = svc._apply_simulate_faults(
        tools,
        simulate="prometheus_unavailable_with_delegation_off",
        state=state,
    )
    names = [t.name for t in wrapped]
    assert "query_prometheus_alerts" in names
    assert any(ev.get("stage") == "simulate_fault" for ev in state.timeline_events)

    async def _run_fail():
        with pytest.raises(RuntimeError, match="simulated"):
            await wrapped[0].run({})

    import asyncio

    asyncio.run(_run_fail())


def test_filter_tools_by_cap(monkeypatch):
    svc = HarnessService.__new__(HarnessService)
    monkeypatch.setattr(
        "app.agent.harness.loop.config.harness_slow_path_tool_cap", 2, raising=False
    )
    state = HarnessState(trace_id="t", session_id="s", owner_key="o")
    state.timeline_events = [
        {"type": "tool_event", "tool": "search_app_logs", "status": "completed"},
        {"type": "tool_event", "tool": "search_app_logs", "status": "completed"},
        {"type": "tool_event", "tool": "query_cpu_metrics", "status": "completed"},
    ]
    tools = [
        _tool("search_app_logs"),
        _tool("query_cpu_metrics"),
        _tool("delegate_parallel"),
    ]
    filtered = svc._filter_tools_by_cap(tools, state)
    names = {t.name for t in filtered}
    assert "search_app_logs" not in names
    assert "query_cpu_metrics" in names
    assert "delegate_parallel" in names


def test_timeout_soft_close_builds_complete_without_degraded_marker():
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t", session_id="s", owner_key="o")
    state.route = "diagnosis"
    state.timeline_events = [
        {
            "type": "tool_event",
            "tool": "query_cpu_metrics",
            "status": "completed",
            "summary": "ok",
        }
    ]
    assert svc._should_timeout_soft_close(state)
    payload = svc._soft_close_timeout_event(
        state=state,
        session_id="s",
        message="user-service P99 慢",
        timeout_seconds=180,
    )
    complete = payload["complete"]
    assert complete["type"] == "complete"
    assert "降级响应" not in (complete.get("answer") or "")
    assert "harness main loop and knowledge fallback" not in (
        complete.get("answer") or ""
    ).lower()
    assert complete.get("suggested_actions") is not None


def test_synthesize_zero_evidence_gap_answer_on_primary_fail():
    svc = HarnessService.__new__(HarnessService)
    state = HarnessState(trace_id="t", session_id="s", owner_key="o")
    state.route = "metric"
    state.timeline_events = [
        {
            "type": "tool_event",
            "tool": "query_prometheus_alerts",
            "status": "failed",
            "summary": "simulated prometheus unavailable",
        },
        {
            "type": "tool_event",
            "tool": "query_cpu_metrics",
            "status": "failed",
            "summary": "simulated prometheus unavailable",
        },
    ]
    text = svc._synthesize_zero_evidence_gap_answer(
        message="checkout-api CPU 告警，请查实时指标",
        state=state,
        replan_times_used=1,
    )
    assert text
    assert "缺口" in text
    assert "无法" in text or "失败" in text
    assert "query_prometheus_alerts" in text
    # With successful evidence, do not synthesize.
    state.timeline_events.append(
        {
            "type": "tool_event",
            "tool": "retrieve_knowledge",
            "status": "completed",
            "summary": "kb",
        }
    )
    assert (
        svc._synthesize_zero_evidence_gap_answer(
            message="x", state=state, replan_times_used=1
        )
        == ""
    )
