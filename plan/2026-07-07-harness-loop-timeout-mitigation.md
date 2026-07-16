# Harness Loop 超时治理计划

> 状态: 计划草案
> 创建: 2026-07-07
> 涉及模块: `app/agent/harness/*`、`app/agent/experts/*`、`app/core/llm_client.py`、`app/services/*`、`app/config.py`

## 1. 背景与现象

### 1.1 现象

用户报告"loop 过程中存在模型进行下一步判断时超时,以及进入专家 agent 起步阶段缓慢"。从 `logs/app_2026-07-07.log` 中观察到一次典型超时:

```
16:01:10  assistant 收到统一助手请求: 请告诉现在的 redis 是否可以连接…
16:01:18  mcp_client 初始化                    ← 起步 +8s
16:01:19  专家加载 MCP[monitor] 10 个工具       ← 起步 +1s
16:01:21  专家加载 MCP[cls] 8 个工具           ← 起步 +2s
          …此后再无任何 LLM / tool 完成日志…
16:02:40  harness 执行超时 90.0s
```

收到请求到超时共 90 秒,期间 80 秒无任何模型/工具返回日志;超时后降级到 `_iter_knowledge_fallback` 也未在 1 分钟内恢复。

### 1.2 根因(摘要)

当前配置中三个独立超时**在数学上就不可行**:

| 配置 | 默认值 | 位置 |
|---|---|---|
| `harness_timeout_seconds` | 90s | [config.py:80](app/config.py#L80),外层 `asyncio.timeout(90)` 在 [loop.py:98](app/agent/harness/loop.py#L98) |
| `harness_delegate_timeout_seconds` | 45s | [config.py:88](app/config.py#L88),在 [subagent.py:62](app/agent/harness/subagent.py#L62) |
| `llm_timeout` | 60s | [config.py:40](app/config.py#L40),`httpx.AsyncClient(timeout=60)` 在 [llm_client.py:157](app/core/llm_client.py#L157) |
| `harness_tool_timeout_seconds` | 30s | [config.py:89](app/config.py#L89),`asyncio.wait_for(30)` 在 [agent_loop.py:146](app/agent/agent_loop.py#L146) |

`45s (delegate) + 60s (LLM) > 90s (总闸门)`,而 `harness_force_expert_delegation=True` 默认开启([config.py:86](app/config.py#L86)),意味着起步阶段就委派子专家。叠加 MCP 冷启动(8s) + 滚动摘要 LLM(最多 20s) + 路由 LLM(最多 60s) + 委派(45s) + 模型"下一步判断"(60s),任何一次 LLM 慢一点就 90s 撞线。

### 1.3 三大主因

1. **总闸门配小了** — 90s 在 gpt-5.4 扩展思考 + delegate 模式下不够用
2. **专家启动链路长** — MCP 客户端冷启 + 串行加载 + 每个专家新建 LLMClient + 滚动摘要 LLM
3. **模型"下一步判断"路径上没有单步超时** — 一旦某次 `stream_chat` 慢,父超时被它独占

---

## 2. 目标

| 指标 | 当前 | 目标 |
|---|---|---|
| P50 端到端延迟(简单知识类问题) | 未知(估 ~30~60s) | ≤ 25s |
| 90s 超时触发率 | 实测可复现 | < 1% |
| 专家启动开销(从收到请求到第一步 LLM 发出) | 11~15s | ≤ 5s |
| 单步 LLM "下一步判断"最大等待 | 60s(无独立上限) | 25s |
| 流式首字节(TTFB) | 5~30s | ≤ 8s |

---

## 3. 总体策略

按"**先不卡死,再提速**"两阶段推进:

- **P0(止血,1~2 天)**:调整超时链 + 加单步断流保护,确保不再因数学不可能而超时
- **P1(加速,3~5 天)**:MCP/连接复用 + 滚动摘要异步化 + 轻模型判断
- **P2(可观测,1 天)**:补齐时间埋点,作为后续优化依据

---

## 4. 实施项

### P0-1 调整超时链(高收益、低风险)

**问题**:90s 总闸门 < 45s 委派 + 60s LLM,数学不可能。

**改动**:

1. `app/config.py`
   - `harness_timeout_seconds: float = 150.0`(从 90 提到 150,留出 delegate+step+close 余量)
   - 新增 `harness_step_timeout_seconds: float = 25.0`(单步 LLM "下一步判断"独立限时)
   - 新增 `harness_fallback_timeout_seconds: float = 30.0`(降级路径独立限时,防止降级路径再卡)
   - `harness_delegate_timeout_seconds: float = 40.0`(略低于 step 预算,保证留有 close 余地)
2. `app/agent/harness/loop.py` 的 `stream()` 把 `asyncio.timeout(self.limits.timeout_seconds)` 替换为**双层**:
   - 外层 150s 兜底
   - 内层 `_stream_inner` 用 `asyncio.timeout(self.limits.step_timeout_seconds)` 包裹每次 `_stream_chat_turn` 调用,单步超时立即 `step_timeout` 事件 + 收尾
3. `app/agent/harness/loop.py` 的 `_fallback_stream` 外面再包一层 `asyncio.timeout(self.limits.fallback_timeout_seconds)`,超时则直接返回 `build_timeout_report`

**验证**:

```python
# tests/test_harness_service.py 新增
def test_harness_step_timeout_triggers_close():
    """单步 LLM 超时不应阻塞整个 harness,而是触发 step_timeout 事件并收尾。"""

def test_harness_fallback_timeout_returns_report():
    """降级路径自身超时,不能卡死 SSE。"""
```

**回滚**:所有超时都走 `getattr(config, "...")`,默认值改动可通过 env 覆盖。

---

### P0-2 单步断流保护

**问题**:`_stream_chat_turn` 没有自己的超时,完全被外层 90s/150s 兜底。

**改动**:`app/agent/harness/loop.py:377` 的 `_stream_chat_turn` 调用处用 `asyncio.timeout` 包裹:

```python
try:
    async with asyncio.timeout(self.limits.step_timeout_seconds):
        async for event in self._stream_chat_turn(...):
            ...
except TimeoutError:
    yield make_agent_event(
        agent="harness",
        stage="step_timeout",
        status="degraded",
        summary=f"单步 LLM 决策超过 {self.limits.step_timeout_seconds}s,提前收尾。",
        ...
    )
    # 直接进入无工具收尾,跳过剩余 step
    break
```

同样在 `app/agent/experts/base.py:156` 的 `_stream_chat_turn` 调用处加同样的保护(用 `expert_timeout_seconds=60` 作为兜底,单步 25s)。

---

### P1-1 LLMClient 单例 + 连接复用

**问题**:`new_llm_client()` 每次都 `httpx.AsyncClient(timeout=config.timeout)`,每个专家实例/每次请求都新建连接池。

**改动**:`app/core/llm_client.py`

1. 新增模块级 `_default_client: LLMClient | None = None` 和 `async def get_default_llm_client() -> LLMClient`
2. `new_llm_client()` 改为复用同一个 client(只在 `aclose()` 时真正关闭)
3. `aclose()` 区分 owned/borrowed,避免双重关闭

**好处**:省掉每次专家启动 0.5~1.5s 的 TCP+TLS 握手,且连接池复用显著降低后续请求 TTFB。

**注意**:进程内共享,需要保证 `LLMClient` 是线程/协程安全的(目前已经是 — `httpx.AsyncClient` 自身支持并发)。

---

### P1-2 MCP 客户端预热 + 并行加载

**问题**:`monitor` 和 `cls` 在 `collect_tools` 内串行加载,起步 +3s;且首次请求时 `mcp_client.get_mcp_client_with_retry` 还要冷启 5~8s。

**改动**:

1. `app/main.py` 的 `lifespan` 启动阶段:
   ```python
   from app.agent.mcp_client import get_mcp_client_with_retry
   await get_mcp_client_with_retry()  # 进程级预热
   ```
2. `app/agent/experts/base.py:317` 把串行 `for server in servers` 改成 `asyncio.gather`:
   ```python
   results = await asyncio.gather(
       *[client.get_tools(server_name=s) for s in servers],
       return_exceptions=True,
   )
   ```
3. `app/agent/harness/registry.py` 给 `collect_tools` 加缓存:`(route,)` 作为 key,同一个 session 内复用,避免 `_seed_expert_delegation` 时第二次重新拉取

---

### P1-3 滚动摘要去同步化

**问题**:`abuild` 路径上 `_load_or_update_rolling_summary` 同步调 LLM(最多 20s),直接 +20s 起步开销。

**改动**:`app/agent/harness/context.py`

1. `abuild` 先返回**不带新摘要**的 context(用旧的或空)
2. 后台 `asyncio.create_task` 异步调一次 LLM 更新摘要并写回 conversation_service
3. 第二次起就能命中已更新的摘要
4. 配合 memory_cache 中已有的 rolling summary 缓存,首问多等一次,后续问题 < 1s

**trade-off**:首问滚动摘要仍是旧版(可接受 — 系统提示里有滚动摘要段,没有比空好)。

---

### P1-4 模型分层:轻模型做"下一步判断"

**问题**:`gpt-5.4` 扩展思考模型对 tool-call 决策慢(单次 10~30s);实际只需要"调用哪个工具",不需要深度思考。

**改动**:

1. `app/config.py` 新增:
   ```python
   llm_planner_model: str = "gpt-5-mini"  # 路由/规划/单步判断用
   llm_reasoner_model: str = "gpt-5.4"   # 最终收口、证据自检用
   ```
2. `app/agent/harness/loop.py`:
   - `_stream_chat_turn` 内部根据 stage 选择模型:`model_decision` 阶段用 `planner_model`,`model_closing` 阶段用 `reasoner_model`
   - `verifier.averify` 用 `reasoner_model`
3. `app/services/router_service.py` 的 `_semantic_route_message` 改用 `planner_model`

**预期**:路由 + 单步判断从 10~30s 降到 2~5s。

---

### P1-5 日志专家的 Map-Reduce 提前截断

**问题**:`log_max_lines=20000` + 长行,触发 5~10 个 chunk,每次 chunk LLM 摘要 10~30s,合计 50~300s 远超 90s 闸门。

**改动**:`app/agent/experts/log_pipeline.py:223`

1. 在 `_mapreduce_summarize` 进入 Map 之前**先按行数截断**到 `log_max_lines=8000`(从 20000 砍半)
2. 同步调小 `app/config.py` 的 `log_max_lines: int = 8000`
3. 给 `_mapreduce_summarize` 整体加 `asyncio.timeout(expert_timeout_seconds // 2)`,超时返回"日志摘要超时,已使用聚类头部"的兜底结果

---

### P2 可观测性

**问题**:目前 `agent_event` 只有 stage,没有 `duration_ms`;`LLMResponse.usage` 只有 token 没有时间,无法定位"哪一次 LLM 慢"。

**改动**:

1. `app/agent/harness/loop.py` 所有 `make_agent_event` 调 payload 增 `started_at` / `duration_ms` 字段(用 `time.perf_counter()` 包裹)
2. `app/core/llm_client.py` 的 `LLMResponse` 增 `latency_ms: int` 字段,`_post_with_retry` / `_stream_with_retry` 填充
3. `app/agent/agent_loop.py` 的 `_run_one` 增 `tool_latency_ms` 到 `make_tool_event` payload
4. 上述字段在 SSE 事件 JSON 里也透传给前端,前端时间线面板直接显示
5. 新增 `tests/test_harness_observability.py` 验证关键事件包含 `duration_ms`

---

## 5. 实施顺序与工期

| 阶段 | 任务 | 依赖 | 估时 |
|---|---|---|---|
| P0-1 | 调整超时链 + 新增 `step_timeout` / `fallback_timeout` 配置 | 无 | 0.5d |
| P0-2 | 单步断流保护 (`asyncio.timeout` 包裹 `_stream_chat_turn`) | P0-1 | 0.5d |
| P0 测试 | `test_harness_step_timeout_triggers_close` / `test_harness_fallback_timeout_returns_report` | P0-1, P0-2 | 0.5d |
| **P0 小计** | | | **1.5d** |
| P1-1 | LLMClient 单例 + 连接复用 | 无 | 0.5d |
| P1-2 | MCP 客户端预热 + 并行加载 | 无 | 0.5d |
| P1-3 | 滚动摘要去同步化 | 无 | 0.5d |
| P1-4 | 模型分层(轻模型做判断) | P1-1 | 1d |
| P1-5 | 日志 Map-Reduce 提前截断 + 限时 | 无 | 0.5d |
| **P1 小计** | | | **3d** |
| P2 | 可观测性埋点 + duration_ms 透传 | P0 | 1d |
| **总计** | | | **5.5d** |

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 调高 `harness_timeout_seconds` 让请求更慢返回 | 用户等待变长 | 配合 P1-1~P1-4 让单步变快,净效果是 P50 下降而非上升 |
| 滚动摘要异步化导致首问摘要陈旧 | 首问回答可能缺上下文 | 系统提示里 history 段保留,只是缺少更新;后续请求立刻恢复 |
| LLMClient 复用后某次 aclose 不彻底 | 连接泄漏 | 单例模式下 `_owns_client=True` 永不 aclose,只在 `lifespan` shutdown 时关 |
| 轻模型做"下一步判断"质量下降 | tool-call 选错 | 用 `router_multilabel_enabled` + 已有的 `harness_no_progress_limit=2` 兜底;同时小流量灰度 |
| MCP 并行加载时某个 server 报异常吞掉 | 启动时静默失败 | `return_exceptions=True` 后,只 warn 日志、不影响其他 server |

---

## 7. 验收标准

1. 同样 16:01:10 那条 redis 问题,**不应再出现 90s 超时**;端到端 < 60s 返回
2. 复跑 100 次混合 query(简单知识 / 日志 / 指标 / 变更 / 跨域),P95 ≤ 60s,90s 超时触发率 < 1%
3. `pytest tests/test_harness_service.py tests/test_harness_observability.py` 全绿
4. 前端时间线面板能看到每个 stage 的 `duration_ms`
5. 滚动日志里能看到 "单步 LLM 决策超过 25s" 这类 step_timeout 事件的产生(用于发现"模型偶尔慢"的情况)

---

## 8. 不在本次范围

- 替换 gpt-5.4 为更快的同档模型(需要业务侧确认)
- 完全重写 harness 异步并发模型(本计划保留串行结构,只补防护)
- Milvus 慢查询优化(独立的向量检索性能议题)
- 前端时间线 UI 优化(等 P2 埋点稳定后再做)
