# 可观测执行详情：计划、工具结果与证据缺口

## Problem

当前 `/api/assistant` 的公开事件投影只保留节点名称、状态和耗时，删除了计划 `todos`、必需证据、工具结果以及 verifier 的 `gaps`。新逐条聊天活动流因此只能显示“已制定计划/工具已完成”，无法让操作者观察计划内容、执行结果或证据缺口；历史会话也复用同一投影，所以刷新后仍不可见。

## Decisions And Defaults

- 保持兼容的 SSE `type` / `stage` 语义，不新增一套事件总线。
- 新增后端 `HARNESS_PUBLIC_PROGRESS_DETAILS_ENABLED`，默认 `true`；设为 `false` 时恢复当前最小公开事件契约，作为回滚与故障降级路径。
- 公开字段使用明确的安全摘要：计划步骤、所需证据、缺口、工具执行状态/耗时、工具结果的受限文本摘要和结构化计数/状态。原始 `arguments`、完整 `result`、prompt、上下文、trace/span/evidence ID、usage 和内部错误细节继续禁止出现在公开事件中。
- 结果摘要在服务端截断、去控制字符并脱敏常见 token/password/secret、邮箱和手机号模式；无法安全结构化的结果只显示有限文本预览，不把原始对象递给浏览器。
- 前端活动节点默认展示计划/证据/结果详情；每个节点仍可独立展开，历史与 live 使用同一模型。
- 不改变工具执行、重试、持久化或鉴权策略；不新增运行时依赖。

## Scope And Non-goals

Scope:

- 扩展公开事件投影和前端 SSE 类型/防御性清洗。
- 将 plan/replan 的 todos、required evidence、trigger/gaps 显示在计划活动中。
- 将工具成功/失败状态、耗时、有限结果摘要显示在工具活动中。
- 将 verify/re-evidence 的 evidence counts、confidence、gaps 显示在证据节点中。
- 覆盖 live SSE、complete/history 投影、默认与关闭开关的自动化测试。

Non-goals:

- 不显示原始工具参数、完整日志/查询结果、模型 prompt、上下文白板或内部标识。
- 不改变公共事件类型、路由策略、工具重试或数据源策略。
- 不实现浏览器录屏、全量 trace 导出或新的生产修复执行器。

## Existing Capability And Research

Repository review (2026-08-01): `app/agent/public_events.py` is the existing privacy boundary; `events_emit.py` already emits plan and verifier fields; `processContent.ts` already contains result/evidence presentation logic for the legacy process panel; `AgentActivityFeed`/`inlineActivityModel` is the current granular chat surface. The implementation adapts these paths instead of creating a second event contract.

External checks (2026-08-01):

- [React Rendering Lists](https://react.dev/learn/rendering-lists), official React documentation, current React 19.2 docs while this project remains React 18.3.1. Stable keys and append/update ordering are directly compatible; no dependency adoption.
- [assistant-ui](https://github.com/assistant-ui/assistant-ui), GitHub repository checked at API `updated_at=2026-08-01`, latest commit `bf68ddf`, latest release `@assistant-ui/react-devtools@1.2.12` published 2026-07-30, MIT, unarchived and active. It offers a maintained event/thread UI, but adopting it would expand the dependency and replace the project's existing SSE/privacy boundary; rejected in favor of the local components.

No external code is copied and no dependency is added. License/security review therefore introduces no new supply-chain surface.

## Affected Files

- `app/config.py`, `.env.example`: add the reversible public-detail flag.
- `app/agent/public_events.py`: project safe plan, evidence, and result-preview fields.
- `frontend/src/types/events.ts`, `frontend/src/api/agentStream.ts`: type and defensive allowlist updates.
- `frontend/src/components/chat/inlineActivityModel.ts`, `AgentActivityFeed.tsx`: attach and render safe details for plan/tool/verify nodes.
- `tests/test_public_agent_events.py`, `tests/test_public_progress_e2e.py`, `frontend/src/components/chat/__tests__/inlineActivityModel.test.ts`, `frontend/src/components/chat/__tests__/AgentActivityFeed.test.tsx`, `frontend/src/components/__tests__/agentStream.test.ts`: contract, live/history, model, and UI coverage.
- `plan/2026-08-01-observable-execution-details-progress.md`: deviations and verification evidence.

## Verification Commands And Exit Criteria

Backend focused path:

```bash
PYTHONPATH=. .venv/bin/pytest -o addopts='' tests/test_public_agent_events.py tests/test_public_progress_e2e.py tests/test_tool_failure_contract.py -q --no-cov
```

Frontend focused path and build:

```bash
cd frontend
npm test -- --run src/components/chat/__tests__/inlineActivityModel.test.ts src/components/chat/__tests__/AgentActivityFeed.test.tsx src/components/__tests__/agentStream.test.ts
npm run build
```

Exit criteria:

1. Live and history public events contain the same safe plan/result/evidence detail contract.
2. Default UI visibly renders plan steps, tool result summaries, and missing evidence; terminal updates do not duplicate activities.
3. Sensitive fixture values, raw IDs, arguments, prompt/context fields, and full raw results are absent from both stream and history.
4. `HARNESS_PUBLIC_PROGRESS_DETAILS_ENABLED=false` restores the pre-change minimal contract and the UI remains functional.
5. Focused tests and production build pass; an automated API-to-persistence/history path is recorded in the progress document.

## Risks And Rollback

- Risk: a tool result may contain operational secrets or high-cardinality logs. Mitigation is server-side field allowlisting, bounded previews, control-character removal, and secret/PII redaction; tests include hostile fixtures.
- Risk: large public payloads increase SSE latency. Mitigation is hard per-field and per-list limits and omission of raw structures.
- Risk: older frontend bundles may receive new optional fields. They ignore unknown fields; the backend flag can immediately restore the old contract.
- Rollback: set `HARNESS_PUBLIC_PROGRESS_DETAILS_ENABLED=false` and restart the backend; revert only the scoped projection/frontend commits if a code rollback is required.

## Progress

See `plan/2026-08-01-observable-execution-details-progress.md` for deviations, commands, identifiers, and results.
