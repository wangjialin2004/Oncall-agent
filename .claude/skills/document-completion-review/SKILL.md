---
name: document-completion-review
description: Use when the user asks Claude Code to review project completion, acceptance readiness, implementation completeness, delivery quality, remaining gaps, or post-implementation issues and wants the findings recorded in the project's docs directory.
---

# Document Completion Review

## Purpose

Perform a completion-focused project review and write the findings to a Markdown report under `docs/`. If `docs/` does not exist, create it before writing the report.

## Review Workflow

1. Determine the review scope from the user request. If the scope is unclear, infer it from the latest relevant files in `docs/`, `README.md`, plans, specs, tests, and recent git changes. State the assumption in the report.
2. Inspect the actual project state before judging completion:
   - `git status --short`
   - relevant plans/specs under `docs/`
   - README, dependency/config files, app entry points, API/frontend contracts, and tests related to the scope
3. Verify where practical. Run focused tests, builds, lint, or smoke checks that match the reviewed area. If a useful check is skipped, record the reason.
4. Compare promised work against implemented behavior, tests, docs, configuration, startup flow, and user-facing behavior.
5. Record actionable issues only. Prefer concrete evidence over broad commentary.
6. Create the review report in `docs/` using this naming pattern:
   - `docs/completion-review-YYYY-MM-DD.md` for general reviews
   - `docs/completion-review-YYYY-MM-DD-short-scope.md` when the scope is specific
   - If the file already exists, append `-2`, `-3`, and so on.
7. After writing the report, summarize the report path and the highest-priority findings in the final response.

## Report Format

Use Chinese by default for the written report in this project unless the user asks for another language.

```markdown
# Completion Review Report: <scope>

- Review date: YYYY-MM-DD
- Review scope: <files/features/modules reviewed>
- Source material: <plans/specs/README/issues/user request>
- Verification commands: <commands run and results, or reason skipped>

## Overall Conclusion

<1-3 paragraphs. Say whether the project/feature is ready, conditionally ready, or not ready.>

## Findings

### P0/P1/P2/P3 - <short issue title>

- Evidence: <file paths, test output, observed behavior>
- Impact: <why it matters for completion>
- Recommendation: <specific fix or next step>
- Verification: <how to confirm the fix>

## Completion Matrix

| Item | Status | Notes |
| --- | --- | --- |
| <requirement/work item> | Complete/Partial/Missing/Unknown | <evidence> |

## Test And Verification Notes

<commands, outcomes, and any gaps>

## Next Steps

<ordered next steps, highest priority first>
```

## Severity Guide

| Level | Meaning |
| --- | --- |
| P0 | Blocks core usage, data safety, security, or startup; release should stop. |
| P1 | Major promised capability missing or broken; release should wait unless explicitly accepted. |
| P2 | Important gap, edge case, missing test, or operational risk; should be scheduled soon. |
| P3 | Polish, maintainability, documentation, or low-risk improvement. |

## Common Mistakes

- Do not only summarize the code. The output must include a written report under `docs/`.
- Do not create reports outside `docs/` unless the user explicitly requests another location.
- Do not mark work complete because tests pass. Compare implementation with the requested goals and documented plans.
- Do not ignore existing dirty worktree changes. Treat them as the current project state and avoid reverting unrelated changes.
- Do not bury blockers in prose. Put blockers in `Findings` with severity and evidence.
