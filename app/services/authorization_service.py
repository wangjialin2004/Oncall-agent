"""Central role and scope authorization policies for API handlers."""

from __future__ import annotations

from fastapi import HTTPException

from app.config import config
from app.services.session_scope_service import AuthenticatedPrincipal

ROLE_ORDER = {"operator": 10, "curator": 20, "admin": 30}


def forbidden(reason: str, *, trace_id: str = "") -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "code": "forbidden",
            "message": "forbidden",
            "detail": reason,
            **({"trace_id": trace_id} if trace_id else {}),
        },
    )


def require_role(
    principal: AuthenticatedPrincipal,
    minimum: str,
) -> AuthenticatedPrincipal:
    if not bool(getattr(config, "auth_role_enforcement_enabled", False)):
        return principal
    if ROLE_ORDER.get(principal.role, 0) < ROLE_ORDER.get(minimum, 10):
        raise forbidden("insufficient_role")
    return principal


def is_project_member(principal: AuthenticatedPrincipal, project_id: str) -> bool:
    return bool(project_id) and principal.project_id == project_id


authorization_service = type(
    "AuthorizationService",
    (),
    {
        "require_role": staticmethod(require_role),
        "is_project_member": staticmethod(is_project_member),
    },
)()
