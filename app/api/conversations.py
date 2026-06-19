"""Conversation history API (multi-turn session list / restore / delete)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger

from app.services.conversation_service import conversation_service
from app.services.session_scope_service import require_session_owner

router = APIRouter()


@router.get("/conversations")
async def list_conversations(owner_key: str = Depends(require_session_owner)):
    """List the caller's conversations, most-recently-updated first."""
    sessions = conversation_service.list_sessions(owner_key)
    return {"code": 200, "message": "success", "data": sessions}


@router.get("/conversations/{session_id}")
async def get_conversation(
    session_id: str,
    owner_key: str = Depends(require_session_owner),
):
    """Return all turns of one conversation so the frontend can restore it."""
    turns = conversation_service.get_turns(owner_key, session_id)
    return {
        "code": 200,
        "message": "success",
        "data": {"session_id": session_id, "turns": turns},
    }


@router.delete("/conversations/{session_id}")
async def delete_conversation(
    session_id: str,
    owner_key: str = Depends(require_session_owner),
):
    """Delete a conversation and all of its turns."""
    deleted = conversation_service.delete_session(owner_key, session_id)
    logger.info(f"删除会话 {session_id}: {'成功' if deleted else '不存在'}")
    return {
        "code": 200,
        "message": "success",
        "data": {"session_id": session_id, "deleted": deleted},
    }
