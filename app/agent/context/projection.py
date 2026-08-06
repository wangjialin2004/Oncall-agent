"""Compact durable projection serializers (schema v2) and v1 compatibility.

Durable projection deliberately excludes:

- ``conversation.recent_turns`` text (canonical history is conversation_turns)
- duplicate identity owner/session (scope lives on envelope / DB primary key)
- ``patch_tail`` (runtime/debug only; process history lives in turn events)

Runtime still uses full :class:`AgentContextState`. This module only governs the
bytes written to ``conversations.context_state_json`` under the unified path.
"""

from __future__ import annotations

from typing import Any

from app.agent.context.persistence import (
    ContextSchemaError,
    state_from_dict,
    state_to_dict,
)
from app.agent.context.state import SCHEMA_VERSION, AgentContextState

PROJECTION_SCHEMA_V1 = 1
PROJECTION_SCHEMA_V2 = 2
SUPPORTED_PROJECTION_SCHEMAS = {PROJECTION_SCHEMA_V1, PROJECTION_SCHEMA_V2}


def projection_to_dict(
    state: AgentContextState, *, schema_version: int = PROJECTION_SCHEMA_V2
) -> dict[str, Any]:
    """Serialize a compact durable projection.

    Runtime fields that are reconstructible from turns stay out of durable
    storage. Callers that still need a full runtime dict should use
    :func:`state_to_dict` instead.
    """
    if schema_version not in SUPPORTED_PROJECTION_SCHEMAS:
        raise ContextSchemaError(f"unsupported projection schema_version={schema_version}")

    if schema_version == PROJECTION_SCHEMA_V1:
        # Legacy full snapshot path — kept for migration tooling only.
        return state_to_dict(state)

    payload = state_to_dict(state)
    payload["schema_version"] = PROJECTION_SCHEMA_V2
    # Scope authority is envelope/DB primary key, not duplicated JSON fields.
    payload.pop("owner_key", None)
    payload.pop("session_id", None)
    identity = dict(payload.get("identity") or {})
    identity.pop("owner_key", None)
    identity.pop("session_id", None)
    payload["identity"] = identity
    conversation = dict(payload.get("conversation") or {})
    conversation["recent_turns"] = []
    # last_turn_index is deprecated as a watermark; keep 0 in durable form.
    conversation["last_turn_index"] = 0
    # Keep only light attachment index; raw refs remain on turns.
    refs = conversation.get("active_attachment_refs") or []
    conversation["active_attachment_refs"] = [
        {
            key: value
            for key, value in item.items()
            if key in {"file_id", "file_name", "summary", "source_turn_id", "source_turn_index"}
        }
        if isinstance(item, dict)
        else item
        for item in refs
    ]
    payload["conversation"] = conversation
    payload["patch_tail"] = []
    return payload


def projection_from_dict(
    payload: dict[str, Any],
    *,
    owner_key: str = "",
    session_id: str = "",
) -> AgentContextState:
    """Load v1 or v2 projection into a runtime :class:`AgentContextState`.

    Load never writes back. v1 payloads remain readable for migration/compat.
    """
    if not isinstance(payload, dict):
        raise ContextSchemaError("projection payload is not a dict")
    schema_version = payload.get("schema_version", PROJECTION_SCHEMA_V1)
    try:
        schema_version_i = int(schema_version)
    except (TypeError, ValueError) as exc:
        raise ContextSchemaError(f"invalid projection schema_version={schema_version!r}") from exc
    if schema_version_i not in SUPPORTED_PROJECTION_SCHEMAS:
        raise ContextSchemaError(
            f"projection schema_version={schema_version_i} not supported "
            f"(supported={sorted(SUPPORTED_PROJECTION_SCHEMAS)})"
        )

    working = dict(payload)
    # state_from_dict currently accepts only SCHEMA_VERSION (runtime v1 shape).
    # Normalize projection payload into that runtime shape without mutating storage.
    working["schema_version"] = SCHEMA_VERSION
    if owner_key:
        working["owner_key"] = owner_key
    if session_id:
        working["session_id"] = session_id
    identity = dict(working.get("identity") or {})
    if owner_key and not identity.get("owner_key"):
        identity["owner_key"] = owner_key
    if session_id and not identity.get("session_id"):
        identity["session_id"] = session_id
    working["identity"] = identity
    conversation = dict(working.get("conversation") or {})
    conversation.setdefault("recent_turns", [])
    working["conversation"] = conversation
    working.setdefault("patch_tail", [])
    return state_from_dict(working)


def is_compact_projection(payload: dict[str, Any]) -> bool:
    """Heuristic used by audits/tests for durable v2 shape."""
    if int(payload.get("schema_version") or 0) != PROJECTION_SCHEMA_V2:
        return False
    conversation = payload.get("conversation") or {}
    recent = conversation.get("recent_turns") if isinstance(conversation, dict) else None
    if recent:
        return False
    if payload.get("patch_tail"):
        return False
    identity = payload.get("identity") or {}
    if isinstance(identity, dict) and (identity.get("owner_key") or identity.get("session_id")):
        return False
    if payload.get("owner_key") or payload.get("session_id"):
        return False
    return True


__all__ = [
    "PROJECTION_SCHEMA_V1",
    "PROJECTION_SCHEMA_V2",
    "SUPPORTED_PROJECTION_SCHEMAS",
    "is_compact_projection",
    "projection_from_dict",
    "projection_to_dict",
]
