"""Bridge harness tool execution into ContextState evidence writes.

Per plan ``plan/2026-07-08-stateful-agent-context.md`` §8.2 tool results
**never** enter ContextState as raw payloads. Instead the harness frames the
result into structured ``EvidenceItem`` / ``ToolSummary`` rows referencing the
timeline event id (or any other ``raw_ref`` the harness emits), and only those
rows get appended.

This module is intentionally small — its job is to be the single chokepoint
through which tool outcomes become whiteboard evidence, so it's easy to
audit that no large content slips through.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.agent.context.operations import (
    append_evidence_gap,
    append_observed_fact,
    append_tool_summary,
    framework_patch,
)
from app.agent.context.state import (
    AgentContextState,
    EvidenceItem,
    SECTION_TOOL,
    ToolSummary,
)


#: Maximum length (chars) for the human-readable note / facts we keep. Anything
#: longer is truncated with an ellipsis so we never store large content blobs.
MAX_NOTE_CHARS = 240


def _truncate(text: str, *, limit: int = MAX_NOTE_CHARS) -> str:
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def record_tool_result(
    state: AgentContextState,
    *,
    tool_name: str,
    success: bool,
    latency_ms: int,
    raw_ref: str,
    content_summary: str = "",
    fact: str | None = None,
    history_limit: int = 200,
) -> None:
    """Append a structured tool row + observed_fact entry.

    The whole function is a pure append — no mutation of existing rows, no
    storage of arbitrary content. ``content_summary`` is the harness's own
    reduction of the tool output (e.g. "Redis ping ok, used 23ms"); ``fact`` is
    an optional single-fact summary to land in ``observed_facts``.

    Both inputs are truncated to :data:`MAX_NOTE_CHARS` to enforce plan §8.2's
    "no raw payloads" rule.
    """
    note = _truncate(content_summary)
    summary = ToolSummary(
        tool=tool_name,
        status="success" if success else "failure",
        latency_ms=int(latency_ms or 0),
        note=note,
        raw_ref=raw_ref,
    )
    append_tool_summary(state, summary, history_limit=history_limit)

    if fact:
        item = EvidenceItem(
            source=tool_name,
            status="success" if success else "failure",
            latency_ms=int(latency_ms or 0),
            facts=[_truncate(fact, limit=120)],
            raw_ref=raw_ref,
        )
        append_observed_fact(state, item, history_limit=history_limit)
    elif not success and note:
        # Failure with a non-empty note → recorded as an evidence_gap so the
        # verifier knows this branch was attempted but didn't produce a fact.
        append_evidence_gap(state, note, history_limit=history_limit)


def record_delegate_merge(
    state: AgentContextState,
    *,
    mode: str,
    experts: list[str] | tuple[str, ...] | None,
    folded_tools: list[str] | tuple[str, ...] | None,
    status: str = "completed",
    wall_ms: int = 0,
    history_limit: int = 200,
) -> None:
    """Framework-only whiteboard write for parallel/aux merge (M2 W8 / WP-B4).

    Stores a short observed_fact listing unique successful child tools so the
    parent verifier can see multi-expert evidence without raw payloads.
    """
    expert_list = [str(e).strip() for e in (experts or []) if str(e or "").strip()]
    tools: list[str] = []
    seen: set[str] = set()
    for tool in folded_tools or []:
        name = str(tool or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        tools.append(name)
    fact = _truncate(
        f"delegate_merge mode={mode or '?'} status={status or '?'} "
        f"experts={','.join(expert_list) or '-'} tools={','.join(tools) or '-'}",
        limit=200,
    )
    ok = str(status) in {"completed", "degraded", "success"}
    raw_ref = f"delegate_merge:{mode}:{','.join(expert_list[:4])}"
    record_tool_result(
        state,
        tool_name="delegate_merge",
        success=ok,
        latency_ms=int(wall_ms or 0),
        raw_ref=raw_ref,
        content_summary=fact,
        fact=fact,
        history_limit=history_limit,
    )
    record_tool_call_meta(
        state,
        tool_name="delegate_merge",
        raw_ref=raw_ref,
        latency_ms=int(wall_ms or 0),
        status="success" if ok else "failure",
        history_limit=history_limit,
    )


def record_tool_call_meta(
    state: AgentContextState,
    *,
    tool_name: str,
    raw_ref: str,
    latency_ms: int,
    status: str,
    history_limit: int = 200,
) -> None:
    """Lightweight metadata write for the tool block (last latency / status)."""
    framework_patch(
        state,
        SECTION_TOOL,
        "last_latency_ms",
        op="set",
        value=int(latency_ms or 0),
        history_limit=history_limit,
    )
    framework_patch(
        state,
        SECTION_TOOL,
        "last_status",
        op="set",
        value=status,
        history_limit=history_limit,
    )
    framework_patch(
        state,
        SECTION_TOOL,
        "recent_calls",
        op="append",
        append_value={
            "tool": tool_name,
            "raw_ref": raw_ref,
            "latency_ms": int(latency_ms or 0),
            "status": status,
            "at": state.updated_at,
        },
        history_limit=history_limit,
    )
    # Cap the recent_calls list so it never grows unbounded.
    if len(state.tool.recent_calls) > 32:
        del state.tool.recent_calls[: len(state.tool.recent_calls) - 32]


def _looks_like_json_blob(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    # Object/array literals, or a JSON-encoded string (double-encoded blob).
    if stripped[0] in "{[" and stripped[-1] in "}]":
        return True
    return stripped[0] == '"' and stripped[-1] == '"'


def _parse_jsonish(value: Any) -> Any:
    """Best-effort unwrap of JSON / double-encoded JSON strings.

    Accepts object/array literals and JSON-encoded strings (which start with
    ``"``), so a double-encoded blob like ``'"{\\"a\\":1}"'`` still unwraps.
    """
    import json

    current: Any = value
    for _ in range(3):
        if not isinstance(current, str):
            return current
        text = current.strip()
        if not text or text[0] not in "{[\"":
            return current
        try:
            current = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return current
    return current


def _summarize_structured_payload(payload: Mapping[str, Any]) -> str:
    """Turn common tool payload shapes into a short human note."""
    # delegate_to_expert: {expert, status, subtask, answer?}
    if "expert" in payload and ("subtask" in payload or "answer" in payload or "status" in payload):
        parts: list[str] = []
        expert = str(payload.get("expert") or "").strip()
        status = str(payload.get("status") or "").strip()
        subtask = _truncate(str(payload.get("subtask") or ""), limit=80)
        answer = _truncate(
            str(payload.get("answer") or payload.get("error") or ""),
            limit=100,
        )
        if expert:
            parts.append(f"expert={expert}")
        if status:
            parts.append(f"status={status}")
        if subtask:
            parts.append(f"subtask={subtask}")
        if answer:
            parts.append(f"answer={answer}")
        if parts:
            return _truncate(" · ".join(parts))

    # context_read: {section, data} or {view} / {snapshot}
    if "section" in payload or "view" in payload or "snapshot" in payload:
        section = str(payload.get("section") or "").strip()
        data = payload.get("data")
        if isinstance(data, Mapping):
            tool_n = len(data.get("tool_summaries") or []) if isinstance(data.get("tool_summaries"), list) else 0
            fact_n = len(data.get("observed_facts") or []) if isinstance(data.get("observed_facts"), list) else 0
            gap_n = len(data.get("evidence_gaps") or []) if isinstance(data.get("evidence_gaps"), list) else 0
            bits = [b for b in [
                f"section={section}" if section else "",
                f"tools={tool_n}" if tool_n else "",
                f"facts={fact_n}" if fact_n else "",
                f"gaps={gap_n}" if gap_n else "",
            ] if b]
            if bits:
                return _truncate(" · ".join(bits))
        if section:
            return _truncate(f"section={section}")
        if payload.get("view"):
            return _truncate(f"view: {payload.get('view')}")
        return "context snapshot"

    # generic useful fields
    for key in ("message", "summary", "answer", "error", "status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and not _looks_like_json_blob(value):
            return _truncate(value)

    import json
    try:
        return _truncate(json.dumps(dict(payload), ensure_ascii=False))
    except TypeError:
        return _truncate(repr(payload))


def tool_result_to_summary(result: Mapping[str, Any]) -> tuple[str, str | None, bool, int]:
    """Reduce a raw tool result mapping into the (summary, fact, ok, latency) tuple.

    Recognized shapes:

    - ``{"ok": true, "content": "...", "summary": "..."}`` → uses ``summary`` if
      present, falls back to a truncated ``content``.
    - ``{"ok": true, "result": "..."}`` → uses ``result``.
    - structured delegate / context payloads → short field-based notes.
    - any other mapping → JSON-encoded at most :data:`MAX_NOTE_CHARS`.
    """
    ok = bool(result.get("ok", result.get("success", True)))
    latency = int(result.get("latency_ms", 0) or 0)

    summary = ""
    for key in ("summary", "content", "result"):
        if key in result and result[key]:
            raw_value = result[key]
            parsed = _parse_jsonish(raw_value)
            if isinstance(parsed, Mapping):
                summary = _summarize_structured_payload(parsed)
            else:
                text = str(raw_value)
                # Avoid storing double-encoded JSON blobs as the human note.
                if _looks_like_json_blob(text):
                    summary = _summarize_structured_payload(
                        parsed if isinstance(parsed, Mapping) else {"value": text}
                    )
                else:
                    summary = _truncate(text)
            break
    if not summary:
        # Prefer structured reduction over raw dump when the whole result is a
        # known payload shape (e.g. delegate / context_read without summary).
        if any(k in result for k in ("expert", "section", "view", "snapshot", "answer")):
            summary = _summarize_structured_payload(result)
        else:
            import json
            try:
                summary = _truncate(json.dumps(dict(result), ensure_ascii=False))
            except TypeError:
                summary = _truncate(repr(result))

    fact = None
    raw_fact = result.get("fact") or result.get("observed_fact")
    if isinstance(raw_fact, str) and raw_fact.strip():
        fact = _truncate(raw_fact, limit=120)
    return summary, fact, ok, latency


__all__ = [
    "MAX_NOTE_CHARS",
    "record_tool_result",
    "record_tool_call_meta",
    "tool_result_to_summary",
]
