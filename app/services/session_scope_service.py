"""Helpers for isolating user-visible session IDs by caller ownership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException

from app.config import config
from app.services.auth_service import auth_service

AUTHORIZATION_HEADER = "Authorization"


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Authenticated caller identity used by protected routes."""

    username: str
    owner_key: str
    storage_owner_key: str
    project_id: str
    role: str


def unauthorized(reason: str) -> HTTPException:
    """Build the canonical 401 body used by the frontend session lifecycle."""

    return HTTPException(
        status_code=401,
        detail={
            "code": 401,
            "message": "unauthorized",
            "detail": reason,
        },
    )


async def require_authenticated_principal(
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> AuthenticatedPrincipal:
    """Validate the Bearer token and return username + stable owner key."""

    token = _bearer_token(authorization)
    if not token:
        raise unauthorized("token_missing")
    try:
        username = auth_service.verify_access_token(token)
    except ValueError as exc:
        message = str(exc).lower()
        reason = "token_expired" if "expired" in message else "token_invalid"
        raise unauthorized(reason) from exc
    return AuthenticatedPrincipal(
        username=username,
        owner_key=auth_service.stable_owner_key_for_user(username),
        storage_owner_key=auth_service.owner_key_for_user(username),
        project_id=config.project_for_user(username),
        role=config.role_for_user(username),
    )


async def require_session_owner(
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> str:
    """Return a stable owner key derived from the authenticated backend user."""

    return (await require_authenticated_principal(authorization)).storage_owner_key


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
