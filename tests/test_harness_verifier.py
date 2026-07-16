from app.agent.harness.verifier import EvidenceVerifier


def test_verifier_keeps_medium_confidence_when_successful_evidence_exists() -> None:
    result = EvidenceVerifier().verify(
        answer="Redis ping succeeded.",
        timeline_events=[
            {
                "type": "tool_event",
                "tool": "check_redis_health",
                "status": "completed",
                "summary": '{"success": true, "status": "connected"}',
            },
            {
                "type": "tool_event",
                "tool": "search_app_logs",
                "status": "failed",
                "summary": "log backend unavailable",
            },
        ],
        plan=None,
    )

    assert result.status == "degraded"
    assert result.confidence == "medium"
    assert result.evidence_count == 1
    assert result.failed_evidence_count == 1
