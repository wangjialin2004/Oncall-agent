"""HITL suggested-action confirm — audit only, never executes remediation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()

# In-process audit log for pilot (not a durable store).
_AUDIT: list[dict[str, Any]] = []


class ConfirmSuggestionRequest(BaseModel):
    session_id: str = Field(..., alias="SessionId")
    action_id: str = Field(..., alias="ActionId")
    note: str = Field(default="", alias="Note")

    model_config = {"populate_by_name": True}


@router.post("/hitl/confirm-suggestion")
async def confirm_suggestion(body: ConfirmSuggestionRequest) -> dict[str, Any]:
    """Record operator acknowledgement of a *suggested* action.

    Does **not** invoke restart/rollback/scale executors. Read-only product red line.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": body.session_id,
        "action_id": body.action_id,
        "note": (body.note or "")[:500],
        "executed": False,
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
