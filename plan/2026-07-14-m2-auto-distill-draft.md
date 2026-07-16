# M2 WP-D1 Draft: Semi-automatic experience distillation

> **Date**: 2026-07-14  
> **Status**: draft only — **no production code in W7**  
> **Parent**: [W7 plan](./2026-07-14-m2-w7-shared-kernel-change-decision.md) · roadmap WP-D1

---

## Goal

After a successful diagnosis run, optionally create a **draft** long-term experience
record for human confirm (or auto-ingest when explicitly enabled).

## Trigger (proposed)

```text
complete
  AND verification.confidence in {medium, high}
  AND at least one successful non-context investigation tool
  AND not harness_degraded_fallback answer
  AND route not in {clarify}
```

## Switches (proposed defaults)

```text
LONG_TERM_MEMORY_AUTO_DISTILL=false
LONG_TERM_MEMORY_AUTO_DISTILL_MIN_CONFIDENCE=medium
LONG_TERM_MEMORY_DISTILL_REQUIRE_CONFIRM=true
```

## Flow

1. Harness complete → build draft JSON (service, symptoms, tools, summary, gaps).
2. If confirm required → store `status=pending` + optional API/UI「采纳经验」.
3. If `AUTO_DISTILL=true` and confirm off → write via `experience_memory_service` with PII scrub.
4. Never distill pure knowledge how-to without tool evidence (avoid doc regurgitation).

## Non-goals (W7/W8 early)

- No silent production writes with default false.
- No training-data export off-box.
- No auto-distill of failed/timeout runs (those go to WP-D2 anti-patterns later).

## Implementation sketch (W8+)

| File | Role |
|---|---|
| `app/services/experience_memory_service.py` | existing store |
| `app/agent/harness/loop.py` | complete hook |
| `app/api/memory.py` | confirm endpoint |
| frontend | optional accept button |

## Acceptance when implemented

- Manual confirm path e2e 1 case
- Auto path e2e 1 case with flag true
- PII policy regression green
- Default false → zero writes
