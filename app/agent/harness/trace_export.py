"""Best-effort Harness run trace export (M2 W6 / M3 W11).

Disabled by default. Never writes secrets; failures must not break SSE complete.
W11: sample rate + distill/anti/usage summary fields.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import config


def _sample_allows_export() -> bool:
    """Return True when export is allowed under sample rate.

    - enabled=False → never
    - sample_rate <= 0 → never (even if enabled)
    - sample_rate >= 1 → always (when enabled)
    - else random() < rate
    """
    if not bool(getattr(config, "harness_trace_export_enabled", False)):
        return False
    try:
        rate = float(getattr(config, "harness_trace_sample_rate", 0.0) or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    return random.random() < rate


def _compact_usage(usage: Any) -> dict[str, Any]:
    if not isinstance(usage, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            out[key] = int(value)
    return out


def _compact_optional_dict(value: Any, *, keys: tuple[str, ...], limit: int = 8) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in keys:
        if key not in value:
            continue
        item = value.get(key)
        if isinstance(item, str):
            out[key] = item[:200]
        elif isinstance(item, (int, float, bool)) or item is None:
            out[key] = item
        elif isinstance(item, list):
            out[key] = [str(x)[:80] for x in item[:limit]]
        else:
            out[key] = str(item)[:120]
    return out


def maybe_export_harness_trace(
    *,
    session_id: str,
    state: Any,
    plan: Any | None = None,
    verification: Any | None = None,
    re_evidence_rounds: int = 0,
    replan_times: int = 0,
    parallel_stats: dict[str, Any] | None = None,
    distill_draft: dict[str, Any] | None = None,
    anti_pattern: dict[str, Any] | None = None,
) -> str | None:
    """Write a compact JSON trace when enabled + sampled. Returns path or None."""
    if not _sample_allows_export():
        return None
    try:
        out_dir = Path(
            str(getattr(config, "harness_trace_export_dir", "volumes/traces") or "volumes/traces")
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_session = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(session_id or "session")
        )[:120]
        path = out_dir / f"{safe_session}.json"

        timeline = list(getattr(state, "timeline_events", None) or [])
        # Cap timeline size to keep traces small and avoid accidental dumps.
        if len(timeline) > 200:
            timeline = timeline[-200:]

        plan_payload: dict[str, Any] = {}
        if plan is not None:
            plan_payload = {
                "focus_route": getattr(plan, "focus_route", None),
                "todos": list(getattr(plan, "todos", None) or [])[:20],
                "required_evidence": list(getattr(plan, "required_evidence", None) or [])[:20],
            }

        verify_payload: dict[str, Any] = {}
        if verification is not None:
            verify_payload = {
                "status": getattr(verification, "status", None),
                "confidence": getattr(verification, "confidence", None),
                "gaps": list(getattr(verification, "gaps", None) or [])[:20],
            }

        payload = {
            "session_id": session_id,
            "trace_id": getattr(state, "trace_id", None),
            "route": getattr(state, "route", None),
            "steps": getattr(state, "step", None),
            "token_estimate": getattr(state, "token_estimate", None),
            "re_evidence_rounds": re_evidence_rounds,
            "replan_times": replan_times,
            "plan": plan_payload,
            "verify": verify_payload,
            "parallel_stats": parallel_stats or {},
            "usage": _compact_usage(getattr(state, "usage_total", None) or {}),
            "distill_draft": _compact_optional_dict(
                distill_draft,
                keys=(
                    "id",
                    "status",
                    "requires_confirm",
                    "confidence",
                    "source_type",
                    "title",
                ),
            ),
            "anti_pattern": _compact_optional_dict(
                anti_pattern,
                keys=("id", "status", "source_type", "title", "tool", "reason"),
            ),
            "timeline_events": timeline,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return str(path)
    except Exception as exc:  # pragma: no cover - never break complete
        logger.warning("Harness trace export failed: {}", exc)
        return None
