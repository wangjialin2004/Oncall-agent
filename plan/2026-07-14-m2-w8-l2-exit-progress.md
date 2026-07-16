# M2 W8 Progress: Merge · Full Suite · L2 Exit

Date: 2026-07-14

## Implemented

- **WP-B4b**: aux path uses `merge_delegate_results`; `record_delegate_merge` writes
  framework-only tool summary + observed_fact on the whiteboard.
- **WP-F3b**: `scripts/evaluate_oncall_local.py` adds ordered `FULL_IDS` and
  `--suite full` (alias of all with stable order). README weekly baseline updated.
- **WP-EXIT**: [docs/pilot/l2-exit-review-2026-07-14.md](../docs/pilot/l2-exit-review-2026-07-14.md)
  — **Conditional Go (L2)**；full live 已回填。
- Unit: `tests/test_m2_w8_merge_eval.py` (merge→whiteboard, full suite 23 order).

## Verification (unit)

```bash
python -m pytest \
  tests/test_m2_w8_merge_eval.py \
  tests/test_m2_w7_shared_kernel.py \
  tests/test_m2_w6_parallel_delegation.py \
  tests/test_m2_latency_eval_hardening.py \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_harness_verifier.py \
  tests/test_harness_checkpoint.py \
  tests/test_context_integration.py \
  -q --no-cov
```

Expected: green (≈80 tests). Re-run if sandbox blocked mid-session.

## Live

`/health` healthy (milvus + mcp cls/monitor reachable).

### W8 full — `oncall_full_20260714_182406`（**出口主证据**）

| Metric | Value |
|---|---|
| pass/total | **20/23** |
| Core | **4/5**（S5 fallback） |
| P50 / P95 | **64.03s / 143.48s** |
| complete_rate | 1.0 |
| re_evidence / replan rate | 0.52 / 0.43 |
| must_pass N1/N3/M1 | all true |
| Failures | S5 `harness_degraded_fallback`；RE2 `required_replan_not_triggered`；P1 `required_parallel_event_not_triggered` |

Window: 18:24–18:56（~32 min）。Log: `evals/results/oncall_full_run_20260714_182405.log`。

### W8 minimal — `oncall_minimal_20260714_174155`（波动对照）

| Metric | Value |
|---|---|
| pass/total | **7/10** |
| Core | **3/5** (S1 fallback, S2 timeout) |
| P50 / P95 | **130.9s / 210.1s** |
| complete_rate | 1.0 |
| re_evidence / replan rate | 0.4 / 0.4 |
| must_pass N1/N3/M1 | all true |
| Failures | S1 `harness_degraded_fallback`; S2/N6 `client_timeout` |

Vs L1.5 `221643` (9/10, P50 110s): minimal sample **regressed**；full later **recovered** to 20/23 / P50 64s.
Keep **L2 Conditional Go** — full latency gate met, but Core/P1/RE2 and stability not unconditional.

## Assessment

| Gate | Status |
|---|---|
| Parallel delegation | Code+unit Go；live P1 **not triggered** → Conditional |
| Shared kernel | Go |
| Full cases 23 | **20/23** live；Core 4/5 |
| P50 ≤85s | **Met on full** (**64.0s**); minimal sample 130.9s |
| Change option B | Go |

→ **L2 Conditional Go**（full 基线 + 协作能力）+ **Core/并行事件/稳态 → M3 backlog**。

## Next (M3)

See L2 exit §5 backlog（S5 长尾、P1/RE2 触发、distill、HITL、platform）。
