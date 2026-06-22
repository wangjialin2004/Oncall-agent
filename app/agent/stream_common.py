"""Shared scaffolding for the streaming orchestrators (router + harness).

The legacy ``RouterService`` and the unified ``HarnessService`` expose the same
streaming contract (route event → optional clarify → expert/loop events →
timeout fallback → ``complete``). These constants and helpers are the pieces
both share, kept in one place so they can't drift apart.
"""

from __future__ import annotations

# Events persisted into the final response timeline (everything except content/complete).
TIMELINE_EVENT_TYPES = {"route_event", "agent_event", "tool_event", "decision_event"}

# Shown when the request carries no actionable intent.
CLARIFY_TEXT = "请补充你想咨询的问题，或说明需要诊断的服务、告警、日志现象、近期变更。"


def build_timeout_report(*, subject: str, message: str, timeout_seconds: float) -> str:
    """Degraded, traceable answer returned when a stream exceeds its time budget."""
    return (
        "# 降级响应\n\n"
        f"- {subject}执行超过 {timeout_seconds:g} 秒，已先返回可追踪的降级结果。\n"
        "- 前后端链路与时间线可继续使用，请检查 LLM、MCP、监控或日志数据源后重试。\n\n"
        f"原始请求：{message}"
    )
