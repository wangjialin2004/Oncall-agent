# Context Dual Path (H4) — Stateful primary, legacy rebuild-only

> **Date**: 2026-07-15 · **M3 W11 / WP-H4**  
> **Status**: documented convergence — **no physical delete** of ContextBuilder  
> **Related**: `plan/2026-07-08-stateful-agent-context.md` · `app/agent/context/*`

## Decision

| Path | Switch | Role |
|---|---|---|
| **Primary** | `HARNESS_STATEFUL_CONTEXT_ENABLED=true` (default) | `ContextState` whiteboard (Redis + DB snapshot) |
| **Legacy** | `=false` | `ContextBuilder` rolling summary / token window — **rebuild / fallback only** |

New harness features (re-evidence, parallel, distill hooks, etc.) target the **stateful** path. Legacy remains for emergency rollback and cold rebuild semantics, not feature parity.

## Rebuild-from-turns

`HARNESS_CONTEXT_REBUILD_FROM_TURNS_ENABLED=true` (default):

- Resume / cold start can rebuild the whiteboard from stamped `recent_turns`
- Does **not** mean “always prefer legacy builder”
- When stateful store is empty, rebuild seeds ContextState rather than inventing context

## Operator runbook

```text
# Normal pilot / pre-prod
HARNESS_STATEFUL_CONTEXT_ENABLED=true
HARNESS_CONTEXT_REBUILD_FROM_TURNS_ENABLED=true

# Emergency only — legacy ContextBuilder
HARNESS_STATEFUL_CONTEXT_ENABLED=false
```

After flipping to legacy, expect:

- No whiteboard patches / context tools richness
- Rolling summary behavior (token window)
- Possible multi-turn continuity differences vs stateful

## Deprecation policy

1. **Do not delete** `ContextBuilder` / RouterService in W11–W12.
2. Mark comments in `app/config.py` + `.env.example` (done in W11).
3. Prefer tests that smoke **both** import paths; product default stays stateful.
4. Physical removal only after an explicit L3+ ADR with dual-path traffic data.

## Smoke checks

```bash
# Import / config defaults
python -c "from app.config import config; assert config.harness_stateful_context_enabled is True"

# Primary whiteboard + legacy builder both importable
python -c "from app.agent.context.state import AgentContextState; from app.agent.harness.context import ContextBuilder; print('ok', AgentContextState, ContextBuilder)"
```

## Non-goals

- Migrating all history out of legacy in W11
- Dual-write forever as a product feature
- Frontend “context mode” toggle for end users
