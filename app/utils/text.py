"""Shared text-normalization helpers used across services."""

from __future__ import annotations

from difflib import SequenceMatcher


def normalize_text(text: str) -> str:
    """Lowercase, strip, and collapse internal whitespace to single spaces."""
    return " ".join((text or "").strip().lower().split())


def text_similarity(left: str, right: str) -> float:
    """Similarity ratio in [0, 1] between two strings; 0 when either is empty."""
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()
