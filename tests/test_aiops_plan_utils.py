from app.agent.aiops.plan_utils import normalize_plan_steps, plan_step_text, pop_next_plan_step


def test_normalize_plan_steps_converts_strings_to_structured_steps():
    steps = normalize_plan_steps(["Check metrics", "Search logs"])

    assert steps == [
        {
            "step_id": "plan-1",
            "description": "Check metrics",
            "tool_category": "unknown",
            "expected_evidence": "Evidence requested by the plan step.",
        },
        {
            "step_id": "plan-2",
            "description": "Search logs",
            "tool_category": "unknown",
            "expected_evidence": "Evidence requested by the plan step.",
        },
    ]


def test_normalize_plan_steps_preserves_structured_fields():
    steps = normalize_plan_steps(
        [
            {
                "description": "Check latency",
                "tool_category": "monitor",
                "expected_evidence": "Latency trend",
            }
        ]
    )

    assert steps[0]["step_id"] == "plan-1"
    assert steps[0]["description"] == "Check latency"
    assert steps[0]["tool_category"] == "monitor"
    assert steps[0]["expected_evidence"] == "Latency trend"


def test_plan_step_text_accepts_string_and_dict():
    assert plan_step_text("Check metrics") == "Check metrics"
    assert plan_step_text({"description": "Check logs"}) == "Check logs"


def test_pop_next_plan_step_returns_next_step_and_remaining_steps():
    current, remaining = pop_next_plan_step(
        [
            {"step_id": "plan-1", "description": "Check metrics"},
            {"step_id": "plan-2", "description": "Search logs"},
        ]
    )

    assert current == {"step_id": "plan-1", "description": "Check metrics"}
    assert remaining == [{"step_id": "plan-2", "description": "Search logs"}]
