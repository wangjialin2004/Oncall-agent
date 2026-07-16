# M2 W5 Latency and Evaluation Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the long-tail latency of the read-only Harness without hiding failures, and make real re-evidence/replan activity a required, correctly reported part of the minimal evaluation.

**Architecture:** Apply latency controls at the repeated model-work boundaries: diagnosis step budget, delegated expert tool rounds, and delegated evidence-only return. The existing route-timeout profile remains the feature gate; disabling it restores the former Harness step budget, while setting the delegated-round cap to `3` restores the historical delegate behavior. The evaluator becomes the source of truth for verification and trigger evidence: it reads event-level verification status, rejects known Harness fallback responses, makes RE1 require re-evidence, and uses controlled RE2 failure to require replan.

**Tech Stack:** Python 3.12+, FastAPI/SSE Harness, asyncio, Pydantic settings, pytest, JSONL on-call evaluation.

---

## Context and non-goals

- Baseline: `oncall_minimal_20260713_221643.json` is 9/10, P50 110.42s, P95 210.07s. S1/S2/S4 reach the client budget; S2/S4 return `# 降级响应` but historically scored as passing.
- RE1 is the normal low-confidence re-evidence case. RE2 is the controlled primary-tool-failure replan case; a successful knowledge retrieval must not be forced to replan.
- The system remains read-only. This plan does not introduce parallel expert fan-out, writing tools, automatic remediation, or a real change source.

## Files

| File | Responsibility |
|---|---|
| `app/config.py` | Add documented, rollback-safe latency budgets for diagnosis steps and delegated-expert tool rounds. |
| `.env.example` | Document the new controls and the legacy values that disable their optimizations. |
| `app/agent/harness/loop.py` | Apply the diagnosis step cap only while `HARNESS_ROUTE_TIMEOUT_PROFILE` is enabled. |
| `app/agent/harness/subagent.py` | Pass the configured tool-round cap into a serial delegated expert. |
| `app/agent/experts/base.py` | Honor a caller-supplied delegated tool-round cap without mutating singleton expert state. |
| `scripts/evaluate_oncall_local.py` | Parse `agent_event.status`, classify known Harness fallback answers, and fail required trigger cases that do not trigger. |
| `evals/oncall/cases.jsonl` | Make RE1 require re-evidence and add controlled RE2 replan coverage. |
| `tests/test_m2_latency_eval_hardening.py` | Cover the diagnosis cap, delegated-round forwarding, event parsing, fallback rejection, and trigger requirements. |
| `plan/2026-07-13-m2-w5-latency-eval-hardening-progress.md` | Record actual test and live-evaluation evidence after implementation. |
| `docs/pilot/handoff-2026-07-14-l15-conditional.md` | Update only after a fresh live minimal result exists. |

## Configuration contract

```text
# Existing master switch. false restores the legacy Harness route budgets.
HARNESS_ROUTE_TIMEOUT_PROFILE=true

# Applies only while the profile is enabled. Set 5 to match this pilot's current .env.
HARNESS_DIAGNOSIS_MAX_STEPS=3

# Applies only to delegate_to_expert. Set 3 to restore ToolCallingExpert's legacy cap.
HARNESS_DELEGATE_MAX_TOOL_ROUNDS=1
```

The profile must never shorten knowledge below its existing cap of 2. A cap is a work-budget reduction, not an artificial success condition: a timeout, fallback, or missing evidence still remains visible and is rejected by evaluation scoring.

## Addendum — diagnosis evidence early close (2026-07-14)

The first full minimal attempt after the original step and delegate caps did not
finish within the 15-minute runner ceiling. The server timeline shows the
cause: diagnosis cases successfully collect several read-only tool results in
their first turn, then spend additional planner turns until the outer 180s
timeout and 30s fallback. This addendum adds a narrower, rollback-safe close:

- When the existing route-timeout profile is on, any investigation route with
  at least one successful non-context tool result closes directly to the final
  answer after that tool turn. This also covers a `knowledge` focus whose
  evidence came from a delegated expert rather than the direct retrieval tool.
- This is only a work-budget cut. The usual verification, re-evidence, replan,
  and gap reporting remain in effect. A failed-only RE2 run therefore cannot
  early-close and continues into its real replan path.
- `HARNESS_INVESTIGATION_EVIDENCE_EARLY_CLOSE=false` restores the prior behavior.

Regression coverage must assert enabled/on closes only after successful tool
evidence, while profile-off preserves the prior loop behavior.

## Addendum — one delegated tool round (2026-07-14)

The fresh S1 server timeline confirms that MCP tools complete in roughly 20
seconds, but the delegated expert starts a second model/tool round about a
minute later and exhausts the parent Harness's 180-second budget. The default
delegated tool-round budget is therefore reduced from two to one. The expert
still performs one model-directed tool batch, and the parent retains final
answer verification, re-evidence, and real replan. Set
`HARNESS_DELEGATE_MAX_TOOL_ROUNDS=2` (or the historical value `3`) to roll
back the work budget.

## Addendum — delegate evidence-only return (2026-07-14)

With a one-round delegated expert, the expert would otherwise issue a second,
no-tool model call merely to summarize the evidence before the parent Harness
issues its own final-answer call. For delegated use only, stop after the first
successful tool batch and return its normalized child tool events to the
parent, which already performs synthesis and verification. The default is
enabled behind `HARNESS_DELEGATE_EVIDENCE_ONLY`; setting it to `false` restores
the expert's standalone final-summary call. Direct expert usage retains its
current default (`close_after_tools=False`).

## Addendum — concrete incident route override (2026-07-14)

The fresh minimal result shows that S4's 210-second tail was not caused by
tool latency: semantic routing selected `knowledge` for a request containing a
concrete service target plus service-unavailable, failed health checks, and a
high error rate. Add a narrow, downgrade-safe post-semantic override:

- If semantic routing chooses `knowledge`, but the request has both a concrete
  operational target and an incident signal, use `diagnosis` instead.
- Generic how-to and concept questions remain `knowledge` because they lack a
  concrete target/incident pair.
- `ROUTER_CONCRETE_INCIDENT_OVERRIDE_ENABLED=false` restores the semantic
  result unchanged.

## Addendum — preserve primary route during fallback scoring (2026-07-14)

The evaluator currently overwrites the initial `route_event` with the route of
a later fallback completion. That misattributes a diagnosis failure to
`knowledge` and can invalidate route assertions. Keep the first route event as
`route`/`primary_route`, record the completion route separately as
`final_route`, and retain the latter only for diagnostics.

### Task 1: Add evaluator regression tests first

**Files:**
- Create: `tests/test_m2_latency_eval_hardening.py`
- Test: `tests/test_m2_latency_eval_hardening.py`

- [x] **Step 1: Write failing tests for event-level verification and fallback rejection**

```python
from scripts.evaluate_oncall_local import score_case, summarize


def test_summarize_reads_verify_status_from_agent_event_status():
    summary = summarize([{
        "type": "agent_event", "stage": "verify", "status": "degraded",
        "payload": {"gaps": ["缺少指标"]},
    }])
    assert summary["verify_status"] == "degraded"
    assert summary["gaps"] == ["缺少指标"]


def test_degraded_harness_fallback_cannot_pass_a_diagnostic_case():
    score = score_case(
        {"expected": {"require_evidence": True}, "scoring": {"pass_score": 7}},
        {"answer": "# 降级响应\\nHarness main loop and knowledge fallback执行超过 30 秒", "tools": [],
         "tool_success_count": 1, "has_complete": True, "re_evidence_rounds": 0,
         "replan_times": 0, "gaps": []},
        latency=10,
        err=None,
    )
    assert score["passed"] is False
    assert score["error"] == "harness_degraded_fallback"
```

- [x] **Step 2: Write failing tests for required real trigger evidence**

```python
def test_required_re_evidence_and_replan_must_both_be_observed():
    case = {"expected": {"require_re_evidence": True, "require_replan": True},
            "scoring": {"pass_score": 6}}
    absent = score_case(case, _complete_summary(re_evidence_rounds=0, replan_times=0), 1, None)
    present = score_case(case, _complete_summary(re_evidence_rounds=1, replan_times=1), 1, None)
    assert absent["passed"] is False
    assert absent["error"] == "required_re_evidence_not_triggered"
    assert present["passed"] is True
```

- [x] **Step 3: Run the test file and confirm it fails before implementation**

Run: `python -m pytest tests/test_m2_latency_eval_hardening.py -q --no-cov`

Expected: FAIL because the evaluator currently reads `payload.status`, accepts fallback text, and ignores required trigger keys.

### Task 2: Correct evaluation reporting and make RE1 a trigger gate

**Files:**
- Modify: `scripts/evaluate_oncall_local.py:123-269`
- Modify: `evals/oncall/cases.jsonl:RE1-re-evidence-gap`
- Test: `tests/test_m2_latency_eval_hardening.py`

- [x] **Step 1: Read verification status from the event envelope**

```python
if stage in {"verify", "verification"}:
    verify_status = str(ev.get("status") or verify_status)
    payload = ev.get("payload") or {}
    # retain the existing gaps collection
```

- [x] **Step 2: Add an explicit degraded-fallback classifier**

```python
_HARNESS_DEGRADED_FALLBACK_MARKERS = (
    "# 降级响应",
    "harness main loop and knowledge fallback",
)


def _is_harness_degraded_fallback(answer: str) -> bool:
    normalized = (answer or "").lower()
    return any(marker.lower() in normalized for marker in _HARNESS_DEGRADED_FALLBACK_MARKERS)
```

When the classifier returns true, set `err = "harness_degraded_fallback"` before computing `passed`; preserve the row and its latency for diagnosis.

- [x] **Step 3: Enforce required trigger fields in `score_case`**

```python
if expected.get("require_re_evidence") and int(summary.get("re_evidence_rounds") or 0) < 1:
    err = err or "required_re_evidence_not_triggered"
if expected.get("require_replan") and int(summary.get("replan_times") or 0) < 1:
    err = err or "required_replan_not_triggered"
```

Add `"require_re_evidence": true` to RE1's `expected` object. Add `"require_replan": true` to controlled RE2. Do not require these events for normal alert cases.

- [x] **Step 4: Run the evaluator regression tests**

Run: `python -m pytest tests/test_m2_latency_eval_hardening.py -q --no-cov`

Expected: PASS.

### Task 3: Limit repeated diagnosis and delegated-expert work

**Files:**
- Modify: `app/config.py:83-131`
- Modify: `.env.example:Harness configuration section`
- Modify: `app/agent/harness/loop.py:1371-1381`
- Modify: `app/agent/harness/subagent.py:13-82`
- Modify: `app/agent/experts/base.py:80-190`
- Test: `tests/test_m2_latency_eval_hardening.py`

- [x] **Step 1: Add failing tests for the two latency caps**

```python
def test_route_timeout_profile_caps_diagnosis_but_off_restores_budget(monkeypatch):
    monkeypatch.setattr(config, "harness_route_timeout_profile", True)
    monkeypatch.setattr(config, "harness_diagnosis_max_steps", 3)
    service = HarnessService(limits=HarnessLimits(max_steps=5, token_budget=1000, timeout_seconds=30))
    assert service._effective_max_steps("diagnosis") == 3
    monkeypatch.setattr(config, "harness_route_timeout_profile", False)
    assert service._effective_max_steps("diagnosis") == 5


@pytest.mark.asyncio
async def test_delegate_forwards_configured_round_cap_to_expert():
    expert = RecordingExpert()
    tool = create_delegate_tool(..., expert_getter=lambda _: expert)
    await tool.handler({"expert": "metric", "subtask": "probe"})
    assert expert.received_max_tool_rounds == 2
```

- [x] **Step 2: Add the config settings and documented rollback values**

```python
harness_diagnosis_max_steps: int = 3
harness_delegate_max_tool_rounds: int = 1
```

The `.env.example` comments must state `HARNESS_DIAGNOSIS_MAX_STEPS=5` and `HARNESS_DELEGATE_MAX_TOOL_ROUNDS=3` as legacy-compatible rollback values.

- [x] **Step 3: Extend only the existing route profile for diagnosis**

```python
if route_name == "diagnosis" and bool(getattr(config, "harness_route_timeout_profile", True)):
    cap = max(1, int(getattr(config, "harness_diagnosis_max_steps", 3) or 3))
    return max(1, min(base, cap))
```

Keep `metric`, `log`, and `change` at the caller's regular budget. Keep knowledge/clarify at their existing profile cap.

- [x] **Step 4: Pass a per-invocation cap to the delegated expert**

```python
# subagent.py
round_cap = max(1, int(getattr(config, "harness_delegate_max_tool_rounds", 1) or 1))
generator = expert.run(..., max_tool_rounds=round_cap)

# experts/base.py
async def run(..., max_tool_rounds: int | None = None):
    round_limit = max(1, int(max_tool_rounds or self.max_tool_rounds))
    for _round_index in range(round_limit):
        ...
```

Do not mutate `self.max_tool_rounds`: experts are registry singletons and parallel callers must not alter each other's budgets.

- [x] **Step 5: Run the latency-cap tests and existing M1 latency tests**

Run: `python -m pytest tests/test_m2_latency_eval_hardening.py tests/test_m1_w3_replan_latency.py tests/test_m1_w4_latency_exit.py -q --no-cov`

Expected: PASS; the profile-off assertions retain legacy budgets.

### Task 4: Verify behavior against the running pilot and record evidence

**Files:**
- Create: `plan/2026-07-13-m2-w5-latency-eval-hardening-progress.md`
- Modify: `docs/pilot/handoff-2026-07-14-l15-conditional.md` only after the fresh result exists

- [x] **Step 1: Run the full M1/M2 regression subset**

Run:

```bash
python -m pytest \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_m2_latency_eval_hardening.py \
  tests/test_harness_verifier.py \
  tests/test_harness_checkpoint.py \
  tests/test_context_integration.py \
  tests/test_harness_observability.py \
  -q --no-cov
```

Expected: zero failures.

- [x] **Step 2: Check the pilot health before running live evaluation**

Run: `Invoke-WebRequest http://127.0.0.1:9900/health -UseBasicParsing`

Expected: HTTP 200. If unavailable, record the live run as blocked rather than inventing performance results.

- [x] **Step 3: Prove normal re-evidence and controlled replan**

Run: `python scripts/evaluate_oncall_local.py --case RE1-re-evidence-gap --timeout-extra 90`

Observed: RE1 passed with `re_evidence_rounds=1`, `replan_times=0`, and non-empty `verify_status`; controlled RE2 passed with both values `>= 1`.

- [x] **Step 4: Run the minimal suite and compare to W4**

Run: `python scripts/evaluate_oncall_local.py --suite minimal --timeout-extra 90`

Observed: P50 109.69s and P95 186.29s improved against the W4 baseline. The strict run was 6/10 while MCP services were unavailable, so it is not an exit pass; fallback scoring rejected the affected row as intended.

- [x] **Step 5: Write the progress record from actual outputs only**

Include: file names, pass/total, Core results, P50/P95, completion rate, trigger rates, whether RE1's event contract passed, and any remaining timeout rows. Update the Conditional handoff only with values observed in that fresh result.

## Self-review

- Scope coverage: latency controls (Task 3), true re-evidence/replan evidence (Task 2 and Task 4), event parsing/scoring repair (Task 2), regression tests (Tasks 1 and 3), and documented results (Task 4) each have an explicit task.
- Placeholder scan: no unassigned implementation or verification step remains.
- Type consistency: `max_tool_rounds` is optional in `ToolCallingExpert.run`, is passed by `create_delegate_tool`, and is tested through a recording expert; existing direct calls retain the default behavior.

## Change record — structured tool failures (2026-07-13)

Live RE1 correctly emitted `re_evidence`, but replan was absent because knowledge retrieval satisfied the selected route. S2/S4/RE2 also showed that a tool may return a structured provider error while the shared executor labels the call `completed`. This prevents both truthful evidence verification and rule replan. Add a narrow shared-kernel correction before the controlled replan drill:

- `app/agent/agent_loop.py`: treat mapping or JSON-string payloads with `success=false` or `status in {error, failed, timeout, timed_out}` as failed `ToolExecutionResult`s; preserve content, raw payload, latency, and existing retry behavior.
- `tests/test_m2_latency_eval_hardening.py`: assert a runtime tool returning `{"status": "error"}` becomes a failed result.
- Live drill: temporarily point only the local test backend's Prometheus base URL at `127.0.0.1:1`, disable delegation, restart the local backend, and run a metric-only RE2 scenario. Restore the original `.env` values and restart the backend immediately after the result is saved.

This is not a test-only fault injector and does not expose a new API; it corrects the meaning of existing provider error payloads.
