import pytest

import app.services.router_service as router_module
from app.services.router_service import RouterService


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
async def test_answer_dispatches_to_rag(monkeypatch):
    service = RouterService()

    async def fake_query(message, session_id):
        return f"rag:{session_id}:{message}"

    monkeypatch.setattr(router_module.rag_agent_service, "query", fake_query)

    result = await service.answer("怎么处理慢响应", session_id="s1")

    assert result == {
        "success": True,
        "route": "rag",
        "answer": "rag:s1:怎么处理慢响应",
        "errorMessage": None,
    }


@pytest.mark.asyncio
async def test_answer_collects_aiops_final_response(monkeypatch):
    service = RouterService()

    async def fake_execute(message, session_id):
        yield {"type": "status", "message": "running"}
        yield {"type": "complete", "case_id": "case-1", "response": "# report"}

    monkeypatch.setattr(router_module.aiops_service, "execute", fake_execute)

    result = await service.answer("帮我诊断 CPU 告警", session_id="s1")

    assert result == {
        "success": True,
        "route": "aiops",
        "case_id": "case-1",
        "answer": "# report",
        "errorMessage": None,
    }


@pytest.mark.asyncio
async def test_answer_reports_aiops_error_event(monkeypatch):
    service = RouterService()

    async def fake_execute(message, session_id):
        yield {"type": "status", "message": "running"}
        yield {"type": "error", "message": "diagnosis failed"}

    monkeypatch.setattr(router_module.aiops_service, "execute", fake_execute)

    result = await service.answer("甯垜璇婃柇 CPU 鍛婅", session_id="s1")

    assert result == {
        "success": False,
        "route": "aiops",
        "case_id": "",
        "answer": None,
        "errorMessage": "diagnosis failed",
    }
