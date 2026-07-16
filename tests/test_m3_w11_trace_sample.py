"""M3 W11: harness trace sampling + payload fields; OTEL no-op; dual-path config."""

from __future__ import annotations

import json
from pathlib import Path

from app.agent.harness import otel_export, trace_export
from app.agent.harness.state import HarnessState
from app.config import config


def test_trace_sample_rate_zero_never_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "harness_trace_export_enabled", True, raising=False)
    monkeypatch.setattr(config, "harness_trace_sample_rate", 0.0, raising=False)
    monkeypatch.setattr(config, "harness_trace_export_dir", str(tmp_path), raising=False)
    state = HarnessState(trace_id="t1", session_id="s-rate0")
    state.usage_total = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    path = trace_export.maybe_export_harness_trace(session_id="s-rate0", state=state)
    assert path is None
    assert list(tmp_path.glob("*.json")) == []


def test_trace_disabled_never_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "harness_trace_export_enabled", False, raising=False)
    monkeypatch.setattr(config, "harness_trace_sample_rate", 1.0, raising=False)
    monkeypatch.setattr(config, "harness_trace_export_dir", str(tmp_path), raising=False)
    state = HarnessState(trace_id="t2", session_id="s-off")
    assert trace_export.maybe_export_harness_trace(session_id="s-off", state=state) is None


def test_trace_sample_rate_one_writes_with_w11_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "harness_trace_export_enabled", True, raising=False)
    monkeypatch.setattr(config, "harness_trace_sample_rate", 1.0, raising=False)
    monkeypatch.setattr(config, "harness_trace_export_dir", str(tmp_path), raising=False)
    state = HarnessState(trace_id="t3", session_id="s-rate1")
    state.route = "diagnosis"
    state.usage_total = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    state.timeline_events.append(
        {"type": "tool_event", "tool": "search_app_logs", "status": "completed"}
    )
    path = trace_export.maybe_export_harness_trace(
        session_id="s-rate1",
        state=state,
        re_evidence_rounds=1,
        replan_times=0,
        distill_draft={
            "id": "exp-1",
            "status": "pending",
            "requires_confirm": True,
            "title": "demo",
        },
        anti_pattern={"id": "ap-1", "source_type": "anti_pattern", "tool": "query_prometheus"},
    )
    assert path is not None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["session_id"] == "s-rate1"
    assert payload["usage"]["total_tokens"] == 18
    assert payload["distill_draft"]["id"] == "exp-1"
    assert payload["anti_pattern"]["tool"] == "query_prometheus"
    assert len(payload["timeline_events"]) == 1


def test_otel_empty_endpoint_noop(monkeypatch):
    monkeypatch.setattr(config, "otel_exporter_otlp_endpoint", "", raising=False)
    ok = otel_export.maybe_export_otel_span(
        session_id="s-otel",
        route="diagnosis",
        status="completed",
        latency_seconds=1.2,
        usage={"total_tokens": 3},
    )
    assert ok is False


def test_stateful_context_default_true():
    # Product default: stateful primary path (H4).
    assert bool(getattr(config, "harness_stateful_context_enabled", False)) is True
