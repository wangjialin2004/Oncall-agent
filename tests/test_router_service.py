import pytest

from app.core.llm_client import LLMResponse
import app.services.router_service as router_module
from app.services.router_service import RouteDecision, RouterService


def test_route_message_detects_aiops_intent():
    service = RouterService()

    decision = service.route_message("CPU 告警了，帮我诊断服务日志")

    assert decision.route == "aiops"
    assert decision.reason == "matched_aiops_keyword"


def test_route_message_detects_rag_intent():
    service = RouterService()

    decision = service.route_message("请说明知识库里的慢响应排查步骤")

    assert decision.route == "rag"
    assert decision.reason == "matched_rag_keyword"


def test_route_message_clarifies_empty_input():
    service = RouterService()

    decision = service.route_message("   ")

    assert decision.route == "clarify"
    assert decision.reason == "empty_message"


def test_route_message_clarifies_punctuation_only_input():
    service = RouterService()

    decision = service.route_message("??? ...")

    assert decision.route == "clarify"
    assert decision.reason == "no_meaningful_text"


@pytest.mark.asyncio
async def test_answer_uses_custom_llm_client_for_default_route(monkeypatch):
    class FakeLLMClient:
        def __init__(self):
            self.messages = None
            self.temperature = None

        async def complete(self, messages, *, temperature):
            self.messages = messages
            self.temperature = temperature
            return LLMResponse(
                content='{"route":"aiops","reason":"transaction is stuck"}',
                raw={},
            )

    llm_client = FakeLLMClient()
    service = RouterService(llm_client=llm_client)

    async def fake_execute(message, session_id):
        yield {"type": "complete", "case_id": "case-custom", "response": "# custom llm diagnosis"}

    monkeypatch.setattr(router_module.aiops_service, "execute", fake_execute)

    result = await service.answer("user checkout keeps spinning", session_id="s1")

    assert llm_client.temperature == 0
    assert llm_client.messages[0].role == "system"
    assert llm_client.messages[1].content == "user checkout keeps spinning"
    assert result["route"] == "aiops"
    assert result["route_reason"] == "llm_semantic_aiops"
    assert result["case_id"] == "case-custom"
    assert result["answer"] == "# custom llm diagnosis"


@pytest.mark.asyncio
async def test_answer_uses_llm_semantic_route_for_aiops_without_keywords(monkeypatch):
    service = RouterService(semantic_router=lambda message: RouteDecision("aiops", "llm_semantic_aiops"))

    async def fake_execute(message, session_id):
        yield {"type": "complete", "case_id": "case-1", "response": "# diagnosis"}

    monkeypatch.setattr(router_module.aiops_service, "execute", fake_execute)

    result = await service.answer("用户下单一直转圈，帮忙看下", session_id="s1")

    assert result == {
        "success": True,
        "route": "aiops",
        "route_reason": "llm_semantic_aiops",
        "case_id": "case-1",
        "answer": "# diagnosis",
        "events": [],
        "errorMessage": None,
    }


@pytest.mark.asyncio
async def test_answer_parses_custom_llm_json_for_default_route(monkeypatch):
    class FakeLLMClient:
        async def complete(self, messages, *, temperature):
            return LLMResponse(
                content='{"route":"aiops","reason":"transaction is stuck"}',
                raw={},
            )

    service = RouterService(llm_client=FakeLLMClient())

    async def fake_execute(message, session_id):
        yield {"type": "complete", "case_id": "case-2", "response": "# semantic diagnosis"}

    monkeypatch.setattr(router_module.aiops_service, "execute", fake_execute)

    result = await service.answer("用户下单一直转圈，帮忙看下", session_id="s1")

    assert result["route"] == "aiops"
    assert result["route_reason"] == "llm_semantic_aiops"
    assert result["case_id"] == "case-2"
    assert result["answer"] == "# semantic diagnosis"


@pytest.mark.asyncio
async def test_answer_falls_back_to_rag_when_semantic_route_fails(monkeypatch):
    def failing_semantic_router(message):
        raise RuntimeError("llm unavailable")

    service = RouterService(semantic_router=failing_semantic_router)

    async def fake_query(message, session_id):
        return "rag fallback"

    monkeypatch.setattr(router_module.rag_agent_service, "query", fake_query)

    result = await service.answer("用户下单一直转圈，帮忙看下", session_id="s1")

    assert result == {
        "success": True,
        "route": "rag",
        "route_reason": "semantic_route_failed_default_rag",
        "answer": "rag fallback",
        "errorMessage": None,
    }


@pytest.mark.asyncio
async def test_answer_includes_route_reason_for_clarification():
    service = RouterService()

    result = await service.answer("   ", session_id="s1")

    assert result["route"] == "clarify"
    assert result["route_reason"] == "empty_message"


@pytest.mark.asyncio
async def test_answer_dispatches_to_rag(monkeypatch):
    service = RouterService()

    async def fake_query(message, session_id):
        return f"rag:{session_id}:{message}"

    monkeypatch.setattr(router_module.rag_agent_service, "query", fake_query)

    result = await service.answer("怎么处理慢响应", session_id="s1")

    assert result == {
        "success": True,
        "route": "rag",
        "route_reason": "matched_rag_keyword",
        "answer": "rag:s1:怎么处理慢响应",
        "errorMessage": None,
    }


@pytest.mark.asyncio
async def test_answer_collects_aiops_final_response(monkeypatch):
    service = RouterService()

    async def fake_execute(message, session_id):
        yield {"type": "status", "message": "running"}
        yield {"type": "agent_event", "agent": "triage", "summary": "started"}
        yield {
            "type": "complete",
            "case_id": "case-1",
            "response": "# report",
            "events": [
                {"type": "agent_event", "agent": "triage", "summary": "started"},
                {"type": "decision_event", "decision": "report", "summary": "done"},
            ],
        }

    monkeypatch.setattr(router_module.aiops_service, "execute", fake_execute)

    result = await service.answer("帮我诊断 CPU 告警", session_id="s1")

    assert result == {
        "success": True,
        "route": "aiops",
        "route_reason": "matched_aiops_keyword",
        "case_id": "case-1",
        "answer": "# report",
        "events": [
            {"type": "agent_event", "agent": "triage", "summary": "started"},
            {"type": "decision_event", "decision": "report", "summary": "done"},
        ],
        "errorMessage": None,
    }


@pytest.mark.asyncio
async def test_answer_reports_aiops_error_event(monkeypatch):
    service = RouterService()

    async def fake_execute(message, session_id):
        yield {"type": "status", "message": "running"}
        yield {"type": "tool_event", "tool": "logs", "summary": "queried"}
        yield {"type": "error", "message": "diagnosis failed"}

    monkeypatch.setattr(router_module.aiops_service, "execute", fake_execute)

    result = await service.answer("甯垜璇婃柇 CPU 鍛婅", session_id="s1")

    assert result == {
        "success": False,
        "route": "aiops",
        "route_reason": "matched_aiops_keyword",
        "case_id": "",
        "answer": None,
        "events": [{"type": "tool_event", "tool": "logs", "summary": "queried"}],
        "errorMessage": "diagnosis failed",
    }
