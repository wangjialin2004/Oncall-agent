# Local RAG Evaluation Pipeline Implementation Plan

**Goal:** Build a minimal offline local pipeline that evaluates this project's RAG retrieval and answer generation over a small JSONL case set.

**Architecture:** Add a focused helper module under `app/evaluation/` for loading cases, building local evaluation rows, and writing CSV output. Add a CLI script under `scripts/` that calls the existing vector search service and knowledge expert, then computes deterministic local metrics.

**Tech Stack:** Python, pytest, existing `VectorSearchService`, and the knowledge expert path.

## Step 1: Evaluation case helpers

- Create: `app/evaluation/local_eval_pipeline.py`
- Test: local helper tests
- Implement `RagEvalCase`, `load_cases`, `build_eval_row`, and `default_output_path`.

## Step 2: Local CLI runner

- Create: `scripts/evaluate_rag_local.py`
- The script should retrieve contexts with `vector_search_service.search`, optionally generate responses through the knowledge expert, compute local scores, and always write a CSV.
- No external evaluation framework is required.

## Step 3: Verification

- Run focused helper tests.
- Run `python scripts/evaluate_rag_local.py --help`.
