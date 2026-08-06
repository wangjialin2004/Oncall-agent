"""Immutable request identity shared by API, harness and data-plane code."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.services.session_scope_service import AuthenticatedPrincipal

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CURRENT_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "current_request_context", default=None
)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Validated caller and request scope.

    ``owner_key`` is the new stable identifier. ``storage_owner_key`` keeps
    legacy SQLite/checkpoint rows addressable until the explicit data migration
    switches those stores to the stable identifier.
    """

    owner_key: str
    storage_owner_key: str
    project_id: str
    role: str
    session_id: str
    trace_id: str


def build_request_context(
    principal: AuthenticatedPrincipal,
    session_id: str,
    *,
    trace_id: str | None = None,
) -> RequestContext:
    value = str(session_id or "").strip()
    if not _SESSION_ID.fullmatch(value):
        raise ValueError("invalid session_id")
    return RequestContext(
        owner_key=principal.owner_key,
        storage_owner_key=principal.storage_owner_key,
        project_id=principal.project_id,
        role=principal.role,
        session_id=value,
        trace_id=(trace_id or uuid.uuid4().hex),
    )


def is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID.fullmatch(str(session_id or "").strip()))


def get_request_context() -> RequestContext | None:
    """Return the request scope bound to the current async task, if any."""

    return _CURRENT_REQUEST_CONTEXT.get()


@contextmanager
def bind_request_context(context: RequestContext) -> Iterator[None]:
    """Bind a validated context for tools invoked during one request."""

    token = _CURRENT_REQUEST_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_REQUEST_CONTEXT.reset(token)
