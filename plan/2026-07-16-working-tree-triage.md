# Working Tree Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use task-by-task execution and retain the checkbox state as the implementation record.

**Goal:** Make the recovered working tree safe to review and stage without deleting user files or mixing temporary recovery artifacts into product changes.

**Architecture:** Use `.git/info/exclude` for local-only exclusions so repository policy is not changed merely to accommodate a recovered checkout. Correct the four `git diff --check` findings, then document logical review groups without staging, committing, or changing unreviewed product files.

**Tech Stack:** Git status and local exclude rules; Markdown and Python whitespace cleanup.

---

## Scope and safeguards

- In scope: local exclusion of Git recovery pointers and `tmp/`, removal of confirmed whitespace errors, and a review-group manifest.
- Out of scope: deleting files, modifying `.gitignore`, staging files, committing, changing application behaviour, or resolving product-level diffs.
- Safeguard: excluded files remain on disk and are retained for audit; `.git/info/exclude` is local metadata and is never committed.

## Files

| File | Responsibility |
|---|---|
| `.git/info/exclude` | Keep local recovery pointers and temporary tooling out of `git status` and accidental staging. |
| `plan/memory-cache-layer.md` | Remove trailing whitespace reported by `git diff --check`. |
| `tests/test_harness_service.py` | Remove excess terminal blank lines reported by `git diff --check`. |
| `plan/2026-07-16-working-tree-triage.md` | Record review groups and validation. |
| `AGENTS.md` | Index this plan and its final status. |

## Tasks

### Task 1: Establish local staging boundaries

- [x] Add `.git.broken-20260716`, `.git.invalid-pointer-20260716`, and `tmp/` to `.git/info/exclude`.
- [x] Confirm the excluded files still exist on disk and no longer appear as untracked status entries.

### Task 2: Correct confirmed whitespace errors

- [x] Remove the three trailing spaces in `plan/memory-cache-layer.md` lines 324-326.
- [x] Leave exactly one terminating newline in `tests/test_harness_service.py`.
- [x] Run `git diff --check` and require exit code zero.

### Task 3: Define review groups without mutating the index

- [x] Record the functional groups: Harness/context backend, frontend process UI, tests/evaluations, deployment/automation, and plans/documentation.
- [x] Record that the two historical completion-review deletions are identical moves to `docs/reviews/`.
- [x] Confirm no file is staged and update the plan index.

## Outcome — completed 2026-07-16

The recovery pointers and `tmp/` remain on disk but are excluded locally through `.git/info/exclude`; visible Git status entries fell from 252 to 227. The index is empty. The three Markdown trailing-whitespace findings and the excess EOF blank lines were removed, and `git diff --check` now exits zero. No product behaviour, user files, staging state, or commit history was changed.

## Verification

```powershell
git status --short
git diff --check
git diff --cached --quiet
Test-Path .git.broken-20260716
Test-Path .git.invalid-pointer-20260716
```

Expected: no recovery pointers or `tmp/` files appear in status, `git diff --check` exits zero, the index remains empty, and both backup files remain on disk.

## Review groups

1. Harness/context backend: `app/agent/context/`, `app/agent/harness/`, related API/service/config files, and backend tests.
2. Frontend process UI: `frontend/src/components/agent-process/`, `AgentProcessPanel`, stream/event types, component tests, and styles.
3. Tests and evaluations: `tests/`, `evals/`, and evaluation scripts.
4. Delivery and automation: `.github/`, `deploy/`, and non-temporary `scripts/`.
5. Plans and documentation: `AGENTS.md`, adapters, `plan/`, `docs/`, and the two review-document moves.

## Rollback

Remove only the three local patterns from `.git/info/exclude` to make those files visible again. Whitespace edits can be reverted independently without changing product logic.
