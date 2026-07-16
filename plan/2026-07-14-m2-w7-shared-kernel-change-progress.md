# M2 W7 Progress: Shared Kernel · Change Policy B · Merge Dedup

Date: 2026-07-14

## Implemented

- **WP-B2 Shared kernel**: `app/agent/harness/sub_harness.py` owns the multi-round
  model→tools loop. `ToolCallingExpert.run` delegates to `run_sub_harness`
  (lazy import to avoid circular deps). Events carry `payload.shared_kernel`.
- **WP-C4 option B**: formal permanent demotion of change source until a future
  epic. `CHANGE_SOURCE_AVAILABLE=False`, `CHANGE_SOURCE_POLICY=unavailable`,
  updated [docs/pilot/change-capability-unavailable.md](../docs/pilot/change-capability-unavailable.md).
- **WP-B4 light**: `merge_delegate_results` de-dupes experts/tools; used by
  `run_parallel_delegates`.
- **WP-D1 draft only**: [plan/2026-07-14-m2-auto-distill-draft.md](./2026-07-14-m2-auto-distill-draft.md).

New config:

```text
HARNESS_SHARED_KERNEL_DELEGATION=true
CHANGE_SOURCE_POLICY=unavailable
```

## Verification

```text
pytest subset including W7:
  tests/test_m1_*
  tests/test_m2_latency_eval_hardening.py
  tests/test_m2_w6_parallel_delegation.py
  tests/test_m2_w7_shared_kernel.py
  tests/test_harness_verifier.py
  tests/test_harness_checkpoint.py
  tests/test_context_integration.py

Result: 77 passed (W7 file 5 tests)
```

W7 unit coverage:

- sub_harness close_after_tools skips second model call
- ToolCallingExpert emits shared_kernel complete payload
- merge_delegate_results expert/tool dedup
- change option B gap payload + invalid policy fallback

## Live

Not re-run this slice. L1.5 remains Conditional Go.

## Next (W8)

- Full 23-case eval baseline when MCP+LLM healthy
- Optional observed_facts write for merged parallel evidence
- L2 exit review draft
- Auto-distill only if product prioritizes WP-D1 over latency
