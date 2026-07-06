"""Harness loop checkpoint introspection endpoints.

These endpoints let the frontend ask "is there a resumable run for this
session?" and "drop the resumable run, I do not want to continue". The actual
resume path lives on ``POST /api/assistant`` — the same session_id picks up
the partial state on the next request — to avoid a second entry point.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger

from app.config import config
from app.services.harness_checkpoint import get_default_checkpoint_store
from app.services.session_scope_service import require_session_owner

router = APIRouter()


def _store_or_none():
    """Return the active checkpoint store, or ``None`` when the feature is off."""
    if not (
        bool(getattr(config, "redis_enabled", False))
        and bool(getattr(config, "harness_checkpoint_enabled", False))
    ):
        return None
    return get_default_checkpoint_store()


@router.get("/checkpoint/{session_id}")
async def get_checkpoint(
    session_id: str,
    owner_key: str = Depends(require_session_owner),
):
    """Return the checkpoint meta for one session (no messages / no tool results)."""
    store = _store_or_none()
    if store is None:
        return {
            "code": 200,
            "message": "success",
            "data": {"session_id": session_id, "enabled": False, "resumable": False},
        }
    meta = await store.get_meta(owner_key, session_id)
    if meta is None:
        return {
            "code": 200,
            "message": "success",
            "data": {"session_id": session_id, "enabled": True, "resumable": False},
        }
    completed = bool(meta.get("completed"))
    return {
        "code": 200,
        "message": "success",
        "data": {
            "session_id": session_id,
            "enabled": True,
            "resumable": not completed,
            "step": int(meta.get("step") or 0),
            "route": str(meta.get("route") or ""),
            "started_at": str(meta.get("started_at") or ""),
            "last_step_at": str(meta.get("last_step_at") or ""),
            "completed": completed,
        },
    }


@router.delete("/checkpoint/{session_id}")
async def delete_checkpoint(
    session_id: str,
    owner_key: str = Depends(require_session_owner),
):
    """Drop the resumable run for this session. No-op when no checkpoint exists."""
    store = _store_or_none()
    if store is None:
        return {
            "code": 200,
            "message": "success",
            "data": {"session_id": session_id, "deleted": 0},
        }
    removed = await store.delete(owner_key, session_id)
    logger.info(f"删除 checkpoint {session_id}: 清理 {removed} 个 key")
    return {
        "code": 200,
        "message": "success",
        "data": {"session_id": session_id, "deleted": int(removed)},
    }
