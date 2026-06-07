"""AIOps 工具证据摘要工具。"""

from __future__ import annotations

import json
from typing import Any


def build_tool_evidence(tool_messages: list[Any]) -> list[dict[str, Any]]:
    """从 LangChain ToolMessage 列表提取可审计证据摘要。"""

    evidence_items = []
    for message in tool_messages:
        content = getattr(message, "content", "")
        payload = _parse_payload(content)
        tool_name = getattr(message, "name", None) or payload.get("tool_name") or "unknown_tool"
        success = _is_success(payload)
        evidence_items.append(
            {
                "tool_name": tool_name,
                "tool_call_id": getattr(message, "tool_call_id", None),
                "evidence_id": payload.get("evidence_id") or "",
                "source": payload.get("source") or "",
                "success": success,
                "duration_ms": payload.get("duration_ms"),
                "summary": _summarize_payload(payload, fallback=str(content)),
            }
        )
    return evidence_items


def build_persistent_tool_evidence(
    tool_messages: list[Any],
    tool_calls: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Build evidence records suitable for durable storage."""

    arguments_by_call_id = _tool_call_arguments_by_id(tool_calls or [])
    records = []
    for message, item in zip(tool_messages, build_tool_evidence(tool_messages), strict=False):
        tool_call_id = item.get("tool_call_id")
        records.append(
            {
                **item,
                "arguments": arguments_by_call_id.get(tool_call_id, {}),
                "raw_result": _serialize_content(getattr(message, "content", "")),
            }
        )
    return records


def append_evidence_summary(result: str, evidence_items: list[dict[str, Any]]) -> str:
    """把证据摘要追加到步骤执行结果，供最终报告引用。"""

    if not evidence_items:
        return result

    lines = ["", "## 工具证据摘要"]
    for index, item in enumerate(evidence_items, 1):
        lines.append(

                f"{index}. 证据ID: {item.get('evidence_id') or '无'} | "
                f"工具: {item.get('tool_name') or 'unknown_tool'} | "
                f"来源: {item.get('source') or '未知'} | "
                f"耗时: {item.get('duration_ms') if item.get('duration_ms') is not None else '未知'}ms"

        )
        lines.append(f"   摘要: {item.get('summary') or '无摘要'}")

    return result.rstrip() + "\n" + "\n".join(lines)


def _parse_payload(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        return {"items": content}
    if not isinstance(content, str):
        return {"raw": str(content)}

    text = content.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    if isinstance(parsed, dict):
        return parsed
    return {"items": parsed}


def _serialize_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except TypeError:
        return str(content)


def _tool_call_arguments_by_id(tool_calls: list[Any]) -> dict[str, Any]:
    arguments_by_id = {}
    for call in tool_calls:
        call_id = _read_mapping_or_attr(call, "id")
        if not call_id:
            continue
        arguments = _read_mapping_or_attr(call, "args")
        arguments_by_id[call_id] = arguments if isinstance(arguments, dict) else {}
    return arguments_by_id


def _read_mapping_or_attr(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _is_success(payload: dict[str, Any]) -> bool:
    if "success" in payload:
        return bool(payload["success"])
    status = str(payload.get("status") or "").lower()
    if status:
        return status not in {"error", "failed", "failure"}
    return "error" not in payload


def _summarize_payload(payload: dict[str, Any], fallback: str) -> str:
    if not payload:
        return fallback[:300]

    parts = []
    status = payload.get("status")
    if status:
        parts.append(f"status={status}")

    for key in ["total", "message", "error"]:
        if key in payload and payload[key] not in (None, ""):
            parts.append(f"{key}={payload[key]}")

    for list_key in ["logs", "ports", "services", "processes", "data_points"]:
        values = payload.get(list_key)
        if isinstance(values, list):
            parts.append(_summarize_list(list_key, values))
            break

    if parts:
        return "; ".join(part for part in parts if part)
    return fallback[:300]


def _summarize_list(name: str, values: list[Any]) -> str:
    if not values:
        return f"{name}[0]"

    first = values[0]
    if isinstance(first, dict):
        if name == "logs":
            level = first.get("level") or "UNKNOWN"
            message = first.get("message") or first.get("raw") or ""
            return f"{name}[{len(values)}]: {level} {message}".strip()
        if name == "ports":
            service = first.get("service_name") or first.get("port")
            status = first.get("status") or first.get("reachable")
            return f"{name}[{len(values)}]: {service} {status}".strip()
        if name == "data_points":
            return f"{name}[{len(values)}]: latest={first.get('value')}"
        return f"{name}[{len(values)}]: {first}"

    return f"{name}[{len(values)}]: {first}"
