from __future__ import annotations

import json

import pytest

from app.api.assistant import assistant
from app.api.conversations import get_conversation
from app.config import config
from app.models.request import ChatRequest
from app.services.conversation_service import ConversationService
from app.services.context_repository import ContextRepository, ContextRepositorySettings
from app.services.session_scope_service import AuthenticatedPrincipal
from tests._context_db import initialize_context_db


class PrivateProgressStream:
    async def stream(self, message: str, session_id: str, owner_key: str = ""):
        del message, session_id, owner_key
        private_tool = {
            "type": "tool_event",
            "agent": "harness",
            "tool": "search_app_logs",
            "status": "completed",
            "evidence_id": "private-call-id",
            "summary": "SECRET_ARGUMENT SECRET_RESULT",
            "payload": {
                "arguments": {"keyword": "SECRET_ARGUMENT"},
                "result": "SECRET_RESULT",
            },
        }
        yield {
            "type": "agent_event",
            "agent": "harness",
            "stage": "tool_start",
            "status": "in_progress",
            "payload": {"tool": "search_app_logs", "tool_call_id": "private-call-id"},
        }
        yield private_tool
        yield {"type": "content", "data": "Public answer"}
        yield {
            "type": "complete",
            "route": "diagnosis",
            "answer": "Public answer",
            "case_id": "case-public-progress",
            "events": [private_tool],
        }


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        username="test-user",
        owner_key="owner-1",
        storage_owner_key="owner-1",
        project_id="default",
        role="operator",
    )


def _patch_unified_committer(monkeypatch, db_path):
    from app.agent.context import unified as unified_mod

    repository = ContextRepository(
        db_path=db_path,
        settings=ContextRepositorySettings(redis_enabled=False, db_snapshot_enabled=True),
    )
    real_build = unified_mod.build_completion_committer

    def build_committer(**kwargs):
        kwargs["repository"] = repository
        return real_build(**kwargs)

    monkeypatch.setattr("app.api.assistant.build_completion_committer", build_committer)
    return repository


@pytest.mark.asyncio
async def test_live_complete_and_history_share_the_safe_public_contract(
    tmp_path, monkeypatch
) -> None:
    db_path = initialize_context_db(tmp_path / "conversation.db")
    conversations = ConversationService(db_path)
    _patch_unified_committer(monkeypatch, db_path)
    monkeypatch.setattr("app.api.assistant.harness_service", PrivateProgressStream())
    monkeypatch.setattr("app.api.assistant.conversation_service", conversations)
    monkeypatch.setattr("app.api.conversations.conversation_service", conversations)

    response = await assistant(
        ChatRequest(id="public-progress-session", question="check the service"),
        principal=_principal(),
    )
    streamed = [
        json.loads(item["data"])
        async for item in response.body_iterator
        if isinstance(item, dict) and item.get("data")
    ]

    assert [item["type"] for item in streamed] == [
        "agent_event",
        "tool_event",
        "content",
        "complete",
    ]
    assert streamed[0]["payload"]["tool_call_id"] == "activity-1"
    assert streamed[1]["payload"]["tool_call_id"] == "activity-1"
    assert streamed[-1]["events"][0]["payload"]["tool_call_id"] == "activity-1"

    history = await get_conversation(
        "public-progress-session", owner_key="owner-1"
    )
    restored_event = history["data"]["turns"][0]["events"][0]
    assert restored_event["tool"] == "search_app_logs"
    assert restored_event["payload"]["tool_call_id"] == "activity-1"

    public_json = json.dumps({"stream": streamed, "history": history})
    assert "SECRET_ARGUMENT" not in public_json
    assert "SECRET_RESULT" not in public_json
    assert "private-call-id" not in public_json

    internal = conversations.get_turns("owner-1", "public-progress-session")
    internal_json = json.dumps(internal)
    assert "SECRET_ARGUMENT" in internal_json
    assert "SECRET_RESULT" in internal_json
    assert "private-call-id" in internal_json


@pytest.mark.asyncio
async def test_live_and_history_expose_safe_progress_details(tmp_path, monkeypatch) -> None:
    db_path = initialize_context_db(tmp_path / "conversation.db")
    conversations = ConversationService(db_path)
    _patch_unified_committer(monkeypatch, db_path)
    monkeypatch.setattr("app.api.assistant.conversation_service", conversations)
    monkeypatch.setattr("app.api.conversations.conversation_service", conversations)
    monkeypatch.setattr(config, "harness_public_progress_details_enabled", True)

    class ProgressStream:
        async def stream(self, message: str, session_id: str, owner_key: str = ""):
            del message, session_id, owner_key
            plan_event = {
                "type": "agent_event",
                "agent": "harness",
                "stage": "plan",
                "status": "completed",
                "payload": {
                    "todos": ["查询指标", "核对日志"],
                    "required_evidence": ["指标证据", "日志证据"],
                },
            }
            tool_event = {
                "type": "tool_event",
                "agent": "harness",
                "tool": "query_metrics",
                "status": "completed",
                "evidence_id": "internal-call",
                "payload": {
                    "arguments": {"tenant": "private-tenant"},
                    "result": '{"status":"success","count":1,"items":[{"summary":"CPU 91%"}]}',
                },
            }
            verify_event = {
                "type": "agent_event",
                "agent": "harness",
                "stage": "verify",
                "status": "degraded",
                "payload": {
                    "confidence": "low",
                    "evidence_count": 1,
                    "failed_evidence_count": 0,
                    "gaps": ["缺少日志证据"],
                },
            }
            yield plan_event
            yield tool_event
            yield verify_event
            yield {"type": "content", "data": "Public answer"}
            yield {
                "type": "complete",
                "route": "diagnosis",
                "answer": "Public answer",
                "case_id": "case-details",
                "events": [plan_event, tool_event, verify_event],
            }

    monkeypatch.setattr("app.api.assistant.harness_service", ProgressStream())
    response = await assistant(
        ChatRequest(id="public-details-session", question="check the service"),
        principal=_principal(),
    )
    streamed = [
        json.loads(item["data"])
        async for item in response.body_iterator
        if isinstance(item, dict) and item.get("data")
    ]

    assert streamed[0]["payload"]["todos"] == ["查询指标", "核对日志"]
    assert streamed[1]["payload"]["result_items"] == ["CPU 91%"]
    assert streamed[2]["payload"]["gaps"] == ["缺少日志证据"]
    history = await get_conversation("public-details-session", owner_key="owner-1")
    history_events = history["data"]["turns"][0]["events"]
    assert history_events[0]["payload"]["todos"] == ["查询指标", "核对日志"]
    assert history_events[1]["payload"]["result_items"] == ["CPU 91%"]
    assert history_events[2]["payload"]["gaps"] == ["缺少日志证据"]
    public_json = json.dumps({"stream": streamed, "history": history}, ensure_ascii=False)
    assert "private-tenant" not in public_json
    assert "internal-call" not in public_json
