# 并行专家过程栏可视化

## 问题

后端已通过 `delegate_parallel` / force-seed / aux_probe 并行委派多个专家，但前端过程栏：

1. 把一次并行委派压成单条 activity / 计为 1 次委派
2. 子专家事件等全部结束后才按时间线串行回放
3. 没有按专家并排展示任务与状态

用户在最近会话 `e2e-full-246cd48fe0` 中看到“没有同时显示两个专家”，根因是前端信息架构，不是后端未并行。

## 决策与默认

| 项 | 决策 |
|---|---|
| 改动面 | **前端过程栏为主**；不改 SSE type 语义 |
| 并行展示位置 | 过程栏 execute/retry 步骤内，按专家卡片并排 |
| 中心对话区 | 仍只显示最终回答；不把子专家草稿直接当正文 |
| 专家计数 | 按「委派 call × 专家」计，一次 parallel 的 N 个专家计 N |
| 后端增量推送 | **本轮非目标**；先消费现有事后回灌事件 |

## 范围

### 做

1. `processModel.ts`
   - `delegate_parallel_start` / `delegate_parallel` / `delegate_parallel_done` 展开为每专家一条 activity
   - 用 `tool_call_id` + expert 作为 activity id
   - 子事件（`delegated_expert` / `parent_tool_call_id`）归并到对应专家卡片
   - 修正 `counts.experts`
2. `ProcessTimeline.tsx` + `styles.css`
   - 同一 `parallelGroupId` 的专家 activity 用网格并排渲染
3. 测试
   - processModel：并行 2/3 专家计数与卡片
   - 既有单专家委派去重回归

### 不做

- 中心区双栏实时流
- 后端在 parallel 执行中增量推送子专家事件
- 改 harness / SSE type 契约

## 受影响文件

- `frontend/src/components/agent-process/processModel.ts`
- `frontend/src/components/agent-process/ProcessTimeline.tsx`
- `frontend/src/components/agent-process/__tests__/processModel.test.ts`
- `frontend/src/styles.css`
- `AGENTS.md`（计划索引）

## 验证

```bash
cd frontend && npm test -- --run src/components/agent-process/__tests__/processModel.test.ts
cd frontend && npm test -- --run
cd frontend && npm run build
```

## 退出标准

1. 一次 `delegate_parallel` 到 metric+log 时，过程栏出现两张专家卡，计数 `experts >= 2`
2. 子工具事件挂在对应专家卡详情里，不把整次 parallel 压成一条
3. 单专家 `delegate_to_expert` 仍去重为 1
4. 前端 focused 测试 + build 通过

## 验证证据（2026-07-23）

```bash
cd frontend
npm test -- --run
# 13 files / 88 tests passed

npm run build
# tsc -b && vite build 成功
```

实现要点：
- `processModel` 将 `delegate_parallel_*` 展开为 `parallel:{callId}:{expert}` 专家卡
- `counts.experts` 按专家卡计数，不再把整次 parallel 计 1
- `ProcessTimeline` 对同 `parallelGroupId` 使用并排网格
- 中心对话区仍只显示最终答案；SSE type 未改

## 风险与回滚

- 风险：历史事件缺 `experts`/`results` 时卡片过空 → 回退到现有单条 activity 展示
- 回滚：还原上述前端文件即可；无后端/数据迁移

## 开关

纯前端展示改进，无新环境变量。
