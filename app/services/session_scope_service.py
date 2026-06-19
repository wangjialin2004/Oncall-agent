"""Helpers for isolating user-visible session IDs by caller ownership."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException

from app.services.auth_service import auth_service

AUTHORIZATION_HEADER = "Authorization"


def require_session_owner(
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> str:
    """Return a stable owner key derived from the authenticated backend user."""

    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authorization Bearer token is required",
        )
    try:
        username = auth_service.verify_access_token(token)
        return auth_service.owner_key_for_user(username)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc


def scope_session_id(session_id: str, owner_key: str) -> str:
    """Namespace a user-visible session ID without storing the raw owner token."""

    return f"owner:{owner_key}:{session_id}"


def _bearer_token(authorization: str | None) -> str:
    value = (authorization or "").strip()
    if not value:
        return ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()
