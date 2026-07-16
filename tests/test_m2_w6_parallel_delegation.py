"""M2 W6 regression coverage for parallel delegation and aux probes."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessLimits, HarnessState
from app.agent.harness.subagent import (
    aux_execution_mode,
    create_delegate_parallel_tool,
    create_delegate_tool,
    normalize_delegate_pairs,
    run_parallel_delegates,
)
from app.agent.harness.trace_export import maybe_export_harness_trace
from app.core.llm_client import ChatMessage
from scripts.evaluate_oncall_local import score_case, summarize


class _SleepExpert:
    def __init__(self, delay: float, label: str, *, fail: bool = False) -> None:
        self.delay = delay
        self.label = label
        self.fail = fail
        self.calls = 0
        self.received_max_tool_rounds: int | None = None
        self.received_close_after_tools: bool | None = None

    async def run(
        self,
        *,
        message: str,
        session_id: str,
        trace_id: str,
        context: str = "",
        max_tool_rounds: int | None = None,
        close_after_tools: bool = False,
    ):
        self.calls += 1
        self.received_max_tool_rounds = max_tool_rounds
        self.received_close_after_tools = close_after_tools
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"{self.label} boom")
        yield {
            "type": "tool_event",
            "tool": f"{self.label}_tool",
            "status": "completed",
        }
        yield {"type": "content", "data": f"{self.label}:{message}"}


@pytest.mark.asyncio
async def test_parallel_tool_runs_two_experts_concurrently(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_parallel_delegation_enabled", True)
    monkeypatch.setattr(app_config, "harness_delegate_max_tool_rounds", 1)
    metric = _SleepExpert(0.05, "metric")
    log = _SleepExpert(0.05, "log")
    experts = {"metric": metric, "log": log}

    tool = create_delegate_parallel_tool(
        session_id="s1",
        trace_id="t1",
        context_getter=lambda: "",
        expert_getter=lambda route: experts[route],
    )

    started = time.perf_counter()
    result = await tool.handler(
        {
            "experts": ["metric", "log"],
            "subtasks": ["check metrics", "check logs"],
        }
    )
    wall = time.perf_counter() - started

    assert result["status"] == "completed"
    assert result["parallel"] is True
    assert len(result["results"]) == 2
    assert wall < 0.09
    assert metric.calls == 1 and log.calls == 1
    assert metric.received_max_tool_rounds == 1
    assert metric.received_close_after_tools is True


@pytest.mark.asyncio
async def test_parallel_disabled_rejects_tool(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_parallel_delegation_enabled", False)
    tool = create_delegate_parallel_tool(
        session_id="s1",
        trace_id="t1",
        context_getter=lambda: "",
        expert_getter=lambda _route: _SleepExpert(0.0, "metric"),
    )
    result = await tool.handler(
        {"experts": ["metric", "log"], "subtasks": ["a", "b"]}
    )
    assert result["status"] == "failed"
    assert result["error"] == "parallel_delegation_disabled"


def test_parallel_respects_max_experts_truncation(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_parallel_max_experts", 2)
    pairs, truncated = normalize_delegate_pairs(
        ["metric", "log", "knowledge"],
        ["m", "l", "k"],
        max_experts=2,
    )
    assert truncated is True
    assert [p[0] for p in pairs] == ["metric", "log"]


@pytest.mark.asyncio
async def test_parallel_one_expert_failure_does_not_drop_sibling_success():
    metric = _SleepExpert(0.01, "metric")
    log = _SleepExpert(0.01, "log", fail=True)
    experts = {"metric": metric, "log": log}

    payload = await run_parallel_delegates(
        [("metric", "m"), ("log", "l")],
        session_id="s",
        trace_id="t",
        expert_getter=lambda route: experts[route],
    )
    by_expert = {r["expert"]: r for r in payload["results"]}
    assert by_expert["metric"]["status"] == "completed"
    assert by_expert["log"]["status"] == "failed"
    assert payload["status"] == "completed"


def test_aux_mode_invalid_falls_back_to_off(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "router_aux_execution_mode", "bogus")
    assert aux_execution_mode() == "off"


@pytest.mark.asyncio
async def test_aux_mode_off_does_not_invoke_experts(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "router_aux_execution_mode", "off")
    monkeypatch.setattr(app_config, "harness_delegation_enabled", True)
    expert = _SleepExpert(0.0, "metric")
    service = HarnessService(
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=30)
    )
    state = HarnessState(trace_id="t", session_id="s", route="diagnosis")
    messages: list[ChatMessage] = []

    # Patch get_expert used inside loop module.
    monkeypatch.setattr(
        "app.agent.harness.loop.get_expert", lambda _route: expert
    )

    events = []
    async for event in service._maybe_run_aux_probes(
        message="cpu high",
        aux_routes=["metric", "log"],
        primary_route="diagnosis",
        messages=messages,
        state=state,
        context_state=None,
    ):
        events.append(event)

    assert events == []
    assert expert.calls == 0


@pytest.mark.asyncio
async def test_aux_mode_parallel_emits_aux_probe_and_invokes_aux_only(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "router_aux_execution_mode", "parallel")
    monkeypatch.setattr(app_config, "router_aux_max_probes", 2)
    monkeypatch.setattr(app_config, "harness_delegation_enabled", True)

    metric = _SleepExpert(0.02, "metric")
    log = _SleepExpert(0.02, "log")
    knowledge = _SleepExpert(0.02, "knowledge")
    experts = {"metric": metric, "log": log, "knowledge": knowledge}
    monkeypatch.setattr(
        "app.agent.harness.loop.get_expert", lambda route: experts[route]
    )

    service = HarnessService(
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=30)
    )
    state = HarnessState(trace_id="t", session_id="s", route="diagnosis")
    messages: list[ChatMessage] = []

    started = time.perf_counter()
    stages = []
    async for event in service._maybe_run_aux_probes(
        message="checkout-api down, check metrics and logs",
        aux_routes=["metric", "log"],
        primary_route="diagnosis",
        messages=messages,
        state=state,
        context_state=None,
    ):
        if event.get("type") == "agent_event":
            stages.append(event.get("stage"))
    wall = time.perf_counter() - started

    assert "aux_probe" in stages
    assert metric.calls == 1 and log.calls == 1
    assert knowledge.calls == 0
    assert wall < 0.05
    assert any(m.role == "user" and "aux" in (m.content or "").lower() for m in messages)
    assert any(
        ev.get("type") == "tool_event" and ev.get("tool") == "delegate_to_expert"
        for ev in state.timeline_events
    )


@pytest.mark.asyncio
async def test_aux_mode_serial_order(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "router_aux_execution_mode", "serial")
    monkeypatch.setattr(app_config, "router_aux_max_probes", 2)
    monkeypatch.setattr(app_config, "harness_delegation_enabled", True)

    order: list[str] = []

    class OrderedExpert(_SleepExpert):
        async def run(self, **kwargs):
            order.append(self.label)
            async for event in super().run(**kwargs):
                yield event

    metric = OrderedExpert(0.01, "metric")
    log = OrderedExpert(0.01, "log")
    experts = {"metric": metric, "log": log}
    monkeypatch.setattr(
        "app.agent.harness.loop.get_expert", lambda route: experts[route]
    )

    service = HarnessService(
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=30)
    )
    state = HarnessState(trace_id="t", session_id="s", route="diagnosis")
    messages: list[ChatMessage] = []
    async for _ in service._maybe_run_aux_probes(
        message="x",
        aux_routes=["metric", "log"],
        primary_route="diagnosis",
        messages=messages,
        state=state,
        context_state=None,
    ):
        pass

    assert order == ["metric", "log"]


@pytest.mark.asyncio
async def test_shared_delegate_round_cap_still_forwarded(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_delegate_max_tool_rounds", 1)
    monkeypatch.setattr(app_config, "harness_delegate_evidence_only", True)
    expert = _SleepExpert(0.0, "metric")
    tool = create_delegate_tool(
        session_id="s",
        trace_id="t",
        context_getter=lambda: "",
        expert_getter=lambda _r: expert,
    )
    result = await tool.handler({"expert": "metric", "subtask": "probe"})
    assert result["status"] == "completed"
    assert expert.received_max_tool_rounds == 1
    assert expert.received_close_after_tools is True


def test_trace_export_noop_when_disabled(tmp_path, monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_trace_export_enabled", False)
    monkeypatch.setattr(app_config, "harness_trace_export_dir", str(tmp_path))
    state = HarnessState(trace_id="t", session_id="s", route="diagnosis")
    path = maybe_export_harness_trace(session_id="s", state=state)
    assert path is None
    assert list(tmp_path.iterdir()) == []


def test_trace_export_writes_when_enabled(tmp_path, monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_trace_export_enabled", True)
    # W11: sample_rate defaults to 0; force 1.0 so enabled still writes.
    monkeypatch.setattr(app_config, "harness_trace_sample_rate", 1.0, raising=False)
    monkeypatch.setattr(app_config, "harness_trace_export_dir", str(tmp_path))
    state = HarnessState(trace_id="t", session_id="sess-1", route="diagnosis")
    state.timeline_events.append({"type": "agent_event", "stage": "plan"})
    path = maybe_export_harness_trace(
        session_id="sess-1",
        state=state,
        re_evidence_rounds=1,
        replan_times=0,
    )
    assert path is not None
    assert Path(path).exists()
    text = Path(path).read_text(encoding="utf-8")
    assert "sess-1" in text
    assert "timeline_events" in text


def test_summarize_detects_parallel_event():
    summary = summarize(
        [
            {
                "type": "agent_event",
                "stage": "delegate_parallel_start",
                "payload": {"parallel": True, "experts": ["metric", "log"]},
            },
            {
                "type": "agent_event",
                "stage": "aux_probe",
                "payload": {"parallel": True, "mode": "parallel"},
            },
            {"type": "complete", "route": "diagnosis", "answer": "ok"},
        ]
    )
    assert summary["parallel_event"] is True
    assert summary["has_complete"] is True


def test_require_parallel_event_gate():
    case = {
        "expected": {"require_parallel_event": True},
        "scoring": {"pass_score": 6},
    }
    absent = score_case(
        case,
        {
            "answer": "诊断完成",
            "tools": ["query_prometheus_alerts"],
            "tool_success_count": 1,
            "has_complete": True,
            "re_evidence_rounds": 0,
            "replan_times": 0,
            "gaps": [],
            "parallel_event": False,
        },
        latency=1,
        err=None,
    )
    present = score_case(
        case,
        {
            "answer": "诊断完成",
            "tools": ["delegate_parallel"],
            "tool_success_count": 1,
            "has_complete": True,
            "re_evidence_rounds": 0,
            "replan_times": 0,
            "gaps": [],
            "parallel_event": True,
        },
        latency=1,
        err=None,
    )
    assert absent["passed"] is False
    assert absent["error"] == "required_parallel_event_not_triggered"
    assert present["passed"] is True
