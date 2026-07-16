"""Safety helpers for text that can reach the user-visible assistant answer."""

from __future__ import annotations

import json
import re

_PROTOCOL_MARKER_RE = re.compile(
    r"(?:^|\s)(?:analysis\s+)?to=(?:multi_tool_use\.parallel|functions\.[A-Za-z0-9_.-]+)\b",
    re.IGNORECASE,
)
_FINAL_MARKDOWN_BOUNDARY_RE = re.compile(
    r"#{1,6}\s+(?:现象|证据|判断|结论|建议|风险|下一步|最终结论)\b"
)


def contains_internal_tool_protocol(text: str) -> bool:
    """Return whether *text* contains an internal tool-routing marker.

    The marker is intentionally required. Business JSON may legitimately contain
    fields named ``recipient_name`` and must remain visible.
    """

    if not text:
        return False
    return _PROTOCOL_MARKER_RE.search(text) is not None


def sanitize_user_visible_answer(text: str) -> str:
    """Remove known internal tool-protocol fragments from an assistant answer.

    This is a narrow guard, not a general JSON filter. It only acts after a
    ``to=multi_tool_use.parallel`` or ``to=functions.<name>`` marker is present.
    """

    cleaned = str(text or "")
    while True:
        marker = _PROTOCOL_MARKER_RE.search(cleaned)
        if marker is None:
            return cleaned.strip()

        suffix = cleaned[marker.end() :]
        final_boundary = _FINAL_MARKDOWN_BOUNDARY_RE.search(suffix)
        if final_boundary is not None:
            boundary = marker.end() + final_boundary.start()
            cleaned = f"{cleaned[: marker.start()].rstrip()}\n\n{cleaned[boundary:]}"
            continue

        removal_end = _protocol_payload_end(cleaned, marker.end())
        cleaned = f"{cleaned[: marker.start()]}{cleaned[removal_end:]}"


def _protocol_payload_end(text: str, marker_end: int) -> int:
    """Return the end offset of one marker plus its optional JSON payload."""

    line_end = text.find("\n", marker_end)
    if line_end < 0:
        line_end = len(text)

    json_start = text.find("{", marker_end)
    if json_start < 0:
        return line_end

    try:
        _, consumed = json.JSONDecoder().raw_decode(text[json_start:])
    except json.JSONDecodeError:
        return line_end
    return json_start + consumed
