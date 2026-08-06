"""Router continuation inherit, low-confidence keep, knowledge light path."""

from __future__ import annotations

import pytest

from app.agent.harness.planner import LightweightPlanner, _is_knowledge_light_path
from app.agent.harness.policy import HarnessPolicyMixin
from app.agent.harness.state import HarnessState
from app.services.router_service import RouteDecision, RouterService


@pytest.mark.asyncio
async def test_continuation_inherits_previous_route(monkeypatch):
    monkeypatch.setattr(
        "app.services.router_service.config.router_continuation_inherit_enabled",
        True,
        raising=False,
    )
    router = RouterService(semantic_router=lambda message, hints=(): (_ for _ in ()).throw(
        AssertionError("semantic should not run for continuation")
    ))
    decision = await router._resolve_route("继续", previous_route="knowledge")
    assert decision.route == "knowledge"
    assert decision.reason == "continuation_inherit_knowledge"
    assert decision.confidence == 0.85


@pytest.mark.asyncio
async def test_continuation_disabled_falls_through(monkeypatch):
    monkeypatch.setattr(
        "app.services.router_service.config.router_continuation_inherit_enabled",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.router_service.config.router_low_confidence_keep_semantic_enabled",
        True,
        raising=False,
    )

    async def fake_semantic(message, hints=()):
        return RouteDecision(route="knowledge", reason="llm", confidence=0.99)

    router = RouterService(semantic_router=fake_semantic)
    decision = await router._resolve_route("继续", previous_route="knowledge")
    assert decision.route == "knowledge"
    assert not decision.reason.startswith("continuation_inherit_")


@pytest.mark.asyncio
async def test_low_confidence_keeps_knowledge(monkeypatch):
    monkeypatch.setattr(
        "app.services.router_service.config.router_low_confidence_keep_semantic_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.router_service.config.router_min_confidence",
        0.55,
        raising=False,
    )

    async def fake_semantic(message, hints=()):
        return RouteDecision(
            route="knowledge",
            reason="llm_semantic_knowledge",
            confidence=0.22,
            hints=("knowledge",),
        )

    router = RouterService(semantic_router=fake_semantic)
    router.min_confidence = 0.55
    decision = await router._resolve_route("刚刚的问题是什么")
    assert decision.route == "knowledge"
    assert decision.reason == "low_confidence_keep_knowledge"


@pytest.mark.asyncio
async def test_low_confidence_operational_still_diagnosis(monkeypatch):
    monkeypatch.setattr(
        "app.services.router_service.config.router_low_confidence_keep_semantic_enabled",
        True,
        raising=False,
    )

    async def fake_semantic(message, hints=()):
        return RouteDecision(
            route="knowledge",
            reason="llm_semantic_knowledge",
            confidence=0.2,
            hints=("metric", "diagnosis"),
        )

    router = RouterService(semantic_router=fake_semantic)
    router.min_confidence = 0.55
    decision = await router._resolve_route("支付服务延迟升高怎么排查")
    # concrete target + incident signal may also override; either operational
    # diagnosis reason is acceptable.
    assert decision.route == "diagnosis"
    assert "diagnosis" in decision.reason


@pytest.mark.asyncio
async def test_low_confidence_legacy_default_when_flag_off(monkeypatch):
    monkeypatch.setattr(
        "app.services.router_service.config.router_low_confidence_keep_semantic_enabled",
        False,
        raising=False,
    )

    async def fake_semantic(message, hints=()):
        return RouteDecision(route="knowledge", reason="llm", confidence=0.1)

    router = RouterService(semantic_router=fake_semantic)
    router.min_confidence = 0.55
    decision = await router._resolve_route("你好")
    assert decision.route == "diagnosis"
    assert decision.reason == "low_confidence_knowledge_default_diagnosis"


def test_is_knowledge_light_message_markers():
    assert RouterService.is_knowledge_light_message("你好") is True
    assert RouterService.is_knowledge_light_message("你是什么模型") is True
    assert RouterService.is_knowledge_light_message("查一下当前有哪些告警") is False
    assert (
        RouterService.is_knowledge_light_message(
            "order-api CPU 告警，接口超时，帮我根因分析"
        )
        is False
    )


def test_planner_knowledge_light_path_clears_required_evidence(monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.planner.config.harness_knowledge_light_path_enabled",
        True,
        raising=False,
    )
    planner = LightweightPlanner()
    plan = planner.create(
        message="你是什么模型",
        route_decision=RouteDecision(route="knowledge", reason="x", confidence=0.9),
        tools=[],
        history_turns=1,
    )
    assert plan.focus_route == "knowledge"
    assert plan.required_evidence == []
    assert _is_knowledge_light_path(route="knowledge", message="你是什么模型") is True


def test_policy_skips_replan_on_knowledge_light_plan(monkeypatch):
    monkeypatch.setattr(
        "app.agent.harness.policy.config.harness_knowledge_light_path_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.agent.harness.policy.config.harness_replan_enabled",
        True,
        raising=False,
    )

    class _Svc(HarnessPolicyMixin):
        def __init__(self):
            self.limits = type("L", (), {"max_steps": 4, "token_budget": 99999})()

    svc = _Svc()
    plan = type(
        "P",
        (),
        {"focus_route": "knowledge", "required_evidence": []},
    )()
    state = HarnessState(
        trace_id="t",
        session_id="s",
        owner_key="o",
        route="knowledge",
        step=2,
    )
    assert (
        svc._should_replan(
            replan_times_used=0,
            resume_close_only=False,
            state=state,
            verification_gaps=["缺少证据类型：知识库检索结果"],
            force_after_re_evidence=True,
            aux_routes=(),
            plan=plan,
        )
        is False
    )


def test_is_knowledge_light_plan_helper():
    plan = type("P", (), {"focus_route": "knowledge", "required_evidence": []})()
    assert HarnessPolicyMixin._is_knowledge_light_plan(plan) is True
    heavy = type(
        "P",
        (),
        {"focus_route": "knowledge", "required_evidence": ["知识库检索结果"]},
    )()
    assert HarnessPolicyMixin._is_knowledge_light_plan(heavy) is False
    assert HarnessPolicyMixin._is_knowledge_light_plan(None) is False
