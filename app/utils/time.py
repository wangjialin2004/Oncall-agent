"""Shared timestamp helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()
