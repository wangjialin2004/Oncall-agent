"""Large-log processing pipeline for the 日志分析 Agent.

Tens of thousands of raw log lines must never enter the LLM context directly.
This module collapses them deterministically before any model call:

  1. extract  — pull log lines out of the (JSON or plain-text) tool result
  2. cluster  — Drain-style template mining: normalize each line to a signature,
                group + count, keep a representative sample (pure Python, no LLM)
  3. reduce   — keep the Top-N templates (errors first), fold the long tail into a
                single counted summary so nothing is silently dropped
  4. map-reduce summarize — only if the structured digest still exceeds the token
                budget, chunk it, LLM-summarize each chunk, then merge

The output is a compact, structured digest handed back to the answering LLM.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.agent.events import make_agent_event
from app.agent.experts.base import estimate_tokens
from app.config import config
from app.core.llm_client import ChatMessage

# Normalization patterns — order matters (specific → generic).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TIME>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"https?://[^\s\"']+"), "<URL>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HASH>"),
    (re.compile(r"(?:/[\w.\-]+){2,}/?"), "<PATH>"),
    (re.compile(r"\"[^\"]*\""), "<STR>"),
    (re.compile(r"\b\d+\b"), "<NUM>"),
]

_LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|ERROR|ERR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b", re.IGNORECASE)
_ERROR_LEVELS = {"FATAL", "CRITICAL", "ERROR", "ERR"}


@dataclass(slots=True)
class LogPattern:
    template: str
    count: int = 0
    sample: str = ""
    level: str = ""
    first_index: int = 0
    last_index: int = 0


@dataclass(slots=True)
class LogDigest:
    total_lines: int
    truncated: bool
    patterns: list[LogPattern]
    tail_pattern_count: int = 0
    tail_line_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def extract_log_lines(raw: str) -> list[str]:
    """Best-effort extraction of individual log lines from a tool result string."""
    text = (raw or "").strip()
    if not text:
        return []

    # Try structured JSON first (most MCP log tools return JSON).
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = None

    if parsed is not None:
        lines = _lines_from_json(parsed)
        if lines:
            return lines

    return [line for line in text.splitlines() if line.strip()]


def _lines_from_json(obj: Any) -> list[str]:
    if isinstance(obj, list):
        return [_entry_to_line(item) for item in obj if item is not None]
    if isinstance(obj, dict):
        for key in ("logs", "data", "results", "items", "entries", "content", "messages"):
            value = obj.get(key)
            if isinstance(value, list):
                return [_entry_to_line(item) for item in value if item is not None]
    return []


def _entry_to_line(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("message", "msg", "log", "content", "line", "text", "raw"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                level = item.get("level") or item.get("severity") or ""
                ts = item.get("timestamp") or item.get("time") or item.get("@timestamp") or ""
                prefix = " ".join(str(p) for p in (ts, level) if p).strip()
                return f"{prefix} {value}".strip() if prefix else value
        return json.dumps(item, ensure_ascii=False, default=str)
    return str(item)


def normalize_line(line: str) -> str:
    """Reduce a log line to a template signature by masking volatile tokens."""
    template = line.strip()
    for pattern, replacement in _PATTERNS:
        template = pattern.sub(replacement, template)
    return re.sub(r"\s+", " ", template).strip()


def _detect_level(line: str) -> str:
    match = _LEVEL_RE.search(line)
    if not match:
        return ""
    level = match.group(1).upper()
    if level == "ERR":
        return "ERROR"
    if level.startswith("WARN"):
        return "WARN"
    return level


def cluster_lines(lines: list[str]) -> list[LogPattern]:
    """Group lines by normalized template; keep counts + first representative."""
    groups: OrderedDict[str, LogPattern] = OrderedDict()
    for index, line in enumerate(lines):
        signature = normalize_line(line)
        if not signature:
            continue
        pattern = groups.get(signature)
        if pattern is None:
            groups[signature] = LogPattern(
                template=signature,
                count=1,
                sample=line.strip(),
                level=_detect_level(line),
                first_index=index,
                last_index=index,
            )
        else:
            pattern.count += 1
            pattern.last_index = index
            if not pattern.level:
                pattern.level = _detect_level(line)
    return list(groups.values())


def _pattern_sort_key(pattern: LogPattern) -> tuple[int, int]:
    # Errors first, then by frequency (both descending).
    error_priority = 1 if pattern.level in _ERROR_LEVELS else 0
    return (error_priority, pattern.count)


def build_digest(raw: str, *, max_lines: int, top_patterns: int) -> LogDigest:
    lines = extract_log_lines(raw)
    total = len(lines)
    truncated = total > max_lines
    if truncated:
        # Keep the most recent lines (typical log order is chronological).
        lines = lines[-max_lines:]

    patterns = cluster_lines(lines)
    patterns.sort(key=_pattern_sort_key, reverse=True)

    kept = patterns[:top_patterns]
    tail = patterns[top_patterns:]
    return LogDigest(
        total_lines=total,
        truncated=truncated,
        patterns=kept,
        tail_pattern_count=len(tail),
        tail_line_count=sum(p.count for p in tail),
    )


def render_digest(digest: LogDigest) -> str:
    """Render the structured digest as compact text for the answering LLM."""
    header = [
        "## 日志聚类分析（确定性预处理，非原始日志）",
        f"- 原始日志行数：{digest.total_lines}"
        + ("（已按时间倒序截断）" if digest.truncated else ""),
        f"- 去重后模板数：{len(digest.patterns) + digest.tail_pattern_count}",
        "",
        "### Top 模板（错误优先，按频次排序）",
    ]
    body: list[str] = []
    for i, pattern in enumerate(digest.patterns, 1):
        level = pattern.level or "—"
        body.append(
            f"{i}. [count={pattern.count}][level={level}] {pattern.template}\n"
            f"   示例: {pattern.sample[:300]}"
        )
    if digest.tail_pattern_count:
        body.append(
            f"\n（另有 {digest.tail_pattern_count} 个低频模板共 "
            f"{digest.tail_line_count} 行已折叠，未逐条展开）"
        )
    return "\n".join(header + body).strip()


async def _mapreduce_summarize(
    text: str,
    *,
    llm_client: Any,
    chunk_size: int,
    model: str | None,
) -> str:
    """Chunk an oversized digest, summarize each chunk, then merge."""
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    logger.info(f"日志摘要 Map-Reduce：{len(chunks)} 个分块")

    partials: list[str] = []
    for idx, chunk in enumerate(chunks, 1):
        response = await llm_client.complete(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "你是日志分析助手。请把下面这批日志模板压缩为要点摘要："
                        "突出错误/异常模板、频次与可能的故障信号。只输出要点，不要寒暄。"
                    ),
                ),
                ChatMessage(role="user", content=f"第 {idx}/{len(chunks)} 批：\n{chunk}"),
            ],
            temperature=0,
            model=model or None,
        )
        partials.append(response.content.strip())

    if len(partials) == 1:
        return partials[0]

    merge = await llm_client.complete(
        [
            ChatMessage(
                role="system",
                content="将多批日志摘要合并为一份统一摘要，去重并按严重程度排序，保留频次信息。",
            ),
            ChatMessage(role="user", content="\n\n".join(partials)),
        ],
        temperature=0,
        model=model or None,
    )
    return merge.content.strip()


async def analyze_logs(
    raw: str,
    *,
    llm_client: Any,
    trace_id: str,
    events_sink: list[dict[str, Any]],
) -> str:
    """Full pipeline entry. Returns a compact digest safe for LLM context."""
    digest = build_digest(
        raw,
        max_lines=config.log_max_lines,
        top_patterns=config.log_top_patterns,
    )

    events_sink.append(
        make_agent_event(
            agent="log_expert",
            stage="log_pipeline",
            status="completed",
            summary=(
                f"日志预处理：{digest.total_lines} 行 → "
                f"{len(digest.patterns) + digest.tail_pattern_count} 个模板"
            ),
            payload={
                "total_lines": digest.total_lines,
                "truncated": digest.truncated,
                "kept_patterns": len(digest.patterns),
                "tail_patterns": digest.tail_pattern_count,
            },
            trace_id=trace_id,
            span_id=f"log_pipeline:{trace_id}",
        )
    )

    rendered = render_digest(digest)

    # Only pay for an LLM pass when the deterministic digest is still too large.
    if estimate_tokens(rendered) > config.log_token_budget:
        summary = await _mapreduce_summarize(
            rendered,
            llm_client=llm_client,
            chunk_size=config.log_chunk_size,
            model=config.log_summary_model or None,
        )
        events_sink.append(
            make_agent_event(
                agent="log_expert",
                stage="log_mapreduce",
                status="completed",
                summary="日志摘要超出 token 预算，已执行 Map-Reduce 压缩",
                trace_id=trace_id,
                span_id=f"log_mapreduce:{trace_id}",
            )
        )
        return f"{summary}\n\n（以上为对 {digest.total_lines} 行日志的压缩摘要）"

    return rendered
