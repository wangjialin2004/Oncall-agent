# Model-Agnostic Agent Governance Documentation Implementation Plan

> **For agentic workers:** Execute the tasks in order and retain the checkboxes as the implementation record.

**Goal:** Make `AGENTS.md` the single source of truth for repository governance while keeping Claude Code and Codex instructions as small, explicit model adapters.

**Architecture:** Shared repository rules, safety boundaries, planning requirements, and documentation locations live only in `AGENTS.md`. `CLAUDE.md` and the new `CODEX.md` only describe how their respective hosts consume that shared policy; neither may override or repeat it.

**Tech Stack:** Markdown documentation; repository instruction-file discovery conventions.

---

## Context

`AGENTS.md` is already the project navigation entry, but `CLAUDE.md` contains the enforceable workflow and safety rules. That makes a Claude-named file the de facto policy source for every model, while Codex has no equivalent adapter. Duplicating the full rules into separate files would create drift.

## Design decisions

- `AGENTS.md` is the vendor-neutral, canonical project policy and navigation entry.
- Model adapters may add host-specific execution guidance only; they cannot weaken project safety, planning, verification, or documentation requirements.
- Unknown or unsupported agent hosts use `AGENTS.md` alone rather than treating `CLAUDE.md` as generic policy.
- The existing plan index remains in `AGENTS.md`; this plan is added before the documentation migration.

## Scope and non-goals

In scope: clarify precedence, move the compact set of enforceable shared workflow rules into `AGENTS.md`, reduce `CLAUDE.md`, and create `CODEX.md`.

Out of scope: changing the product architecture, editing model system prompts, adding adapters for every third-party IDE, or changing the existing project roadmap and pilot records.

## File list

| File | Change |
|---|---|
| `AGENTS.md` | Add canonical governance and model-adapter map; link this plan. |
| `CLAUDE.md` | Replace duplicated project policy with a Claude Code adapter. |
| `CODEX.md` | Create the equivalent Codex adapter. |
| `plan/2026-07-16-model-agent-governance.md` | Record decisions, implementation steps, and validation. |

## Tasks

### Task 1: Establish the canonical shared policy

- [x] Add this plan to the `AGENTS.md` current-plan index.
- [x] Add an instruction-governance section defining precedence, the shared workflow requirements, safety boundaries, documentation responsibilities, and the policy for unrecognised models.
- [x] Keep model-specific execution mechanics out of the shared section.

### Task 2: Create thin model adapters

- [x] Replace `CLAUDE.md` with a Claude Code adapter that explicitly delegates all repository policy to `AGENTS.md`.
- [x] Create `CODEX.md` with the equivalent Codex entrypoint and Codex-only host guidance.
- [x] State in both files that a conflict with `AGENTS.md` is resolved in favour of `AGENTS.md`, subject to higher-priority host and user instructions.

### Task 3: Verify the documentation contract

- [x] Confirm all three files are valid UTF-8.
- [x] Confirm `AGENTS.md` links to both adapters and the new plan.
- [x] Confirm each adapter points to `AGENTS.md` and contains no copied project roadmap, architecture, or safety-policy blocks.

## Verification

Run:

```powershell
$files = 'AGENTS.md', 'CLAUDE.md', 'CODEX.md', 'plan/2026-07-16-model-agent-governance.md'
$utf8 = [System.Text.UTF8Encoding]::new($false, $true)
$files | ForEach-Object { $null = $utf8.GetString([System.IO.File]::ReadAllBytes($_)); "UTF-8 OK: $_" }
Select-String -LiteralPath AGENTS.md -Pattern 'CLAUDE.md', 'CODEX.md', '2026-07-16-model-agent-governance.md'
Select-String -LiteralPath CLAUDE.md, CODEX.md -Pattern 'AGENTS.md'
```

Expected: every listed file reports UTF-8 success; `AGENTS.md` contains all three links; each adapter explicitly links back to `AGENTS.md`.

## Risks and rollback

- Risk: model-specific files quietly reaccumulate project policy. Mitigation: both adapters state their intentionally narrow scope, and the canonical policy names them as adapters.
- Risk: an unsupported host ignores an adapter. Mitigation: `AGENTS.md` remains complete on its own.
- Rollback: restore the prior `CLAUDE.md` and remove `CODEX.md`, but retain this plan as the decision record if the migration is deferred.
