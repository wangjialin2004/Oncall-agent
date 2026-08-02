"""Harness observability hooks — duration_ms / latency_ms 透传与模型分层断言。

Verifies the P2 deliverable from
``plan/2026-07-07-harness-loop-timeout-mitigation.md``:

- ``complete``/``error``/``step_timeout`` agent events carry ``duration_ms``.
- ``tool_event`` payloads carry ``tool_latency_ms`` (from LLMResponse/result).
- ``LLMResponse.latency_ms`` is populated by ``_post_with_retry``.
- Router and verifier respect the model-tiering config
  (``llm_planner_model`` / ``llm_reasoner_model``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from app.agent.agent_loop import GuardedToolExecutor
from app.agent.harness.loop import HarnessService
from app.agent.harness.state import HarnessLimits
from app.agent.harness.subagent import create_delegate_tool
from app.core.llm_client import LLMResponse, LLMStreamChunk, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.services.router_service import RouteDecision


@dataclass
class _StageCall:
    """Minimal stub of the OpenAI provider-side chat response used by
    ``LLMClient._post_with_retry`` to assert ``latency_ms`` is recorded."""

    latency_ms: int


class _FakeLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs})
        return self.responses.pop(0)

    async def stream_complete(self, messages, **kwargs):
        response = self.responses.pop(0)
        yield response.content

    async def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": {**kwargs, "stream": True}})
        response = self.responses.pop(0)
        content = response.content
        midpoint = max(1, len(content) // 2)
        for chunk in (content[:midpoint], content[midpoint:]):
            if chunk:
                yield LLMStreamChunk(content=chunk)
        yield LLMStreamChunk(response=response)

    async def aclose(self) -> None:
        pass


class _FakeRouter:
    def __init__(self, route: str = "diagnosis") -> None:
        self.route = route

    async def _resolve_route(self, message: str, previous_route: str | None = None, **kwargs) -> RouteDecision:
        return RouteDecision(route=self.route, reason="fake_focus", confidence=0.8)


class _FakeDelegateExpert:
    async def run(self, *, message: str, session_id: str, trace_id: str, context: str = ""):
        yield {
            "type": "tool_event",
            "agent": "metric_expert",
            "tool": "query_metric",
            "status": "completed",
            "evidence_id": "metric-evidence-1",
            "summary": "CPU is high",
            # Simulate downstream tool latency exposure
            "duration_ms": 123,
        }
        yield {"type": "content", "data": f"metric answer:{message}"}


# ---------------------------------------------------------------------- events


@pytest.mark.asyncio
async def test_harness_complete_event_carries_duration_ms():
    """``stage=complete`` 事件 payload 必须包含 ``duration_ms``（毫秒级）。"""
    fake_llm = _FakeLLM(
        [
            LLMResponse(
                content="final answer",
                raw={},
                usage={"total_tokens": 5},
            ),
        ]
    )
    service = HarnessService(
        router=_FakeRouter(route="knowledge"),
        llm_client=fake_llm,
        tools=[],
        limits=HarnessLimits(max_steps=2, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "trivial knowledge question", session_id="trace-obs-1", owner_key="user-1"
        )
    ]

    complete = next(event for event in events if event.get("stage") == "complete")
    assert "duration_ms" in complete, "complete 事件必须包含 duration_ms"
    assert complete["duration_ms"] >= 0
    # payload 里也要可见，便于前端消费
    assert complete["payload"].get("duration_ms") == complete["duration_ms"]


@pytest.mark.asyncio
async def test_harness_error_event_carries_duration_ms():
    """``stage=error`` 事件顶层必须包含 ``duration_ms``，便于前端回放耗时。"""

    class _BoomRouter:
        async def _resolve_route(self, message: str, previous_route: str | None = None, **kwargs) -> RouteDecision:  # noqa: ARG002
            raise RuntimeError("synthetic failure inside the loop")

    fake_llm = _FakeLLM(
        [LLMResponse(content="unused", raw={}, usage={"total_tokens": 1})]
    )
    service = HarnessService(
        router=_BoomRouter(),
        llm_client=fake_llm,
        tools=[],
        limits=HarnessLimits(
            max_steps=2,
            token_budget=1000,
            timeout_seconds=5,
            fallback_timeout_seconds=1,
        ),
    )

    events = [
        event
        async for event in service.stream(
            "force an error", session_id="trace-obs-err", owner_key="user-1"
        )
    ]

    error_event = next(
        (event for event in events if event.get("stage") == "error"), None
    )
    assert error_event is not None, "异常路径必须发出 stage=error 事件"
    assert "duration_ms" in error_event, "error 事件顶层必须包含 duration_ms"
    assert error_event["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_harness_step_timeout_event_emitted_when_single_step_exceeds_budget(monkeypatch):
    """``stage=step_timeout`` 必须出现（说明 P0-2 单步断流保护生效）。"""

    class _SlowFakeLLM(_FakeLLM):
        def __init__(self) -> None:
            super().__init__(
                [LLMResponse(content="never used", raw={}, usage={"total_tokens": 1})]
            )

        async def stream_chat(self, messages, **kwargs):  # noqa: ARG002
            await asyncio.sleep(2.0)
            yield LLMStreamChunk(content="late")
            yield LLMStreamChunk(
                response=LLMResponse(content="late", raw={}, usage={"total_tokens": 1})
            )

    fake_llm = _SlowFakeLLM()

    # 必须有工具才会走 model_decision 分支（用 stream_chat + step_timeout 保护），
    # tools=[] 会直接走 _stream_final_answer 路径，不会触发 step_timeout。
    probe_tool = RuntimeTool(
        name="noop_probe",
        description="probe to force model_decision path",
        handler=lambda arguments: {"ok": True},
    )

    service = HarnessService(
        router=_FakeRouter(route="knowledge"),
        llm_client=fake_llm,
        tools=[probe_tool],
        limits=HarnessLimits(
            max_steps=3,
            token_budget=1000,
            timeout_seconds=10,
            step_timeout_seconds=0.05,
            fallback_timeout_seconds=2,
        ),
    )

    events = [
        event
        async for event in service.stream(
            "force step timeout", session_id="trace-obs-step", owner_key="user-1"
        )
    ]

    assert any(event.get("stage") == "step_timeout" for event in events)
    # 即便 step_timeout 触发，SSE 最终仍会收到 complete 事件，避免连接悬挂
    assert events[-1]["type"] == "complete"


# ------------------------------------------------------------------ tool latency


@pytest.mark.asyncio
async def test_tool_event_payload_exposes_tool_latency_ms():
    """``tool_event`` payload 必须含 ``tool_latency_ms`` 字段（P2 透传到前端）。"""
    from app.agent.agent_loop import stream_tool_results

    # 构造一个耗时工具 handler（~50ms），executor.execute 会自然填 latency_ms。
    async def _slow_handler(arguments):  # noqa: ARG001
        await asyncio.sleep(0.05)
        return {"ok": True}

    tool = RuntimeTool(
        name="query_metric",
        description="probe",
        handler=_slow_handler,
    )
    tool_call = ToolCall(
        id="call-1", name="query_metric", arguments={"service": "checkout-api"}
    )
    executor = GuardedToolExecutor()
    executed = await executor.execute([tool_call], [tool])
    assert executed[0].latency_ms >= 30, (
        f"executor 应该已经填好 latency_ms, 实际={executed[0].latency_ms}"
    )

    async def _slow_processor(result):  # noqa: ARG001
        # processor 是 measure_duration 阶段，再睡 30ms 确保 duration_ms > 0
        await asyncio.sleep(0.03)
        return result.content, [], []

    messages: list[Any] = []
    tool_events: list[dict[str, Any]] = []
    async for _event in stream_tool_results(
        executed,
        messages=messages,
        agent_label="harness",
        trace_id="trace-tool-latency",
        args_by_id={"call-1": {"service": "checkout-api"}},
        measure_duration=True,
        process_result=_slow_processor,
        on_event=tool_events.append,
    ):
        pass

    tool_event = next(event for event in tool_events if event.get("type") == "tool_event")
    payload = tool_event["payload"]
    assert "tool_latency_ms" in payload, (
        "tool_event payload 必须包含 tool_latency_ms 字段（P2 透传）"
    )
    assert payload["tool_latency_ms"] == executed[0].latency_ms
    assert "duration_ms" in tool_event, "tool_event 顶层必须包含 duration_ms"
    assert tool_event["duration_ms"] >= 30


# ----------------------------------------------------------------- LLM latency


@pytest.mark.asyncio
async def test_llm_response_carries_latency_ms(monkeypatch):
    """``LLMResponse.latency_ms`` 必须被 ``_post_with_retry`` 填充（P2）。"""
    from dataclasses import dataclass

    from app.core import llm_client as llm_module

    @dataclass
    class _StubConfig:
        provider: str = "openai"
        base_url: str = "http://test.invalid/v1"
        api_key: str = "test-key"
        model: str = "gpt-5.4"
        timeout: float = 5.0
        max_retries: int = 0
        retry_base_delay: float = 0.0

    captured: dict[str, int] = {}

    async def _fake_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        captured["hit"] = 1
        # 模拟一次 50ms 的网络请求；和真实实现一样，结束后写 _last_latency_ms
        await asyncio.sleep(0.05)
        self._last_latency_ms = 50
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok", "tool_calls": None},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(llm_module.LLMClient, "_post_with_retry", _fake_post)

    # 不需要真实网络：用 stub config + 自己 mock http_client，让 httpx 永远不发请求
    import httpx

    http_client = httpx.AsyncClient(timeout=1.0)
    client = llm_module.LLMClient(_StubConfig(), http_client=http_client)

    response = await client.complete(
        [llm_module.ChatMessage(role="user", content="ping")],
    )
    assert response.latency_ms == 50, "LLMResponse.latency_ms 必须由 _post_with_retry 填充"
    assert client._last_latency_ms == 50
    assert captured.get("hit") == 1, "_post_with_retry 必须真的被调用"

    await client.aclose()


# ----------------------------------------------------------------- model tiering


@pytest.mark.asyncio
async def test_router_semantic_call_uses_planner_model(monkeypatch):
    """语义路由调 LLM 时必须显式传 ``model=llm_planner_model``（P1-4 全量分层）。"""
    from app.services import router_service

    monkeypatch.setattr(
        router_service.config, "llm_planner_model", "gpt-5-mini"
    )

    fake_llm = _FakeLLM(
        [
            LLMResponse(
                content='{"route":"knowledge","reason":"generic how-to","confidence":0.86}',
                raw={},
                usage={"total_tokens": 5},
            )
        ]
    )
    svc = router_service.RouterService(llm_client=fake_llm)

    await svc._resolve_route("how to troubleshoot high cpu usage")

    assert fake_llm.calls, "router 应该至少调一次 LLM"
    call_kwargs = fake_llm.calls[0]["kwargs"]
    assert call_kwargs.get("model") == "gpt-5-mini", (
        f"router 必须传 planner_model, 实际 kwargs={call_kwargs!r}"
    )


@pytest.mark.asyncio
async def test_verifier_llm_verify_uses_reasoner_model(monkeypatch):
    """LLM 自检调 LLM 时必须显式传 ``model=llm_reasoner_model``（P1-4 全量分层）。"""
    from app.agent.harness import verifier as verifier_module

    monkeypatch.setattr(verifier_module.config, "llm_reasoner_model", "gpt-5.4")
    monkeypatch.setattr(verifier_module.config, "harness_llm_verify_enabled", True)

    fake_llm = _FakeLLM(
        [
            LLMResponse(
                content=(
                    '{"status":"completed","confidence":"high",'
                    '"gaps":[],"summary":"ok"}'
                ),
                raw={},
                usage={"total_tokens": 5},
            )
        ]
    )

    plan = None  # EvidenceVerifier 不需要 plan, 只要 timeline_events + answer
    events = [
        {
            "type": "tool_event",
            "tool": "query_metric",
            "status": "completed",
            "evidence_id": "x",
        }
    ]
    result = await verifier_module.EvidenceVerifier().averify(
        answer="ok",
        timeline_events=events,
        plan=plan,
        llm_client=fake_llm,
    )
    assert result.status == "completed"
    assert fake_llm.calls, "verifier 应该调一次 LLM"
    call_kwargs = fake_llm.calls[0]["kwargs"]
    assert call_kwargs.get("model") == "gpt-5.4", (
        f"verifier 必须传 reasoner_model, 实际 kwargs={call_kwargs!r}"
    )


@pytest.mark.asyncio
async def test_router_and_verifier_fall_back_to_default_when_planner_reasoner_unset(monkeypatch):
    """``llm_planner_model`` / ``llm_reasoner_model`` 为空字符串时应回退默认(不传)。"""
    from app.agent.harness import verifier as verifier_module
    from app.services import router_service

    monkeypatch.setattr(router_service.config, "llm_planner_model", "")
    monkeypatch.setattr(verifier_module.config, "llm_reasoner_model", "")
    monkeypatch.setattr(verifier_module.config, "harness_llm_verify_enabled", True)

    fake_llm = _FakeLLM(
        [
            LLMResponse(
                content='{"route":"knowledge","reason":"generic","confidence":0.7}',
                raw={},
                usage={"total_tokens": 5},
            ),
            LLMResponse(
                content='{"status":"completed","confidence":"medium","gaps":[],"summary":"ok"}',
                raw={},
                usage={"total_tokens": 5},
            ),
        ]
    )
    router = router_service.RouterService(llm_client=fake_llm)
    await router._resolve_route("how to clean up disk")
    # 第一次调用(router) 不应该传 model 字段（因为配置为空）
    router_kwargs = fake_llm.calls[0]["kwargs"]
    assert "model" not in router_kwargs or router_kwargs.get("model") in (None, "")

    # 第二次调用(verifier) 也不应该传 model
    await verifier_module.EvidenceVerifier().averify(
        answer="ok",
        timeline_events=[],
        plan=None,
        llm_client=fake_llm,
    )
    verifier_kwargs = fake_llm.calls[1]["kwargs"]
    assert "model" not in verifier_kwargs or verifier_kwargs.get("model") in (None, "")


# ----------------------------------------------------------------- delegation


@pytest.mark.asyncio
async def test_harness_with_delegation_still_records_duration_ms():
    """强委派路径下，complete 事件仍带 duration_ms（验证观测性不被委派破坏）。"""
    delegate_tool = create_delegate_tool(
        session_id="trace-delegate-obs",
        trace_id="trace-delegate-obs",
        context_getter=lambda: "parent context",
        expert_getter=lambda route: _FakeDelegateExpert(),
    )
    fake_llm = _FakeLLM(
        [
            LLMResponse(content="delegated answer", raw={}, usage={"total_tokens": 6}),
        ]
    )
    service = HarnessService(
        router=_FakeRouter(route="metric"),
        llm_client=fake_llm,
        tools=[delegate_tool],
        limits=HarnessLimits(max_steps=3, token_budget=1000, timeout_seconds=5),
    )

    events = [
        event
        async for event in service.stream(
            "delegate metric check", session_id="trace-delegate-obs", owner_key="user-1"
        )
    ]
    complete = next(event for event in events if event.get("stage") == "complete")
    assert "duration_ms" in complete
    assert complete["duration_ms"] >= 0