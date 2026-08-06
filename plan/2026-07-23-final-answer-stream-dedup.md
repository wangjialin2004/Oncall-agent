# Final Answer Stream Deduplication Plan

## Problem

When the harness has already streamed a draft/final answer and later enters
re-evidence or replan because verification reports gaps, it streams the
replacement answer as additional `content` events. The frontend appends every
`content` payload to the same assistant bubble. The user therefore sees two
near-identical answers in one response, even though the backend's final
`complete.answer` contains only the authoritative replacement.

The reported sample is consistent with this path: an initial response is
followed by another response headed as a new summary, with overlapping tool
and evidence descriptions.

## Decision and defaults

1. Preserve the existing SSE event types (`content`, `complete`, `agent_event`,
   `tool_event`, `decision_event`). No new public event type is introduced;
   `complete` gains only a backward-compatible optional
   `replace_streamed_answer` marker.
2. `complete.answer` is the canonical user-visible answer for a completed run.
   The frontend replaces the temporary streamed text with this value on
   completion instead of retaining an appended draft.
3. If a verified/replanned replacement is generated after visible content has
   already been streamed, the harness retains it for verification and the
   `complete` payload but does not emit it as a second sequence of `content`
   events.
4. Add `HARNESS_FINAL_ANSWER_REPLACEMENT_ENABLED=true` as a degradation switch.
   With `false`, the legacy append behavior remains available for rollback.
5. This is a correctness repair, not a change to tool authority, checkpoint
   replay, authentication, or production remediation behavior.

## Scope

- Track whether visible answer content has been emitted before re-evidence and
  post-replan close paths.
- Suppress only replacement `content` emission while retaining the model call,
  verification, state persistence, and final `complete` payload.
- Make completed frontend bubbles use `complete.answer` when provided.
- Add focused backend and frontend regressions for the replacement path.
- Record verification evidence in this plan on completion.

## Non-goals

- Do not alter model prompts, tool selection, verifier criteria, or evidence
  gathering.
- Do not deduplicate semantically similar prose within a single model response.
- Do not change conversation persistence schema or SSE event `type` values.
- Do not restart services automatically or alter Redis/Milvus configuration.

## Affected files

| Area | Files |
|---|---|
| Feature switch | `app/config.py`, `.env.example` |
| Harness close path | `app/agent/harness/close_path.py` |
| Stream UI | `frontend/src/App.tsx` |
| Backend regression | `tests/test_harness_service.py` or focused harness test module |
| Frontend regression | `frontend/src/App.test.tsx` or existing component/API test suite |
| Delivery record | This plan and `AGENTS.md` Current Plan Index |

## Flag and rollback

| Environment variable | Default | Effect |
|---|---:|---|
| `HARNESS_FINAL_ANSWER_REPLACEMENT_ENABLED` | `true` | Prevents a verified replacement answer from being appended as a second streamed body; completion replaces the provisional body. |

Rollback: set `HARNESS_FINAL_ANSWER_REPLACEMENT_ENABLED=false` and restart the
API. This restores legacy append behavior without changing stored turns or
event types.

## Implementation steps

1. Inspect all `content` emissions in the re-evidence and post-replan branches
   and centralize the "emit only if not replacing visible text" decision in the
   close-path mixin.
2. Keep generated replacement text in `final_answer`, run the existing verifier
   and corrective notice logic, and persist it unchanged.
3. On `complete`, make the frontend choose the canonical `event.answer` only
   when the server marks it as a replacement; retain streamed content for
   older servers and when rollback is enabled.
4. Add a backend test asserting a prior streamed answer plus a replacement
   produces one visible answer sequence under the default flag and retains the
   authoritative `complete.answer`.
5. Add a frontend test asserting an initial content event followed by a
   complete event renders only the complete answer.

## Verification

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' \
  tests/test_harness_service.py -k 're_evidence or replan or final_answer or stream' \
  -q --no-cov

npm --prefix frontend test -- --run

PYTHONPATH=. .venv/bin/python -m compileall -q app
PYTHONPATH=. .venv/bin/ruff check --select E9,F63,F7,F82 app tests
git diff --check
```

Soft-path regression: run the existing harness stream test that emits an
initial final answer, forces verification gaps, and completes after a
replacement; assert no duplicate rendered body and unchanged event types.

## Exit criteria

- A verified/replanned response never leaves two complete answers appended in
  one assistant bubble.
- `complete.answer` persists as the exact final answer.
- Existing non-replacement streaming behavior remains intact.
- Focused backend and frontend tests pass, plus one existing harness soft-path
  regression.
- The flag provides a documented, tested rollback path.

## Risks

- Replacing the visible draft at completion can cause a short UI text change.
  The agent process panel continues to show verification progress, making that
  transition understandable.
- An older server that does not set `complete.answer` must retain the streamed
  text; frontend fallback covers that case.
- The close path has multiple final-answer branches. Tests must cover both
  re-evidence and replan to prevent a branch-specific regression.

## External research

No dependency or external implementation is adopted. This repair modifies the
project's existing streaming contract internally while preserving the public
event types, so external package/license/security review is not applicable.

## Progress / deviations

- 2026-07-23: Plan created after tracing the frontend event reducer and harness
  close path.
- 2026-07-23: Implemented the default-on repair. Re-evidence and post-replan
  answers are retained for verification and persistence, but their `content`
  chunks are suppressed when an earlier answer is already visible. The final
  `complete` event carries optional `replace_streamed_answer=true` only when
  that replacement was actually suppressed; the frontend then replaces the
  provisional bubble. With
  `HARNESS_FINAL_ANSWER_REPLACEMENT_ENABLED=false`, no marker is emitted and
  legacy append behaviour is retained.
- 2026-07-23: Added backend regression coverage for re-evidence and replan,
  frontend reducer/translation coverage for replacement and rollback, and
  restored an existing stream test's explicit no-re-evidence fixture so its
  one-model-call contract remains isolated from the default close loop.
- 2026-07-23: Verification passed:

  ```text
  PYTHONPATH=. .venv/bin/pytest -o addopts='' \
    tests/test_m1_close_the_loop.py tests/test_m1_w3_replan_latency.py \
    -q --no-cov                                      13 passed
  PYTHONPATH=. .venv/bin/pytest -o addopts='' \
    tests/test_harness_service.py \
    -k 're_evidence or replan or final_answer or stream' \
    -q --no-cov                                      10 passed
  npm --prefix frontend test -- --run                 86 passed
  npm --prefix frontend run build                     passed
  compileall, ruff E9/F63/F7/F82, git diff --check    passed
  ```

- 2026-07-23: User-requested local runtime recovery completed. Started the
  existing `super-biz-redis` container on host port `6380` and restarted the
  FastAPI tmux pane so it loads the repair. `redis-cli ping` returned `PONG`;
  `/health`, `/health/live`, and `/health/readiness` all returned `200`; the
  frontend returned `200`, and health reports both MCP services reachable.
