"""Project internal harness events into a minimal browser-visible contract."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.config import config

TIMELINE_EVENT_TYPES = frozenset(
    {"route_event", "agent_event", "tool_event", "decision_event"}
)

_MAX_DETAIL_ITEMS = 8
_MAX_DETAIL_TEXT = 640
_MAX_RESULT_ITEMS = 6
_MAX_RESULT_FIELDS = 12
_MAX_RESULT_ITEM_TEXT = 280
_SECRET_PATTERN = re.compile(
    r"(?i)\b(token|api[_-]?key|secret|password|authorization|cookie|access[_-]?key)\b"
    r"\s*[:=]\s*([^\s,;]+)"
)
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(https?://)([^\s/@:]+):([^\s/@]+)@")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")

_RESULT_FIELD_KEYS = (
    "status",
    "success",
    "source",
    "metric_name",
    "retrieval_type",
    "interval",
    "count",
    "total",
    "series_count",
    "duration_ms",
    "service",
    "service_name",
    "message",
    "summary",
    "answer",
    "error",
    "error_code",
    "source_available",
    "capability_available",
    "gap",
    "warning",
    "note",
)
_RESULT_ITEM_KEYS = (
    "data_points",
    "series",
    "logs",
    "alerts",
    "changes",
    "items",
    "results",
    "experiences",
    "observed_facts",
    "services",
    "ports",
)
_RESULT_OBJECT_FIELD_KEYS = {
    "statistics": ("avg", "max", "min", "p95", "threshold_exceeded", "memory_pressure"),
    "alert_info": ("triggered", "threshold", "message"),
    "cpu": ("usage_percent", "count"),
    "memory": ("usage_percent", "total_bytes", "used_bytes", "available_bytes"),
    "disk": ("usage_percent", "total_bytes", "used_bytes", "free_bytes"),
}

_PUBLIC_TIMELINE_KEYS = (
    "type",
    "agent",
    "stage",
    "status",
    "tool",
    "route",
    "duration_ms",
    "started_at",
)


def _text(value: Any, *, limit: int = 160) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = str(value).strip()
    rendered = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", rendered)
    rendered = _URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]@", rendered)
    rendered = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", rendered)
    rendered = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", rendered)
    rendered = _PHONE_PATTERN.sub("[REDACTED_PHONE]", rendered)
    return rendered[:limit]


def _safe_scalar_text(value: Any, *, limit: int = 160) -> str:
    """Keep typed metric scalars intact while redacting untrusted strings."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)[:limit]
    return _text(value, limit=limit)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _string_list(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [text for item in value[:limit] if (text := _text(item))]


def _public_progress_details_enabled() -> bool:
    return bool(getattr(config, "harness_public_progress_details_enabled", True))


def _decode_result(value: Any) -> Any:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate and candidate[0] in "[{":
            try:
                return json.loads(candidate)
            except (TypeError, ValueError):
                return value
    return value


def _result_item_text(value: Any) -> str:
    if isinstance(value, Mapping):
        if value.get("timestamp") is not None and value.get("value") is not None:
            timestamp = _safe_scalar_text(value.get("timestamp"), limit=80)
            point_value = _safe_scalar_text(value.get("value"), limit=80)
            if timestamp and point_value:
                return f"{timestamp}={point_value}"
        if isinstance(value.get("metric"), Mapping):
            labels = _safe_mapping_pairs(value["metric"], limit=3)
            points = value.get("data_points")
            point_count = (
                len(points)
                if isinstance(points, Sequence) and not isinstance(points, (str, bytes))
                else 0
            )
            if labels:
                suffix = f"；{point_count} 个点" if point_count else ""
                return f"指标标签：{labels}{suffix}"
        for key in ("summary", "message", "alertname", "content", "symptom", "answer", "value"):
            if value.get(key) is not None:
                return _safe_scalar_text(value.get(key), limit=_MAX_RESULT_ITEM_TEXT)
        pairs = _safe_mapping_pairs(
            value,
            keys=("service_name", "name", "status", "reachable", "usage_percent", "count", "total"),
            limit=3,
        )
        if pairs:
            return pairs
        return ""
    return _text(value, limit=_MAX_RESULT_ITEM_TEXT)


def _safe_mapping_pairs(
    value: Mapping[Any, Any],
    *,
    keys: Sequence[str] | None = None,
    limit: int = 4,
) -> str:
    """Render a small allowlisted scalar mapping without publishing raw objects."""

    allowed = tuple(keys) if keys is not None else tuple(str(key) for key in value.keys())
    pairs: list[str] = []
    for key in allowed:
        raw_value = value.get(key)
        if key not in value or (
            isinstance(raw_value, (Mapping, Sequence))
            and not isinstance(raw_value, (str, bytes))
        ):
            continue
        rendered = _safe_scalar_text(raw_value, limit=120)
        if rendered:
            pairs.append(f"{key}={rendered}")
        if len(pairs) >= limit:
            break
    return "；".join(pairs)


def _safe_nested_result_fields(value: Mapping[Any, Any]) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    for object_key, field_keys in _RESULT_OBJECT_FIELD_KEYS.items():
        nested = value.get(object_key)
        if not isinstance(nested, Mapping):
            continue
        for field_key in field_keys:
            if field_key not in nested:
                continue
            rendered = _safe_scalar_text(nested.get(field_key), limit=260)
            if rendered:
                fields.append({"label": f"{object_key}.{field_key}", "value": rendered})
            if len(fields) >= _MAX_RESULT_FIELDS:
                return fields
    return fields


def _safe_result_details(value: Any) -> tuple[str, list[dict[str, str]], list[str]]:
    """Turn a tool result into bounded display data without exposing raw objects."""
    decoded = _decode_result(value)
    if isinstance(decoded, Mapping):
        fields: list[dict[str, str]] = []
        for key in _RESULT_FIELD_KEYS:
            if key not in decoded:
                continue
            rendered = _safe_scalar_text(decoded.get(key), limit=260)
            if rendered:
                fields.append({"label": key, "value": rendered})
        fields.extend(_safe_nested_result_fields(decoded))
        if isinstance(decoded.get("data_points"), Sequence) and not isinstance(decoded.get("data_points"), (str, bytes)):
            fields.append({"label": "data_points_count", "value": str(len(decoded["data_points"]))})
        fields = fields[:_MAX_RESULT_FIELDS]
        items: list[str] = []
        for key in _RESULT_ITEM_KEYS:
            raw_items = decoded.get(key)
            if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
                continue
            items.extend(
                rendered
                for item in raw_items[:_MAX_RESULT_ITEMS]
                if (rendered := _result_item_text(item))
            )
            if items:
                break
        if fields:
            preview = "；".join(f"{item['label']}={item['value']}" for item in fields)
        elif items:
            preview = f"返回 {len(items)} 条结果"
        else:
            preview = "结构化结果已返回（未展开字段）"
        return _text(preview, limit=_MAX_DETAIL_TEXT), fields, items[:_MAX_RESULT_ITEMS]
    if isinstance(decoded, Sequence) and not isinstance(decoded, (str, bytes)):
        items = [
            rendered
            for item in decoded[:_MAX_RESULT_ITEMS]
            if (rendered := _result_item_text(item))
        ]
        preview = f"返回 {len(decoded)} 条结果"
        return _text(preview, limit=_MAX_DETAIL_TEXT), [], items
    if isinstance(decoded, str):
        # Plain tool text can be a raw log, retrieved document, or upstream
        # error body. Keep the public contract observable without publishing
        # arbitrary source content to the browser.
        return f"文本结果已返回（{len(decoded)} 字符，原文未公开）", [], []
    preview = _text(decoded, limit=_MAX_DETAIL_TEXT)
    return preview, [], []


def _suggested_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    actions: list[dict[str, Any]] = []
    for item in value[:8]:
        if not isinstance(item, Mapping):
            continue
        action_id = _text(item.get("id"))
        if not action_id:
            continue
        actions.append(
            {
                "id": action_id,
                "title": _text(item.get("title"), limit=200),
                "risk": _text(item.get("risk") or "low"),
                "requires_confirm": item.get("requires_confirm") is not False,
            }
        )
    return actions


class PublicEventProjector:
    """Keep only display-safe fields and replace internal call IDs per turn."""

    def __init__(self) -> None:
        self._activity_ids: dict[str, str] = {}

    def _activity_id(self, raw_id: Any) -> str:
        normalized = _text(raw_id, limit=500)
        if not normalized:
            return ""
        if normalized not in self._activity_ids:
            self._activity_ids[normalized] = f"activity-{len(self._activity_ids) + 1}"
        return self._activity_ids[normalized]

    def project_timeline(self, event: Mapping[str, Any]) -> dict[str, Any]:
        event_type = _text(event.get("type"))
        if event_type not in TIMELINE_EVENT_TYPES:
            return {}

        public: dict[str, Any] = {"type": event_type}
        for key in _PUBLIC_TIMELINE_KEYS[1:]:
            value = event.get(key)
            if key in {"duration_ms", "started_at"}:
                if (number := _number(value)) is not None:
                    public[key] = number
            elif value is not None and (safe_value := _text(value)):
                public[key] = safe_value

        payload = event.get("payload")
        source = payload if isinstance(payload, Mapping) else {}
        public_payload: dict[str, Any] = {}

        if experts := _string_list(source.get("experts")):
            public_payload["experts"] = experts
        for key in ("delegated_expert", "tool"):
            if value := _text(source.get(key)):
                public_payload[key] = value
        if isinstance(source.get("parallel"), bool):
            public_payload["parallel"] = source["parallel"]
        for key in ("wall_ms", "step", "resumed_from_step", "replayed_steps"):
            if (number := _number(source.get(key))) is not None:
                public_payload[key] = number
        for key in ("conservative", "replay_override"):
            if isinstance(source.get(key), bool):
                public_payload[key] = source[key]
        if value := _text(source.get("started_at"), limit=80):
            public_payload["started_at"] = value

        if _public_progress_details_enabled():
            if event_type == "agent_event" and event.get("stage") in {
                "plan",
                "planning",
                "replan",
            }:
                for key in ("todos", "required_evidence", "gaps", "failed_tools"):
                    values = _string_list(source.get(key), limit=_MAX_DETAIL_ITEMS)
                    if values:
                        public_payload[key] = values
                for key in ("trigger", "focus_route"):
                    if value := _text(source.get(key), limit=160):
                        public_payload[key] = value

            if event_type == "agent_event" and event.get("stage") in {
                "verify",
                "re_evidence",
            }:
                for key in ("evidence_count", "failed_evidence_count"):
                    if (number := _number(source.get(key))) is not None:
                        public_payload[key] = number
                if value := _text(source.get("confidence"), limit=40):
                    public_payload["confidence"] = value
                gaps = _string_list(source.get("gaps"), limit=_MAX_DETAIL_ITEMS)
                if gaps:
                    public_payload["gaps"] = gaps

            if event_type == "tool_event" and "result" in source:
                preview, fields, items = _safe_result_details(source.get("result"))
                if preview:
                    public_payload["result_preview"] = preview
                if fields:
                    public_payload["result_fields"] = fields
                if items:
                    public_payload["result_items"] = items

        results = source.get("results")
        if isinstance(results, Sequence) and not isinstance(results, (str, bytes)):
            public_results: list[dict[str, str]] = []
            for item in results[:8]:
                if not isinstance(item, Mapping):
                    continue
                result = {
                    key: value
                    for key in ("expert", "status")
                    if (value := _text(item.get(key)))
                }
                if result:
                    public_results.append(result)
            if public_results:
                public_payload["results"] = public_results

        raw_call_id = source.get("tool_call_id") or event.get("evidence_id")
        if activity_id := self._activity_id(raw_call_id):
            public_payload["tool_call_id"] = activity_id
        if parent_id := self._activity_id(source.get("parent_tool_call_id")):
            public_payload["parent_tool_call_id"] = parent_id

        if "duration_ms" not in public:
            duration = _number(source.get("tool_latency_ms"))
            if duration is None:
                duration = _number(source.get("wall_ms"))
            if duration is not None:
                public["duration_ms"] = duration

        if public_payload:
            public["payload"] = public_payload
        elif event_type in {"agent_event", "tool_event"}:
            public["payload"] = {}

        actions = _suggested_actions(event.get("actions"))
        if actions:
            public["actions"] = actions
        return public

    def project_stream(self, event: Mapping[str, Any]) -> dict[str, Any]:
        event_type = _text(event.get("type"))
        if event_type in TIMELINE_EVENT_TYPES:
            return self.project_timeline(event)
        if event_type == "content":
            return {"type": "content", "data": str(event.get("data") or "")}
        if event_type == "report":
            return {
                "type": "report",
                "route": _text(event.get("route")),
                "case_id": _text(event.get("case_id")),
                "report": str(event.get("report") or ""),
            }
        if event_type == "error":
            public_error: dict[str, Any] = {
                "type": "error",
                "route": _text(event.get("route") or "error"),
                "message": "internal_error",
            }
            if case_id := _text(event.get("case_id")):
                public_error["case_id"] = case_id
            return public_error
        if event_type != "complete":
            return {"type": event_type} if event_type else {}

        public: dict[str, Any] = {
            "type": "complete",
            "route": _text(event.get("route")),
            "answer": str(event.get("answer") or ""),
            "case_id": _text(event.get("case_id")),
            "events": [
                projected
                for item in (event.get("events") or [])
                if isinstance(item, Mapping)
                and (projected := self.project_timeline(item))
            ],
        }
        if event.get("replace_streamed_answer") is True:
            public["replace_streamed_answer"] = True
        if missing := _string_list(event.get("missing_params"), limit=20):
            public["missing_params"] = missing

        distill = event.get("distill_draft")
        if isinstance(distill, Mapping):
            public["distill_draft"] = {
                "experience_id": _text(distill.get("experience_id")),
                "status": _text(distill.get("status") or "pending"),
                "enabled": bool(distill.get("enabled")),
                "requires_confirm": distill.get("requires_confirm") is not False,
            }
        elif distill is None:
            public["distill_draft"] = None

        clarification = event.get("clarification")
        if isinstance(clarification, Mapping):
            defaults = clarification.get("defaults")
            public["clarification"] = {
                "missing_params": _string_list(
                    clarification.get("missing_params"), limit=20
                ),
                "defaults": {
                    _text(key): _text(value, limit=300)
                    for key, value in (
                        defaults.items() if isinstance(defaults, Mapping) else []
                    )
                    if _text(key)
                },
                "question": _text(clarification.get("question"), limit=500),
            }
        elif clarification is None:
            public["clarification"] = None

        actions = _suggested_actions(event.get("suggested_actions"))
        if actions:
            public["suggested_actions"] = actions
        return public

    def project_history_events(self, events: Any) -> list[dict[str, Any]]:
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
            return []
        return [
            projected
            for item in events
            if isinstance(item, Mapping)
            and (projected := self.project_timeline(item))
        ]


def to_public_timeline_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Project one standalone event with a fresh activity-ID namespace."""

    return PublicEventProjector().project_timeline(event)
