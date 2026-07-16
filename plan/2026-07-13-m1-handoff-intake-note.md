# 接手记录 — 2026-07-13

> 依据：[docs/pilot/handoff-2026-07-13-l1-m1-w2.md](../docs/pilot/handoff-2026-07-13-l1-m1-w2.md)

## 已完成（接手第一小时）

| 项 | 结果 |
|---|---|
| 必读文档 | 交接 / CLAUDE / 路线图 / W1·W2 计划进度 / AGENTS 索引 |
| 健康检查 | backend `http://127.0.0.1:9900/health` → **200**（Milvus/MCP/LLM configured） |
| 前端 | `http://127.0.0.1:5173` → **200** |
| ci-smoke | 本机 **无 `make`**；等价 pytest 子集 **37 passed** |
| `.env` 核对 | 显式：`HARNESS_ENABLED/MCP/DELEGATION=true`，`FORCE_EXPERT_DELEGATION=false`，`MONITOR_TARGET_MODE=prometheus`；M1 新增开关多未写入 `.env`，**走代码默认**（re-evidence/evidence_match/stateful/checkpoint 均为 True） |
| 评测资产 | `cases.jsonl` **13** 题；MINIMAL 10（含 N6/RE1）；历史结果 `20260712_171736` 仍在 |
| W3 计划 | ✅ 已写 [2026-07-13-m1-w3-replan-latency-eval.md](./2026-07-13-m1-w3-replan-latency-eval.md) 并挂 AGENTS/CLAUDE |

## 未做（有意）

- 未改 harness 业务代码（遵守「没有计划不改 harness」；W3 待授权）
- 未重跑 10 题真 LLM eval（H-2 可选，耗时长）
- 未改 H3 真人 OnCall 决策（仍豁免）

## 环境备注

- Windows + Git Bash；`make` 不可用 → 验证请用交接文档 §5.1 的 pytest 命令
- 密码仅在 `logs/.pilot_pass`（未读取、未写入文档）
- codebase-memory 项目名：`E-BaiduNetdiskDownload-OnCall-Agent-3.super_biz_agent_py-release-2026-05-17-super_biz_agent_py-master-commit`（索引偏旧，复杂符号仍以 rg/Read 为准）

## 下一动作

1. 用户审阅并授权 **W3 计划**  
2. 授权后按 Step 1→4 实现 replan / 时延 / 评测扩面  
3. （可选）先跑 10 题 eval 作 W3 前基线  
