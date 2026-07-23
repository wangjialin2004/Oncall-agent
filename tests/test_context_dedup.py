"""Focused coverage for the context deduplication rollout."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent.context.operations import append_observed_fact, append_tool_summary, set_intent
from app.agent.context.integration import build_stateful_context
from app.agent.context.store import ContextStateStore, ContextStateStoreSettings
from app.agent.context.state import AgentContextState, EvidenceItem, ToolSummary
from app.agent.context.views import render_context_view
from app.agent.harness.loop import HarnessService
from app.api import assistant as assistant_api
from app.api.assistant import _select_attachment_runtime_context
from app.config import config
from app.models.request import ChatRequest
from app.services.attachment_reference_service import (
    AttachmentReference,
    strip_attachment_wrapper,
)


class _MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def __call__(self, op: str, key: str, value, ttl):
        if op == "get":
            return self.data.get(key)
        if op == "set":
            self.data[key] = str(value)
            return True
        raise ValueError(op)


def _store() -> ContextStateStore:
    redis = _MemoryRedis()
    return ContextStateStore(
        db_snapshot_get=lambda **_: None,
        db_snapshot_save=lambda **_: None,
        redis_get_set=redis,
        settings=ContextStateStoreSettings(
            redis_enabled=True,
            redis_namespace="dedup-test",
            redis_ttl_seconds=300,
            db_snapshot_enabled=False,
            patch_history_limit=20,
        ),
    )


def _reference() -> AttachmentReference:
    return AttachmentReference(
        file_id="file_1",
        file_name="runbook.md",
        summary="CPU runbook summary",
        keywords=("cpu",),
        status="indexed",
    )


def test_dedup_defaults_are_explicit() -> None:
    assert config.harness_context_intent_omit_from_view_enabled is True
    assert config.harness_attachment_prompt_mode == "summary"
    assert config.harness_preference_inject_mode == "router_and_harness"
    assert config.harness_context_view_dedup_tool_evidence is True
    assert config.harness_context_size_metrics_enabled is False


def test_render_omits_duplicate_intent_by_default(monkeypatch) -> None:
    monkeypatch.setattr(config, "harness_context_intent_omit_from_view_enabled", True)
    state = AgentContextState()
    set_intent(state, current_question="redis ok?", current_goal="redis ok?")

    view = render_context_view(state)

    assert "current_question" not in view
    assert "current_goal" not in view


def test_render_intent_rollback_and_distinct_goal(monkeypatch) -> None:
    state = AgentContextState()
    set_intent(state, current_question="redis ok?", current_goal="check redis health")

    monkeypatch.setattr(config, "harness_context_intent_omit_from_view_enabled", True)
    view = render_context_view(state)
    assert "current_question" not in view
    assert "current_goal: check redis health" in view

    monkeypatch.setattr(config, "harness_context_intent_omit_from_view_enabled", False)
    rollback_view = render_context_view(state)
    assert "current_question: redis ok?" in rollback_view
    assert "current_goal: check redis health" in rollback_view


def test_render_deduplicates_tool_fact_by_raw_ref(monkeypatch) -> None:
    monkeypatch.setattr(config, "harness_context_view_dedup_tool_evidence", True)
    state = AgentContextState()
    append_observed_fact(
        state,
        EvidenceItem(
            source="check_redis",
            status="success",
            facts=["pong"],
            raw_ref="timeline:1",
        ),
    )
    append_tool_summary(
        state,
        ToolSummary(
            tool="check_redis",
            status="success",
            latency_ms=4,
            note="pong",
            raw_ref="timeline:1",
        ),
    )

    view = render_context_view(state)

    assert view.count("pong") == 1
    assert "observed_facts: (deduped; see tool_summaries)" in view

    monkeypatch.setattr(config, "harness_context_view_dedup_tool_evidence", False)
    rollback_view = render_context_view(state)
    assert rollback_view.count("pong") >= 2


def test_stamp_strips_attachment_full_text() -> None:
    state = AgentContextState()
    wrapped = (
        "以下是与当前问题相关的附件材料。\n\n"
        "[附件 runbook.md]\ncontent:\nSECRET FULL TEXT\n\n"
        "用户问题：\n请总结附件"
    )

    HarnessService._stamp_current_turn(
        state,
        user_message=wrapped,
        assistant_answer="done",
    )

    assert state.conversation.recent_turns[0]["content"] == "请总结附件"
    assert "SECRET FULL TEXT" not in str(state.conversation.recent_turns)


def test_stamp_cleans_legacy_wrapped_history() -> None:
    state = AgentContextState()
    state.conversation.recent_turns = [
        {
            "role": "user",
            "content": "[附件 old.md]\ncontent:\nOLD SECRET\n\n用户问题：\n旧问题",
        },
        {"role": "assistant", "content": "old answer"},
    ]

    HarnessService._stamp_current_turn(
        state,
        user_message="new question",
        assistant_answer="new answer",
    )

    assert state.conversation.recent_turns[0]["content"] == "旧问题"
    assert "OLD SECRET" not in str(state.conversation.recent_turns)


def test_strip_attachment_wrapper_leaves_plain_text() -> None:
    assert strip_attachment_wrapper("plain question") == "plain question"


async def test_stateful_intent_stores_raw_question() -> None:
    wrapped = "附件材料\n[附件 x]\ncontent:\nSECRET\n\n用户问题：\n请分析"
    ctx = await build_stateful_context(
        owner_key="owner",
        session_id="session",
        current_question=wrapped,
        current_goal=wrapped,
        store=_store(),
    )
    assert ctx.state.intent.current_question == "请分析"
    assert ctx.state.intent.current_goal == "请分析"
    assert "SECRET" not in ctx.view


def test_attachment_prompt_modes() -> None:
    reference = _reference()
    full = "[附件 runbook.md]\nfile_id: file_1\ncontent:\nFULL SECRET"
    summary = "[附件摘要 runbook.md]\nfile_id: file_1\nsummary:\nCPU runbook summary"

    compact = _select_attachment_runtime_context(
        question="请总结这个附件",
        mode="summary",
        full_context=full,
        summary_context=summary,
        references=[reference],
        fallback_file_ids=["file_1"],
    )
    assert "CPU runbook summary" in compact
    assert "FULL SECRET" not in compact

    assert (
        _select_attachment_runtime_context(
            question="请总结这个附件",
            mode="full",
            full_context=full,
            summary_context=summary,
            references=[reference],
            fallback_file_ids=["file_1"],
        )
        == full
    )

    indexed = _select_attachment_runtime_context(
        question="请总结这个附件",
        mode="index",
        full_context=full,
        summary_context=summary,
        references=[reference],
        fallback_file_ids=["file_1"],
    )
    assert "file_id=file_1" in indexed
    assert "CPU runbook summary" not in indexed
    assert "FULL SECRET" not in indexed

    strict_index = _select_attachment_runtime_context(
        question="请给出全文原文",
        mode="index",
        full_context=full,
        summary_context=summary,
        references=[reference],
        fallback_file_ids=["file_1"],
    )
    assert "file_id=file_1" in strict_index
    assert "FULL SECRET" not in strict_index


def test_attachment_summary_upgrades_for_explicit_english_detail_request() -> None:
    reference = _reference()
    full = "[附件 runbook.md]\nfile_id: file_1\ncontent:\nFULL SECRET"
    summary = "[附件摘要 runbook.md]\nfile_id: file_1\nsummary:\nCPU runbook summary"
    detailed = _select_attachment_runtime_context(
        question="show the full text of this attachment",
        mode="summary",
        full_context=full,
        summary_context=summary,
        references=[reference],
        fallback_file_ids=["file_1"],
    )
    assert detailed == full


@pytest.mark.asyncio
async def test_load_attachment_payload_default_summary(monkeypatch) -> None:
    monkeypatch.setattr(config, "harness_attachment_prompt_mode", "summary")
    monkeypatch.setattr(
        assistant_api.attachment_context_service,
        "build_context",
        AsyncMock(
            return_value=(
                "[附件 runbook.md]\n"
                "file_id: file_1\n"
                "status: indexed\n"
                f"content:\n{'A' * 700}SECRET_TAIL_CPU_DETAILS"
            )
        ),
    )

    payload = await assistant_api._load_attachment_payload(
        "owner-1",
        ChatRequest(id="session-1", question="请总结这个附件", attachment_ids=["file_1"]),
    )

    assert "SECRET_TAIL_CPU_DETAILS" not in payload.runtime_context
    assert "runbook" in payload.runtime_context
    assert payload.attachment_refs[0]["file_id"] == "file_1"
    assert "SECRET_TAIL_CPU_DETAILS" not in payload.persistent_context


@pytest.mark.asyncio
async def test_index_mode_degrades_without_context_tools(monkeypatch) -> None:
    monkeypatch.setattr(config, "harness_attachment_prompt_mode", "index")
    monkeypatch.setattr(config, "harness_stateful_context_enabled", False)
    monkeypatch.setattr(config, "harness_context_tools_enabled", False)
    monkeypatch.setattr(
        assistant_api.attachment_context_service,
        "build_context",
        AsyncMock(
            return_value=(
                "[附件 runbook.md]\nfile_id: file_1\ncontent:\n"
                + "A" * 700
                + "TAIL"
            )
        ),
    )

    payload = await assistant_api._load_attachment_payload(
        "owner-1",
        ChatRequest(id="session-1", question="请总结这个附件", attachment_ids=["file_1"]),
    )

    assert "summary" in payload.runtime_context
    assert "file_id=file_1" not in payload.runtime_context
