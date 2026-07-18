"""Tenant scope primitives for Milvus knowledge retrieval."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.config import config
from app.core.request_context import RequestContext


class RagScopeError(ValueError):
    """Raised when a scoped RAG operation cannot be made safely."""


@dataclass(frozen=True, slots=True)
class RagScope:
    """The three visibility scopes allowed by the RAG data plane."""

    owner_key: str
    storage_owner_key: str
    project_id: str

    def __post_init__(self) -> None:
        if not self.owner_key or not self.storage_owner_key or not self.project_id:
            raise RagScopeError("owner and project scope are required")

    @classmethod
    def from_context(cls, context: RequestContext | None) -> RagScope:
        if context is None:
            raise RagScopeError("request context is required for tenant-scoped RAG")
        return cls(
            owner_key=context.owner_key,
            storage_owner_key=context.storage_owner_key,
            project_id=context.project_id,
        )


def _literal(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256:
        raise RagScopeError("invalid scope value")
    # Milvus expressions use JSON-compatible quoted string literals. This
    # avoids interpolating caller-controlled quotes or backslashes.
    return json.dumps(normalized, ensure_ascii=True)


def build_scope_filter(
    context: RequestContext | None,
    *,
    tenant_scope_enabled: bool | None = None,
    allow_legacy_unscoped: bool | None = None,
) -> str | None:
    """Return a Milvus scalar filter for system/project/current-user rows.

    ``None`` is returned only when scope enforcement is explicitly disabled.
    With enforcement enabled, absent context raises instead of permitting a
    global query.
    """

    enabled = (
        bool(getattr(config, "rag_tenant_scope_enabled", True))
        if tenant_scope_enabled is None
        else bool(tenant_scope_enabled)
    )
    if not enabled:
        return None
    scope = RagScope.from_context(context)
    system = '(scope_type == "system" && scope_id == "system")'
    project = (
        f'(scope_type == "project" && scope_id == {_literal(scope.project_id)})'
    )
    user_ids = [scope.owner_key]
    if scope.storage_owner_key != scope.owner_key:
        user_ids.append(scope.storage_owner_key)
    user_values = ", ".join(_literal(value) for value in user_ids)
    user = f'(scope_type == "user" && scope_id in [{user_values}])'
    expression = f"{system} || {project} || {user}"
    allow_legacy = (
        bool(getattr(config, "rag_allow_legacy_unscoped", False))
        if allow_legacy_unscoped is None
        else bool(allow_legacy_unscoped)
    )
    if allow_legacy:
        expression = f"({expression}) || (scope_type == \"legacy\")"
    return expression


def scope_fields(*, scope_type: str = "system", scope_id: str = "system") -> dict[str, str]:
    """Validate and return scalar fields for a vector insert."""

    normalized_type = str(scope_type or "").strip().lower()
    if normalized_type not in {"system", "project", "user", "legacy"}:
        raise RagScopeError("unsupported scope_type")
    normalized_id = str(scope_id or "").strip()
    if normalized_type == "system" and normalized_id != "system":
        raise RagScopeError("system scope_id must be system")
    if not normalized_id or len(normalized_id) > 256:
        raise RagScopeError("invalid scope_id")
    return {"scope_type": normalized_type, "scope_id": normalized_id}
