from pathlib import Path

from app.evaluation.local_eval_pipeline import RagEvalCase, build_eval_row, default_output_path, load_cases
from scripts.evaluate_rag_local import add_local_scores


def test_load_cases_and_build_eval_row(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        '{"question":"CPU alert?","ground_truth":"CPU is high","expected_sources":["runbook.md"]}\n',
        encoding="utf-8",
    )

    cases = load_cases(cases_path)
    assert cases == [
        RagEvalCase(
            question="CPU alert?",
            ground_truth="CPU is high",
            expected_sources=["runbook.md"],
        )
    ]

    row = build_eval_row(
        case=cases[0],
        response="CPU is high because checkout is saturated",
        retrieved_contexts=["CPU is high"],
        retrieved_sources=["runbook.md"],
        retrieved_chunk_ids=["chunk-1"],
    )

    assert row["user_input"] == "CPU alert?"
    assert row["retrieved_chunk_ids"] == ["chunk-1"]


def test_add_local_scores() -> None:
    scored = add_local_scores(
        [
            {
                "reference": "cpu high checkout",
                "response": "checkout cpu high",
                "retrieved_contexts": ["checkout cpu high runbook"],
                "expected_sources": ["runbook.md"],
                "retrieved_sources": ["runbook.md", "other.md"],
            }
        ]
    )

    assert scored[0]["local_source_recall"] == 1.0
    assert scored[0]["local_source_precision"] == 0.5
    assert scored[0]["local_reference_context_recall"] == 1.0
    assert scored[0]["local_reference_response_recall"] == 1.0
    assert scored[0]["local_response_context_support"] == 1.0


def test_default_output_path_uses_local_rag_prefix() -> None:
    assert default_output_path().parent == Path("evals") / "results"
    assert default_output_path().name.startswith("local_rag_")
