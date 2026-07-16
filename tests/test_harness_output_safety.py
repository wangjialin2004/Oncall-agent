from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.agent.harness.context import HarnessContext
from app.agent.harness.events_emit import HarnessEventsMixin
from app.agent.harness.loop import HarnessService
from app.agent.harness.output_safety import (
    contains_internal_tool_protocol,
    sanitize_user_visible_answer,
)
from app.agent.harness.planner import HarnessPlan
from app.agent.harness.state import HarnessLimits
from app.agent.harness.verifier import VerificationResult
from app.core.llm_client import LLMResponse, LLMStreamChunk, ToolCall
from app.core.runtime_tools import RuntimeTool
from app.services.router_service import RouteDecision

LEAKED_PAYMENT_SERVICE_ANSWER = """我先补一次最相关的日志证据，验证是否存在直接 OOM 日志。 to=multi_tool_use.parallel  乐彩广告
{"tool_uses":[{"recipient_name":"functions.search_app_logs","parameters":{"keyword":"OutOfMemoryError"}},{"recipient_name":"functions.get_current_time","parameters":{"timezone":"Asia/Shanghai"}}]}## 现象
你关注的是：`payment-service` 内存使用率超过 85%，当前是否存在 **OOM 风险**。
"""


def test_detects_internal_tool_protocol_without_hiding_business_json() -> None:
    assert contains_internal_tool_protocol(LEAKED_PAYMENT_SERVICE_ANSWER) is True
    assert contains_internal_tool_protocol('业务返回：{"status":"ok"}') is False
    assert contains_internal_tool_protocol("普通正文 recipient_name 字段说明") is False


def test_sanitize_removes_protocol_block_and_keeps_final_answer() -> None:
    cleaned = sanitize_user_visible_answer(LEAKED_PAYMENT_SERVICE_ANSWER)

    assert "to=multi_tool_use.parallel" not in cleaned
    assert "tool_uses" not in cleaned
    assert "recipient_name" not in cleaned
    assert "## 现象" in cleaned
    assert "payment-service" in cleaned


def test_sanitize_preserves_normal_markdown_and_business_json() -> None:
    answer = """## 接口结果

```json
{"status":"ok","recipient_name":"业务联系人"}
```
"""

    assert sanitize_user_visible_answer(answer) == answer.strip()


def test_corrective_notice_is_readable_chinese() -> None:
    result = VerificationResult(
        status="degraded",
        confidence="medium",
        evidence_count=1,
        failed_evidence_count=1,
        gaps=["4 个工具调用失败或被降级"],
        summary="证据不足",
    )

    answer = HarnessEventsMixin._apply_corrective_notice("## 现象\n正文", result)

    assert "⚠️ 证据自检：置信度 medium" in answer
    assert "本次回答存在以下证据缺口，请谨慎采用" in answer
    assert "鈿" not in answer
    assert "璇佹嵁" not in answer


class _ContextBuilder:
    async def abuild(self, **_: Any) -> HarnessContext:
        return HarnessContext(system_prompt="只读诊断", history_messages=[])


class _Router:
    async def _resolve_route(self, _: str) -> RouteDecision:
        return RouteDecision(route="diagnosis", reason="test", confidence=0.9)


class _Planner:
    async def acreate(self, **_: Any) -> HarnessPlan:
        return HarnessPlan(
            todos=["读取证据"],
            required_evidence=[],
            focus_route="diagnosis",
            available_tools=["search_app_logs"],
        )


class _Clarifier:
    def check(self, **_: Any) -> None:
        return None


class _Verifier:
    async def averify(self, **_: Any) -> VerificationResult:
        return VerificationResult(
            status="completed",
            summary="证据充分",
            confidence="high",
            evidence_count=1,
            failed_evidence_count=0,
            gaps=[],
        )


@dataclass
class _StreamingLLM:
    responses: list[LLMResponse]

    def _next(self) -> LLMResponse:
        return self.responses.pop(0)

    async def stream_chat(self, *_: Any, **__: Any):
        response = self._next()
        if response.content:
            midpoint = max(1, len(response.content) // 2)
            for chunk in (response.content[:midpoint], response.content[midpoint:]):
                if chunk:
                    yield LLMStreamChunk(content=chunk)
        yield LLMStreamChunk(response=response)

    async def stream_complete(self, *_: Any, **__: Any):
        response = self._next()
        if response.content:
            yield response.content


def _service(llm: _StreamingLLM, executed: list[str]) -> HarnessService:
    async def handler(_: dict[str, Any]) -> dict[str, bool]:
        executed.append("search_app_logs")
        return {"success": True}

    service = HarnessService(
        context_builder=_ContextBuilder(),
        router=_Router(),
        llm_client=llm,
        tools=[
            RuntimeTool(
                name="search_app_logs",
                description="只读搜索日志",
                handler=handler,
            )
        ],
        limits=HarnessLimits(
            max_steps=1,
            token_budget=20_000,
            timeout_seconds=5,
            step_timeout_seconds=2,
            fallback_timeout_seconds=2,
        ),
    )
    service.planner = _Planner()
    service.clarifier = _Clarifier()
    service.verifier = _Verifier()
    service._should_force_seed_parallel = lambda **_: False  # type: ignore[method-assign]
    service._should_force_seed_delegate = lambda **_: False  # type: ignore[method-assign]
    service._maybe_distill_experience = lambda **_: None  # type: ignore[method-assign]
    service._maybe_capture_anti_pattern = lambda **_: None  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_structured_tool_decision_content_is_not_user_visible(monkeypatch) -> None:
    monkeypatch.setattr("app.agent.harness.stream_inner.stateful_context_enabled", lambda: False)
    executed: list[str] = []
    llm = _StreamingLLM(
        responses=[
            LLMResponse(
                content="我先查一下内部日志。",
                raw={},
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="search_app_logs",
                        arguments={"keyword": "OOM"},
                    )
                ],
                usage={},
            ),
            LLMResponse(content="## 结论\n未发现 OOM 直接证据。", raw={}, tool_calls=[], usage={}),
        ]
    )

    events = [event async for event in _service(llm, executed).stream("检查 OOM", "safe-1")]
    visible = "".join(
        str(event.get("data") or "") for event in events if event.get("type") == "content"
    )
    complete = next(event for event in reversed(events) if event.get("type") == "complete")

    assert executed == ["search_app_logs"]
    assert "我先查一下内部日志" not in visible
    assert "我先查一下内部日志" not in complete["answer"]
    assert "未发现 OOM 直接证据" in complete["answer"]


@pytest.mark.asyncio
async def test_text_tool_protocol_is_not_executed_or_exposed(monkeypatch) -> None:
    monkeypatch.setattr("app.agent.harness.stream_inner.stateful_context_enabled", lambda: False)
    executed: list[str] = []
    llm = _StreamingLLM(
        responses=[
            LLMResponse(
                content=LEAKED_PAYMENT_SERVICE_ANSWER,
                raw={},
                tool_calls=[],
                usage={},
            ),
            LLMResponse(
                content="## 结论\n基于现有证据生成的干净结论。", raw={}, tool_calls=[], usage={}
            ),
        ]
    )

    events = [event async for event in _service(llm, executed).stream("检查内存", "safe-2")]
    visible = "".join(
        str(event.get("data") or "") for event in events if event.get("type") == "content"
    )
    complete = next(event for event in reversed(events) if event.get("type") == "complete")

    assert executed == []
    assert "tool_uses" not in visible
    assert "recipient_name" not in visible
    assert "基于现有证据生成的干净结论" in complete["answer"]
    assert any(event.get("stage") == "tool_protocol_degraded" for event in events)
