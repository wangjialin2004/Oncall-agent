"""Helpers for isolating user-visible session IDs by caller ownership."""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import Header, HTTPException

SESSION_OWNER_HEADER = "X-Session-Owner"


def require_session_owner(
    x_session_owner: Annotated[str | None, Header(alias=SESSION_OWNER_HEADER)] = None,
) -> str:
    """Return a stable owner key or reject unauthenticated session access."""

    owner = (x_session_owner or "").strip()
    if not owner:
        raise HTTPException(
            status_code=401,
            detail=f"{SESSION_OWNER_HEADER} header is required",
        )
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:8]


def scope_session_id(session_id: str, owner_key: str) -> str:
    """Namespace a user-visible session ID without storing the raw owner token."""

    return f"owner:{owner_key}:{session_id}"
