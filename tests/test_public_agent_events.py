from __future__ import annotations

import json

from app.agent.public_events import PublicEventProjector, to_public_timeline_event
from app.config import config


def test_public_tool_event_removes_private_fields_and_internal_ids() -> None:
    event = {
        "type": "tool_event",
        "agent": "harness",
        "tool": "search_app_logs",
        "status": "completed",
        "summary": "customer-secret raw log",
        "evidence_id": "call-secret",
        "trace_id": "trace-secret",
        "span_id": "span-secret",
        "usage": {"total_tokens": 20},
        "payload": {
            "arguments": {"keyword": "customer-secret"},
            "result": {"raw": "private-log"},
            "subtask": "private-subtask",
            "delegated_expert": "log",
            "tool_latency_ms": 125,
        },
    }

    public = to_public_timeline_event(event)
    serialized = json.dumps(public)

    assert public["tool"] == "search_app_logs"
    assert public["payload"]["delegated_expert"] == "log"
    assert public["payload"]["tool_call_id"] == "activity-1"
    assert public["duration_ms"] == 125
    assert "trace_id" not in public
    assert "span_id" not in public
    assert "evidence_id" not in public
    assert "summary" not in public
    assert "customer-secret" not in serialized
    assert "private-log" not in serialized
    assert "private-subtask" not in serialized


def test_projector_keeps_one_opaque_id_across_start_terminal_and_child_events() -> None:
    projector = PublicEventProjector()

    start = projector.project_timeline(
        {
            "type": "agent_event",
            "agent": "harness",
            "stage": "tool_start",
            "status": "in_progress",
            "payload": {"tool": "query_cpu_metrics", "tool_call_id": "raw-call-7"},
        }
    )
    terminal = projector.project_timeline(
        {
            "type": "tool_event",
            "agent": "harness",
            "tool": "query_cpu_metrics",
            "status": "completed",
            "evidence_id": "raw-call-7",
            "payload": {"result": "private"},
        }
    )
    child = projector.project_timeline(
        {
            "type": "agent_event",
            "agent": "metric_expert",
            "stage": "analysis",
            "status": "completed",
            "payload": {
                "parent_tool_call_id": "raw-call-7",
                "delegated_expert": "metric",
            },
        }
    )

    assert start["payload"]["tool_call_id"] == "activity-1"
    assert terminal["payload"]["tool_call_id"] == "activity-1"
    assert child["payload"]["parent_tool_call_id"] == "activity-1"


def test_complete_projects_nested_events_and_preserves_user_facing_fields() -> None:
    private_event = {
        "type": "tool_event",
        "tool": "query_prometheus_alerts",
        "status": "failed",
        "evidence_id": "raw-9",
        "payload": {"arguments": {"tenant": "private-tenant"}, "result": "raw-error"},
    }
    projector = PublicEventProjector()

    public = projector.project_stream(
        {
            "type": "complete",
            "route": "diagnosis",
            "answer": "User-facing answer",
            "case_id": "case-1",
            "replace_streamed_answer": True,
            "missing_params": ["service"],
            "suggested_actions": [
                {
                    "id": "inspect_metrics",
                    "title": "Inspect metrics",
                    "risk": "low",
                    "requires_confirm": True,
                    "internal": "drop-me",
                }
            ],
            "events": [private_event],
            "_unified_context_commit_attempted": True,
        }
    )
    serialized = json.dumps(public)

    assert public["answer"] == "User-facing answer"
    assert public["replace_streamed_answer"] is True
    assert public["missing_params"] == ["service"]
    assert public["suggested_actions"] == [
        {
            "id": "inspect_metrics",
            "title": "Inspect metrics",
            "risk": "low",
            "requires_confirm": True,
        }
    ]
    assert public["events"][0]["payload"]["tool_call_id"] == "activity-1"
    assert "private-tenant" not in serialized
    assert "raw-error" not in serialized
    assert "_unified_context" not in serialized


def test_checkpoint_payload_keeps_only_safe_resume_fields() -> None:
    public = to_public_timeline_event(
        {
            "type": "agent_event",
            "agent": "harness",
            "stage": "checkpoint_resume",
            "status": "completed",
            "payload": {
                "resumed_from_step": 2,
                "replayed_steps": 1,
                "started_at": "2026-07-26T12:00:00Z",
                "conservative": True,
                "replay_override": False,
                "messages": ["private history"],
            },
        }
    )

    assert public["payload"] == {
        "resumed_from_step": 2,
        "replayed_steps": 1,
        "started_at": "2026-07-26T12:00:00Z",
        "conservative": True,
        "replay_override": False,
    }


def test_public_progress_details_expose_plan_result_and_evidence_gap_summaries(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "harness_public_progress_details_enabled", True)
    projector = PublicEventProjector()

    plan = projector.project_timeline(
        {
            "type": "agent_event",
            "agent": "harness",
            "stage": "plan",
            "status": "completed",
            "payload": {
                "todos": ["查询告警", "核对日志"],
                "required_evidence": ["指标证据", "日志证据"],
                "required_params": [{"prompt": "private prompt"}],
            },
        }
    )
    tool = projector.project_timeline(
        {
            "type": "tool_event",
            "agent": "harness",
            "tool": "query_metrics",
            "status": "completed",
            "evidence_id": "private-call-id",
            "payload": {
                "result": json.dumps(
                    {
                        "status": "success",
                        "count": 2,
                        "items": [{"summary": "CPU 92%"}],
                        "token": "token=do-not-leak",
                    },
                    ensure_ascii=False,
                ),
                "arguments": {"tenant": "private-tenant"},
            },
        }
    )
    verify = projector.project_timeline(
        {
            "type": "agent_event",
            "agent": "harness",
            "stage": "verify",
            "status": "degraded",
            "payload": {
                "confidence": "low",
                "evidence_count": 1,
                "failed_evidence_count": 1,
                "gaps": ["缺少日志证据"],
            },
        }
    )

    assert plan["payload"]["todos"] == ["查询告警", "核对日志"]
    assert plan["payload"]["required_evidence"] == ["指标证据", "日志证据"]
    assert "required_params" not in json.dumps(plan, ensure_ascii=False)
    assert tool["payload"]["result_preview"]
    assert {field["label"] for field in tool["payload"]["result_fields"]} >= {"status", "count"}
    assert tool["payload"]["result_items"] == ["CPU 92%"]
    assert "token=do-not-leak" not in json.dumps(tool, ensure_ascii=False)
    assert "private-tenant" not in json.dumps(tool, ensure_ascii=False)
    assert "private-call-id" not in json.dumps(tool, ensure_ascii=False)
    assert verify["payload"] == {
        "evidence_count": 1,
        "failed_evidence_count": 1,
        "confidence": "low",
        "gaps": ["缺少日志证据"],
    }


def test_public_tool_result_exposes_bounded_metric_samples_and_nested_statistics(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "harness_public_progress_details_enabled", True)
    public = to_public_timeline_event(
        {
            "type": "tool_event",
            "tool": "query_cpu_metrics",
            "status": "completed",
            "payload": {
                "result": {
                    "status": "success",
                    "source": "prometheus",
                    "service_name": "api",
                    "metric_name": "cpu_usage_percent",
                    "retrieval_type": "prometheus_query_range",
                    "interval": "1m",
                    "data_points": [
                        {"timestamp": "12:00", "value": 42.5},
                        {"timestamp": "12:01", "value": 48.0},
                    ],
                    "statistics": {"avg": 45.25, "max": 48.0, "min": 42.5, "p95": 48.0},
                    "alert_info": {
                        "triggered": False,
                        "threshold": 80.0,
                        "message": "CPU 使用率正常",
                    },
                    "promql": "private query should not be published",
                }
            },
        }
    )

    fields = public["payload"]["result_fields"]
    labels = {field["label"] for field in fields}
    assert {"metric_name", "interval", "statistics.avg", "statistics.max"} <= labels
    assert public["payload"]["result_items"] == ["12:00=42.5", "12:01=48.0"]
    serialized = json.dumps(public, ensure_ascii=False)
    assert "private query should not be published" not in serialized


def test_public_tool_result_keeps_typed_resource_metrics_without_phone_redaction(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "harness_public_progress_details_enabled", True)
    public = to_public_timeline_event(
        {
            "type": "tool_event",
            "tool": "get_local_resource_usage",
            "status": "completed",
            "payload": {
                "result": {
                    "status": "success",
                    "source": "local-machine",
                    "cpu": {"usage_percent": 28.0, "count": 24},
                    "memory": {
                        "usage_percent": 40.8,
                        "total_bytes": 17_179_869_184,
                        "used_bytes": 6_669_352_960,
                        "available_bytes": 9_684_402_176,
                    },
                }
            },
        }
    )

    fields = {
        field["label"]: field["value"]
        for field in public["payload"]["result_fields"]
    }
    assert fields["cpu.usage_percent"] == "28.0"
    assert fields["memory.total_bytes"] == "17179869184"


def test_public_progress_details_flag_restores_minimal_contract(monkeypatch) -> None:
    monkeypatch.setattr(config, "harness_public_progress_details_enabled", False)
    public = to_public_timeline_event(
        {
            "type": "agent_event",
            "stage": "plan",
            "status": "completed",
            "payload": {
                "todos": ["private plan"],
                "required_evidence": ["private evidence"],
            },
        }
    )

    assert "todos" not in public.get("payload", {})
    assert "required_evidence" not in public.get("payload", {})
