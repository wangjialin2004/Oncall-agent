# 路由纠偏 + 轻问题快路径 + 续聊继承

## 问题

会话 `session-1784785246267-f10ea7323a461` 暴露了三类主路径问题：

1. **续聊短句误路由**：用户说「继续」时语义置信度 0.22，触发 `low_confidence_*_default_diagnosis`，把 knowledge 续聊打成 diagnosis。
2. **低置信一律 diagnosis 过激**：对闲聊/身份/解释类问题，默认 diagnosis 会强制「指标/日志/变更」证据闭环。
3. **knowledge 证据过硬**：`你好` / `你是什么模型` 也被要求 `知识库检索结果 + 适用前提`，并进入 re_evidence/replan；依赖故障（embedding）会被放大成冗长缺口报告。

目标：**路由第一步正确，非故障问题不再硬跑 OnCall 取证闭环**。

## 决策与默认

| 项 | 决策 | 默认 |
|---|---|---|
| 续聊继承 | 短句续聊优先继承上一轮 `conversation_turns.route` | **开** |
| 低置信回退 | 不再无刀 diagnosis；仅故障信号/运维 hint 时 diagnosis | **开** |
| knowledge 轻路径 | 问候/身份/纯解释：弱化 required_evidence，跳过 re_evidence/replan | **开** |
| 标准运维路由 | 告警/日志/变更/根因路径保持不变 | 不变 |
| 开关 | 三个独立 env 降级开关 | 见下 |

### 新配置（均有降级）

| Env | 默认 | 含义 |
|---|---|---|
| `ROUTER_CONTINUATION_INHERIT_ENABLED` | `true` | 短句续聊继承上一轮 route |
| `ROUTER_LOW_CONFIDENCE_KEEP_SEMANTIC_ENABLED` | `true` | 低置信保留语义 route，不全量 diagnosis |
| `HARNESS_KNOWLEDGE_LIGHT_PATH_ENABLED` | `true` | knowledge 轻问题跳过重取证闭环 |

回滚：任一开关置 `false` 即恢复旧行为。

## 范围

### 改动文件

- `app/config.py`：新增 3 个 flag
- `app/services/router_service.py`：
  - 续聊检测 + 可选 `previous_route`
  - 低置信回退策略收紧
  - `_resolve_route(message, *, previous_route=None)`
- `app/agent/harness/stream_inner.py`：从会话历史取上一轮 route 传入 router
- `app/agent/harness/planner.py`：knowledge 轻路径时 required_evidence 置空/极简
- `app/agent/harness/close_path.py` 或 policy：轻路径跳过 re_evidence/replan
- `app/services/conversation_service.py`：若已有「最近一轮 route」读取则复用
- 测试：router 单测 + harness soft-path 回归
- `.env.example`、本计划、`AGENTS.md` 索引

### 非目标

- 不改 SSE `type` 语义
- 不改 checkpoint Redis schema
- 不引入自动处置/写操作
- 不做完整多轮意图分类模型

## 设计细节

### A. 续聊短句继承

命中集合（规范化后精确或整句匹配）：

```text
继续 / 接着 / 接着说 / 然后呢 / 还有呢 / 再说详细点 / 详细点 / 展开说说
continue / go on / more details
```

规则：

1. 仅当 `ROUTER_CONTINUATION_INHERIT_ENABLED=true`
2. 当前 message 命中续聊短句
3. `previous_route in EXPERT_ROUTES` 且不为空
4. 返回 `RouteDecision(route=previous_route, reason="continuation_inherit_<route>", confidence=0.85)`
5. **先于** 低置信 diagnosis 回退执行

`previous_route` 来源：同 `owner_key+session_id` 最近一条 `conversation_turns.route`（非空）。

### B. 低置信回退

旧：

```text
semantic.confidence < min_confidence → diagnosis
```

新（开关开时）：

```text
if confidence < min_confidence:
  if semantic.route == knowledge and not incident_signals:
    keep knowledge (reason=low_confidence_keep_knowledge)
  elif hints 含 metric/log/change 或 incident_signals:
    diagnosis (reason=low_confidence_operational_default_diagnosis)
  else:
    keep semantic.route (reason=low_confidence_keep_<route>)
```

开关关：保持旧「一律 diagnosis」。

### C. knowledge 轻路径

判定（全部满足）：

1. `HARNESS_KNOWLEDGE_LIGHT_PATH_ENABLED=true`
2. `focus_route == knowledge`
3. 无具体运维目标（复用 router 的 concrete target 检测为假）
4. 无故障信号词，或命中问候/身份/元问题模式

命中后：

- plan.`required_evidence = []`（或仅保留空，不强制知识库）
- `can_re_evidence = False`
- `_should_replan(...)` 对轻路径返回 False
- 允许模型 0 tool 直接回答；若主动调 knowledge 工具仍可执行

## 验证

```bash
.venv/bin/python -m pytest \
  tests/test_router_lightpath_continuation.py \
  tests/test_harness_service.py -k 'router or route or knowledge' \
  -q --tb=short
```

手工回归句：

1. `你好` → knowledge，不强制 re_evidence 长报告
2. `你是什么模型` → knowledge，可直接答
3. 同上会话再发 `继续` → **knowledge**（继承），不是 diagnosis
4. `查一下当前有哪些告警` → metric（回归）
5. `order-api CPU 告警，接口超时，帮我根因分析` → diagnosis（回归）

退出标准：

- [ ] 续聊继承单测绿
- [ ] 低置信 keep knowledge 单测绿
- [ ] 运维强词路径不回归
- [ ] knowledge 轻路径不进 re_evidence/replan
- [ ] ruff/compile 通过

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 续聊继承错上一轮 route | 仅短句触发；开关可关 |
| 低置信保留错误 route | 有故障信号仍 diagnosis |
| 轻路径漏取证 | 仅 knowledge + 无故障信号 |

回滚：三开关全 `false`，无需改代码。

## 实现顺序

1. 写配置 + router 续聊/低置信逻辑 + 单测
2. harness 注入 previous_route
3. planner/close_path 轻路径
4. 跑验证并更新索引状态

## 验证证据

- 2026-07-23: `tests/test_router_lightpath_continuation.py` + router 相关 harness 测试全部通过。
- 手工 dry-run：`你好`/`你是什么模型` → knowledge 轻路径；`继续`+previous=knowledge → continuation_inherit_knowledge。
- 开关：`ROUTER_CONTINUATION_INHERIT_ENABLED` / `ROUTER_LOW_CONFIDENCE_KEEP_SEMANTIC_ENABLED` / `HARNESS_KNOWLEDGE_LIGHT_PATH_ENABLED` 默认 true，可独立关闭回滚。
