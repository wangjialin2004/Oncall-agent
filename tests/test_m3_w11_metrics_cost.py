"""M3 W11: agent tokens / tool metrics (cardinality-safe)."""

from __future__ import annotations

from app.core import metrics as metrics_mod


def test_observe_agent_tokens_roles_and_skip_missing():
    metrics_mod.observe_agent_tokens(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
    )
    # Missing / empty / non-numeric must not raise
    metrics_mod.observe_agent_tokens(None)
    metrics_mod.observe_agent_tokens({})
    metrics_mod.observe_agent_tokens({"prompt_tokens": "nope", "total_tokens": -1})


def test_observe_tool_call_whitelist_and_status():
    metrics_mod.observe_tool_call(tool="search_app_logs", status="completed")
    metrics_mod.observe_tool_call(tool="search_app_logs", status="failed")
    metrics_mod.observe_tool_call(tool="search_app_logs", status="timeout")
    # Unknown tool → other bucket
    metrics_mod.observe_tool_call(tool="totally_unknown_tool_xyz", status="weird")
    # count <= 0 no-op
    metrics_mod.observe_tool_call(tool="search_app_logs", status="ok", count=0)


def test_normalize_helpers():
    assert metrics_mod._normalize_tool_name("search_app_logs") == "search_app_logs"
    assert metrics_mod._normalize_tool_name("WeirdThing") == "other"
    assert metrics_mod._normalize_tool_status("completed") == "ok"
    assert metrics_mod._normalize_tool_status("failed") == "error"
    assert metrics_mod._normalize_tool_status("timeout") == "timeout"
    assert metrics_mod._normalize_tool_status("???") == "other"


def test_observe_tool_calls_from_timeline():
    events = [
        {"type": "agent_event", "stage": "start"},
        {
            "type": "tool_event",
            "tool": "delegate_to_expert",
            "status": "completed",
        },
        {
            "type": "tool_event",
            "tool": "query_prometheus",
            "status": "failed",
        },
        {"type": "tool_event"},  # empty tool → other
    ]
    metrics_mod.observe_tool_calls_from_timeline(events)
    metrics_mod.observe_tool_calls_from_timeline(None)
    metrics_mod.observe_tool_calls_from_timeline([{"not": "dict"}])  # type: ignore[list-item]
