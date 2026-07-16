# Completion Review Report: 连续 8 题 + Checkpoint 演练

- Review date: 2026-07-12
- Review scope: 严格连续最小 8 题、N3 恢复重试、checkpoint kill/resume、Go/No-Go 更新
- Source material: `docs/pilot/go-nogo-20260712.md`、`evals/results/oncall_minimal_20260712_160315*.json`、`logs/checkpoint_resume_drill_*`
- Verification commands:
  - 连续：`python -u scripts/evaluate_oncall_local.py --timeout-extra 90 --inter-case-sleep 10`
  - N3 重试：`--case N3-no-remediation`
  - Checkpoint：`python -u scripts/checkpoint_resume_drill.py`

## Overall Conclusion

**Conditional Go 再升级，仍非正式 L1 Go。**

- 连续单进程：S1–S5 **5/5**、N1/M1 过、complete 100%、P95 149s；**N3 因 LLM 503 失败** → 严格 7/8。  
- N3 恢复后 19.6s 通过 → 运维口径 8/8。  
- Checkpoint：杀 backend 后可重启并同 session `complete`；步级 `resumable=false`（杀早）。  
- 阻塞正式 Go：严格连续无 503、P50≤90、真人 OnCall。

## Findings

### P0 - 严格连续仍被上游 LLM 503 打断

- Evidence: N3 `llm_provider_degraded`，preview 含 system cpu overloaded 92.1%。
- Impact: 不能签「稳态质量门 100%」。
- Recommendation: 值班前 LLM 探活；N3 类短题失败自动重试 1 次；考虑备用端点。
- Verification: 连续 8 题一次 8/8 且无 degraded 标记。

### P1 - P50 109s 超过 90s 线

- Evidence: 160315 p50=109.34；S2=149.6；M1=158.1。
- Impact: 体验与 SLA 不达标（P95 仍 <180）。
- Recommendation: 减 tool 扇出 / max_steps；M1 第二轮限工具。
- Verification: 连续跑 p50≤90。

### P1 - Checkpoint 步级 resume 未在 tool 后验证

- Evidence: kill 时仅 route/agent；API `resumable=false`；Redis 仅 context key。
- Impact: 只能证明「崩溃后可再答完」，不能证明「从 step N 续跑」。
- Recommendation: 等到 `tool_event` 后再 kill。
- Verification: restart 后 `resumable=true` 且 step>0。

### P2 - OnCall 真人未指定

- Evidence: D5 空；go-nogo 签字栏空。
- Impact: 运维门禁签字不全。
- Recommendation: 指定姓名与升级路径。

## Completion Matrix

| Item | Status | Notes |
| --- | --- | --- |
| 连续 S1–S5 ≥4/5 | Complete | 5/5 |
| 连续 N1/M1 | Complete | 过 |
| 连续 N3 | Partial | 503 后重试过 |
| complete 100% | Complete | 连续与重试均 complete |
| P50≤90 | Missing | 109s |
| Checkpoint 杀进程可恢复 | Complete | complete 收口 |
| Step-level resumable | Partial | 未在 tool 后验证 |
| 真人 OnCall | Missing | |
| 正式 L1 Go | Missing | Conditional |

## Test And Verification Notes

- Continuous log: `logs/eval_continuous_20260712.log`
- Drill result: `logs/checkpoint_resume_drill_result.json` passed=true
- Backend post-drill: healthy

## Next Steps

1. 低负载再跑严格连续 8 题冲 8/8。  
2. 指定 OnCall 联系人并签字。  
3. tool 步后 checkpoint 深演练。  
4. 压 P50（S2/M1）。  
5. 达标后改决策为正式 Go。
