"""HITL suggested-action confirm — audit only, never executes remediation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config
from app.services.conversation_service import conversation_service
from app.services.session_scope_service import require_session_owner

router = APIRouter()

# In-process audit log for pilot (not a durable store).
_AUDIT: list[dict[str, Any]] = []


class ConfirmSuggestionRequest(BaseModel):
    session_id: str = Field(..., alias="SessionId")
    action_id: str = Field(..., alias="ActionId")
    note: str = Field(default="", alias="Note")

    model_config = {"populate_by_name": True}


@router.post("/hitl/confirm-suggestion")
async def confirm_suggestion(
    body: ConfirmSuggestionRequest,
    owner_key: str = Depends(require_session_owner),
) -> dict[str, Any]:
    """Record operator acknowledgement of a *suggested* action.

    Does **not** invoke restart/rollback/scale executors. Read-only product red line.
    """
    if not body.session_id.strip() or not body.action_id.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_suggestion",
                "message": "invalid request",
                "detail": "session_and_action_required",
            },
        )
    authenticated_owner = owner_key if isinstance(owner_key, str) else ""
    if authenticated_owner and not _suggestion_exists(
        authenticated_owner,
        body.session_id,
        body.action_id,
    ):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "suggestion_not_found",
                "message": "not found",
                "detail": "suggestion_not_found",
            },
        )
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "session_id": body.session_id,
        "action_id": body.action_id,
        "note": (body.note or "")[:500],
        "executed": False,
        "owner_key": authenticated_owner,
        "project_id": config.project_id,
        "trace_id": uuid.uuid4().hex,
    }
    _AUDIT.append(entry)
    if len(_AUDIT) > 500:
        del _AUDIT[:-500]
    logger.info(
        "HITL confirm (audit only): session={} action={} executed=false",
        body.session_id,
        body.action_id,
    )
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "accepted": True,
            "executed": False,
            "audit": entry,
            "hint": "确认已记录；系统不会自动执行变更/重启/回滚。",
        },
    }


def get_audit_log() -> list[dict[str, Any]]:
    """Test helper."""
    return list(_AUDIT)


def clear_audit_log() -> None:
    _AUDIT.clear()


def _suggestion_exists(owner_key: str, session_id: str, action_id: str) -> bool:
    for turn in conversation_service.get_turns(owner_key, session_id):
        for event in turn.get("events") or []:
            if not isinstance(event, dict) or event.get("stage") != "suggested_actions":
                continue
            for action in event.get("actions") or []:
                if isinstance(action, dict) and str(action.get("id") or "") == action_id:
                    return True
    return False
