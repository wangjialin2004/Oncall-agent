"""Shared JSON (de)serialization helpers with safe defaults."""

from __future__ import annotations

import json
from typing import Any


def json_loads(value: str, default: Any) -> Any:
    """Parse JSON, returning ``default`` on missing or invalid input."""
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def json_dumps(value: Any) -> str:
    """Serialize to compact UTF-8 JSON (non-ASCII preserved)."""
    return json.dumps(value, ensure_ascii=False, default=str)
