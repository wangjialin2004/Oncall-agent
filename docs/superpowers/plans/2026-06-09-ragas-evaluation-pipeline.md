# RAGAS Evaluation Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal offline RAGAS pipeline that evaluates this project's RAG retrieval and answer generation over a small JSONL case set.

**Architecture:** Add a focused helper module under `app/evaluation/` for loading cases, building RAGAS-compatible rows, and writing CSV output. Add a CLI script under `scripts/` that calls the existing vector search service and RAG agent, then invokes RAGAS metrics.

**Tech Stack:** Python, pytest, RAGAS, datasets, existing `VectorSearchService` and `RagAgentService`.

---

### Task 1: Evaluation Helpers

**Files:**
- Create: `app/evaluation/__init__.py`
- Create: `app/evaluation/ragas_pipeline.py`
- Test: `tests/test_ragas_pipeline.py`

- [ ] Write failing tests for JSONL case loading, required field validation, sample row construction, and timestamped output path generation.
- [ ] Run `pytest tests/test_ragas_pipeline.py -q` and confirm failure because the module does not exist.
- [ ] Implement `RagEvalCase`, `load_cases`, `build_ragas_row`, and `default_output_path`.
- [ ] Run `pytest tests/test_ragas_pipeline.py -q` and confirm the helper tests pass.

### Task 2: Sample Dataset and CLI

**Files:**
- Create: `evals/rag_cases.jsonl`
- Create: `scripts/evaluate_rag_ragas.py`
- Modify: `pyproject.toml`

- [ ] Add 5 initial AIOps evaluation cases covering CPU, memory, disk, service unavailable, and slow response documents.
- [ ] Add `ragas` and `datasets` to dev dependencies.
- [ ] Add a CLI script with `--cases`, `--top-k`, `--output`, `--skip-generation`, and `--limit`.
- [ ] The script should retrieve contexts with `vector_search_service.search`, optionally generate responses with `RagAgentService(streaming=False).query`, run RAGAS metrics when generation is enabled, and always write a CSV.

### Task 3: Verification

**Files:**
- Test: `tests/test_ragas_pipeline.py`

- [ ] Run `pytest tests/test_ragas_pipeline.py -q`.
- [ ] Run `python scripts/evaluate_rag_ragas.py --help`.
- [ ] Report any command that cannot be run because external services or model credentials are required.
