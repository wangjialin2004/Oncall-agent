# Completion Review Report: L1 下午推进（告警/凭据/重评/回滚）

- Review date: 2026-07-12
- Review scope: Conditional Go 升级路径 — LLM/超时、Prometheus firing、AUTH 硬化、最小 8 题重评、L4 回滚
- Source material: `docs/pilot/go-nogo-20260712.md`、任务板、`.env`、`deploy/prometheus/alerts.yml`、`scripts/evaluate_oncall_local.py`
- Verification commands:
  - Docker Desktop 重启后拉起 Milvus/Prometheus；Redis requirepass + ping
  - `/health` healthy；3 firing alerts；强密码登录 200 / 旧密码拒绝
  - 最小 8 题规则分（合并最佳）8/8；S1–S5 5/5；N1/N3/M1 全过
  - L4 `HARNESS_MCP_ENABLED` 关开重启 healthy

## Overall Conclusion

**结论：Conditional Go（升级版）。** 上午 No-Go/弱 Conditional 的五大阻塞项中，凭据、firing 告警、ERROR 日志、质量表数字、L4 回滚均已推进；**仍不能签正式 L1 Go**，因为（1）8 题非单次连续稳定跑完，中途依赖 LLM 503 恢复重试；（2）S4 时延 210s 偏高；（3）OnCall 真人未指定；（4）checkpoint resume 未演练。

## Findings

### P0 - 上游 LLM 间歇 503 仍决定质量门真伪

- Evidence: 连续 8 题中途 `system cpu overloaded`；S4/S5 首次 `llm_provider_degraded`；恢复后重试通过。
- Impact: 合并 8/8 不能等价于「稳态值班可用」。
- Recommendation: 限流、case 间隔、换备用端点、或本地小模型兜底；值班窗口前做 LLM 探活。
- Verification: 连续 8 题一次 exit 0 且无 `llm_provider_degraded`。

### P1 - S4 时延 210s 超建议 150s

- Evidence: `oncall_minimal_20260712_155149.json` latency_s=210.08，has_complete=true。
- Impact: P95 与体验贴边。
- Recommendation: 收紧 max_steps/工具扇出；diagnosis 路径避免过度 knowledge 降级重试。
- Verification: S4 连续 3 次 <150s complete。

### P1 - 路由漂移（S2/S4 → knowledge）

- Evidence: 合并结果 route 字段。
- Impact: 证据工具可能变少，靠规则宽松通过。
- Recommendation: 强化 metric/diagnosis 关键词与 force-delegation 试点对比。
- Verification: S1–S5 expect_route 命中率提升。

### P2 - OnCall 联系人与 resume 演练未闭环

- Evidence: 任务板 D5；checkpoint kill/resume 未做。
- Impact: 运维门禁签字不全。
- Recommendation: 指定值班人；做一次杀 backend + checkpoint resume。
- Verification: 文档签字 + resume 日志。

## Completion Matrix

| Item | Status | Notes |
| --- | --- | --- |
| 工程 pytest/frontend | Complete | 258 / 52（上午） |
| 安全账号/TTL/CORS/写鉴权 | Complete | 强密码已换 |
| Prometheus firing | Complete | 3 seed alerts |
| ERROR 日志可查 | Complete | pilot log 文件 |
| 最小 8 题质量线 | Partial→Complete* | *合并最佳 8/8；非单次连续 |
| L4 回滚 | Complete | MCP off/on |
| L1–L6 全量回滚 | Partial | 仅 L4 实操 |
| OnCall 联系人 | Missing | D5 |
| 正式 L1 Go | Missing | 建议仍 Conditional |

## Test And Verification Notes

- LLM 探活：200 与 503 交替；恢复后可评测。
- 合并汇总：`evals/results/oncall_minimal_20260712_merged_best.json` → passed=8 core=5 complete=1.0 p50=89.19 p95=150。
- 旧 `admin:admin` 已拒绝。

## Next Steps

1. 低负载时段 **连续** 再跑 8 题一次出分。  
2. 指定 OnCall 联系人并更新 go-nogo 签字。  
3. 校准 S2/S4 路由 + 压 S4 时延。  
4. Checkpoint kill/resume 演练。  
5. 达标后将决策从 Conditional 升为正式 Go。
