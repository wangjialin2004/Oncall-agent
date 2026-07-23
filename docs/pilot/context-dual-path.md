# Context Path — Single repository, dual render policy

> **Date**: 2026-07-19 (updated from 2026-07-15 H4)
> **Status**: unified repository **code repair and local gates complete behind a default-off flag**; live schema/apply, Redis instance audit and canary remain gated, so do not enable yet
> **Related**: `plan/2026-07-19-unified-context-repository.md` · `plan/2026-07-08-stateful-agent-context.md` · `app/agent/context/*` · `app/services/context_repository.py`

> Latest completion review: `docs/reviews/completion-review-2026-07-19-unified-context-repository-2.md`.
> The code now satisfies the target runtime model; live rollout remains intentionally incomplete.

## Decision

| Mode | Switch | Role |
|---|---|---|
| **Dual-path (default)** | `HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false` | Existing Redis ContextState + independent API turn append |
| **Unified repository** | `=true` | Single `ContextRepository.load_envelope` + atomic turn/projection commit |
| **Structured render** | `HARNESS_STATEFUL_CONTEXT_ENABLED=true` | Whiteboard view renderer (under either storage mode) |
| **Legacy render** | `=false` | Summary/window renderer — rebuild / emergency only |

Under unified mode, `HARNESS_STATEFUL_CONTEXT_ENABLED` only selects the **render policy**. It must not open a second conversation/snapshot/Redis load path.

New harness features target the structured renderer. Legacy remains for emergency rollback, not feature parity.

## Unified repository (gated)

`HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false` (default):

- Load/persist behaviour unchanged from dual-path.
- Operator scripts and schema migration v3 may still be present; runtime does not use them until the flag is on.

When enabled (canary only after dry-run approval):

1. `ContextRepository.load_envelope(owner_key, session_id)` is the only load entry.
2. On `type=complete`, harness invokes the API-injected completion committer **before** the event is yielded.
3. One SQLite transaction inserts the immutable turn and (if `HARNESS_CONTEXT_DB_SNAPSHOT_ENABLED=true`) updates the compact projection + watermarks.
4. Mid-run tool/aux/re-evidence paths do **not** write durable SQLite snapshots; Redis inflight/committed cache is best-effort.
5. Durable projection JSON excludes recent-turn text, duplicate identity scope, and patch_tail.

Rollback:

```bash
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false
# optional emergency renderer fallback
HARNESS_STATEFUL_CONTEXT_ENABLED=false
```

## Rebuild-from-turns

`HARNESS_CONTEXT_REBUILD_FROM_TURNS_ENABLED=true` (default):

- Resume / cold start can rebuild the whiteboard from stamped `recent_turns`
- Does **not** mean “always prefer legacy builder”
- When stateful store is empty, rebuild seeds ContextState rather than inventing context

## Context deduplication rules

The stateful path keeps one authoritative copy of each high-volume input:

- The final `user` message is authoritative for the complete current question;
  the whiteboard stores intent for inspection, but omits an identical
  `current_question` by default.
- Attachment refs (`file_id`, filename, summary, keywords) remain in the
  whiteboard.  The default `HARNESS_ATTACHMENT_PROMPT_MODE=summary` sends the
  summary with the current question; `full` restores the previous behavior and
  `index` leaves details to `read_attachment` (and degrades to `summary` when
  stateful context tools are disabled).
- Whiteboard `recent_turns` stamps strip the composed attachment block and keep
  only the raw question plus a bounded assistant answer.  Conversation DB
  `user_context` and `attachment_refs` remain the durable attachment record.
- Tool summaries are preferred over an observed fact with the same `raw_ref`
  (or tool/status identity) in the rendered view.  Set
  `HARNESS_CONTEXT_VIEW_DEDUP_TOOL_EVIDENCE=false` for comparison/rollback.
- Preferences remain in the harness system prompt.  The default
  `HARNESS_PREFERENCE_INJECT_MODE=router_and_harness` also preserves the router
  copy; `harness_only` suppresses that router-side copy.

Rollout switches are independent.  The intent and evidence view behavior can
be reverted with `HARNESS_CONTEXT_INTENT_OMIT_FROM_VIEW_ENABLED=false`; the
attachment prompt composition can be reverted with
`HARNESS_ATTACHMENT_PROMPT_MODE=full` (bounded history stamps remain enabled).

## Operator runbook

```text
# Normal pilot / pre-prod (dual-path default)
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=false
HARNESS_STATEFUL_CONTEXT_ENABLED=true
HARNESS_CONTEXT_REBUILD_FROM_TURNS_ENABLED=true

# Canary unified repository (requires schema v3 + projection dry-run approval)
HARNESS_UNIFIED_CONTEXT_REPOSITORY_ENABLED=true
HARNESS_STATEFUL_CONTEXT_ENABLED=true

# Emergency renderer only — legacy ContextBuilder semantics
HARNESS_STATEFUL_CONTEXT_ENABLED=false
```

Read-only audit / dry-run (no writes):

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_context_storage.py \
  --db volumes/long_term_memory.db --read-only
PYTHONPATH=. .venv/bin/python scripts/migrate_context_projection.py \
  dry-run --db volumes/long_term_memory.db
PYTHONPATH=. .venv/bin/python scripts/migrate_database.py status
```

After flipping to legacy renderer, expect:

- No whiteboard patches / context tools richness
- Rolling summary behavior (token window)
- Possible multi-turn continuity differences vs structured

## Deprecation policy

1. **Do not delete** `ContextBuilder` / dual-path store code until unified is default and canaried.
2. Mark comments in `app/config.py` + `.env.example`.
3. Prefer tests that smoke **both** storage modes and both render policies.
4. Physical removal only after an explicit ADR with traffic/rollback evidence.
5. Do **not** auto-`VACUUM` SQLite; freelist reuse is enough for write amplification goals.

## Smoke checks

```bash
# Import / config defaults
python -c "from app.config import config; assert config.harness_stateful_context_enabled is True; assert config.harness_unified_context_repository_enabled is False"

# Envelope + both renderers + legacy builder importable
python -c "from app.agent.context.envelope import ContextEnvelope; from app.agent.context.renderers import StructuredRenderPolicy, LegacyRenderPolicy; from app.agent.harness.context import ContextBuilder; from app.services.context_repository import ContextRepository; print('ok')"
```

## Non-goals

- Changing the product default to unified without a separate approval
- Fabricating conversation turns for snapshot-only/ahead sessions
- Merging checkpoint recovery into permanent conversation rows
- Frontend “context mode” toggle for end users
