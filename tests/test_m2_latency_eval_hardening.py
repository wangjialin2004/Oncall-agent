"""M2 W5 regression coverage for latency budgets and on-call evaluation gates."""

from __future__ import annotations

import pytest

from app.agent.agent_loop import GuardedToolExecutor
from app.agent.experts.base import ToolCallingExpert
from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessLimits, HarnessState
from app.agent.harness.subagent import create_delegate_tool
from app.core.llm_client import LLMResponse, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.services.router_service import RouterService
from scripts.evaluate_oncall_local import score_case, summarize


def _complete_summary(
    *,
    re_evidence_rounds: int = 0,
    replan_times: int = 0,
) -> dict:
    return {
        "answer": "已基于工具证据给出诊断结论。",
        "tools": ["query_prometheus_alerts"],
        "tool_success_count": 1,
        "has_complete": True,
        "re_evidence_rounds": re_evidence_rounds,
        "replan_times": replan_times,
        "gaps": [],
    }


def test_summarize_reads_verify_status_from_agent_event_status():
    summary = summarize(
        [
            {
                "type": "agent_event",
                "stage": "verify",
                "status": "degraded",
                "payload": {"gaps": ["缺少指标"]},
            }
        ]
    )

    assert summary["verify_status"] == "degraded"
    assert summary["gaps"] == ["缺少指标"]


def test_summarize_preserves_primary_route_when_fallback_changes_final_route():
    summary = summarize(
        [
            {"type": "route_event", "route": "diagnosis"},
            {"type": "complete", "route": "knowledge", "answer": "fallback"},
        ]
    )

    assert summary["route"] == "diagnosis"
    assert summary["primary_route"] == "diagnosis"
    assert summary["final_route"] == "knowledge"


def test_degraded_harness_fallback_cannot_pass_a_diagnostic_case():
    score = score_case(
        {
            "expected": {"require_evidence": True},
            "scoring": {"pass_score": 7},
        },
        {
            **_complete_summary(),
            "answer": (
                "# 降级响应\n"
                "Harness main loop and knowledge fallback执行超过 30 秒"
            ),
        },
        latency=10,
        err=None,
    )

    assert score["passed"] is False
    assert score["error"] == "harness_degraded_fallback"


def test_required_re_evidence_and_replan_must_both_be_observed():
    case = {
        "expected": {
            "require_re_evidence": True,
            "require_replan": True,
        },
        "scoring": {"pass_score": 6},
    }

    absent = score_case(case, _complete_summary(), latency=1, err=None)
    only_re_evidence = score_case(
        case,
        _complete_summary(re_evidence_rounds=1),
        latency=1,
        err=None,
    )
    present = score_case(
        case,
        _complete_summary(re_evidence_rounds=1, replan_times=1),
        latency=1,
        err=None,
    )

    assert absent["passed"] is False
    assert absent["error"] == "required_re_evidence_not_triggered"
    assert only_re_evidence["passed"] is False
    assert only_re_evidence["error"] == "required_replan_not_triggered"
    assert present["passed"] is True


def test_route_timeout_profile_caps_diagnosis_but_off_restores_budget(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_route_timeout_profile", True)
    monkeypatch.setattr(app_config, "harness_diagnosis_max_steps", 3, raising=False)
    service = HarnessService(
        limits=HarnessLimits(max_steps=5, token_budget=1000, timeout_seconds=30)
    )

    assert service._effective_max_steps("diagnosis") == 3

    monkeypatch.setattr(app_config, "harness_route_timeout_profile", False)
    assert service._effective_max_steps("diagnosis") == 5


def test_investigation_evidence_early_close_requires_profile_and_success(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(app_config, "harness_route_timeout_profile", True)
    monkeypatch.setattr(
        app_config, "harness_investigation_evidence_early_close", True, raising=False
    )
    state = HarnessState(trace_id="trace", session_id="session", route="diagnosis")
    state.timeline_events.append(
        {
            "type": "tool_event",
            "tool": "query_prometheus_alerts",
            "status": "completed",
        }
    )
    service = HarnessService(
        limits=HarnessLimits(max_steps=5, token_budget=1000, timeout_seconds=30)
    )

    assert service._should_investigation_evidence_early_close(state=state) is True

    state.route = "knowledge"
    assert service._should_investigation_evidence_early_close(state=state) is True

    monkeypatch.setattr(app_config, "harness_route_timeout_profile", False)
    assert service._should_investigation_evidence_early_close(state=state) is False


@pytest.mark.asyncio
async def test_concrete_incident_overrides_semantic_knowledge_route(monkeypatch):
    from app.config import config as app_config

    monkeypatch.setattr(
        app_config, "router_concrete_incident_override_enabled", True, raising=False
    )

    class KnowledgeRouterLLM:
        async def complete(self, *_args, **_kwargs):
            return LLMResponse(
                content='{"route":"knowledge","reason":"generic","confidence":0.9}',
                raw={},
            )

    decision = await RouterService(llm_client=KnowledgeRouterLLM())._resolve_route(
        "checkout-api 服务不可用，健康检查失败且错误率很高，请综合排查"
    )

    assert decision.route == "diagnosis"
    assert decision.reason == "concrete_incident_override_knowledge"


@pytest.mark.asyncio
async def test_structured_provider_error_is_a_failed_tool_result():
    async def handler(_arguments: dict) -> dict:
        return {"status": "error", "error": "prometheus unavailable"}

    tool = RuntimeTool(
        name="query_prometheus_alerts",
        description="query alerts",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )
    result = await GuardedToolExecutor(max_retries=0).execute(
        [ToolCall(id="structured-error", name=tool.name, arguments={})],
        [tool],
    )

    assert len(result) == 1
    assert result[0].success is False
    assert "prometheus unavailable" in result[0].content


@pytest.mark.asyncio
async def test_delegate_forwards_configured_round_cap_to_expert(monkeypatch):
    class RecordingExpert:
        received_max_tool_rounds: int | None = None
        received_close_after_tools: bool = False

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
            self.received_max_tool_rounds = max_tool_rounds
            self.received_close_after_tools = close_after_tools
            yield {"type": "content", "data": "expert evidence"}

    expert = RecordingExpert()
    tool = create_delegate_tool(
        session_id="session-1",
        trace_id="trace-1",
        context_getter=lambda: "",
        expert_getter=lambda _route: expert,
    )

    result = await tool.handler({"expert": "metric", "subtask": "probe"})

    assert result["status"] == "completed"
    assert expert.received_max_tool_rounds == 1
    assert expert.received_close_after_tools is True


@pytest.mark.asyncio
async def test_evidence_only_expert_skips_its_duplicate_final_summary():
    class FakeClient:
        final_summary_calls = 0

        async def complete(self, messages, *, tools=None, **_kwargs):
            if tools:
                return LLMResponse(
                    content="",
                    raw={},
                    tool_calls=[ToolCall(id="probe", name="probe", arguments={})],
                )
            self.final_summary_calls += 1
            raise AssertionError("evidence-only delegate must not request a final summary")

    class OneToolExpert(ToolCallingExpert):
        async def get_tools(self):
            async def handler(_arguments):
                return {"status": "ok", "value": "evidence"}

            return [
                RuntimeTool(
                    name="probe",
                    description="probe",
                    parameters={"type": "object", "properties": {}},
                    handler=handler,
                )
            ]

        async def _new_llm_client(self):
            return fake_client

    fake_client = FakeClient()
    expert = OneToolExpert()
    events = [
        event
        async for event in expert.run(
            message="probe",
            session_id="session",
            trace_id="trace",
            max_tool_rounds=1,
            close_after_tools=True,
        )
    ]

    assert fake_client.final_summary_calls == 0
    assert any(event.get("tool") == "probe" for event in events)
    assert any(event.get("stage") == "evidence_return" for event in events)
