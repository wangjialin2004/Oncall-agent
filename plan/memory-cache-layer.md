# Plan：记忆子系统缓存层规划

- 文档版本：v0.2（评审修订，待用户授权进入实现）
- 日期：2026-06-26
- 状态：待评审 / 未进入实现阶段（受 CLAUDE.md 约束）
- 评审结论：**有条件批准**。进入实现前必须落地 §3.7 的 4 项放行条件（别名防护、key/测试隔离、`upsert_relation` 失效、recall 命中时 `hit_count` 决策）；连接池保持出范围。
- 关联模块：[app/services/experience_memory_service.py](../app/services/experience_memory_service.py)、[app/services/service_knowledge_service.py](../app/services/service_knowledge_service.py)、[app/services/user_preference_service.py](../app/services/user_preference_service.py)、[app/services/experience_memory_index_service.py](../app/services/experience_memory_index_service.py)、[app/api/memory.py](../app/api/memory.py)

## 1. 背景与目标

记忆子系统当前所有读写路径都直接落 SQLite（`volumes/long_term_memory.db`），读路径里也存在两段"全表扫"逻辑——一旦经验/服务条目数增长到几百条以上，每次 Recall / Lookup 都会触发 O(N) 的相似度计算、JSON 解析与 SQL `count(*)`，在每次 harness 主循环里被多次命中，存在显著的延迟与 CPU 浪费。

目标：

- 为三个长/短期记忆服务建立**统一、可降级、可观测**的进程内缓存层。
- 让热路径（Recall、Lookup、format_for_prompt）的尾延迟稳定，不随 SQLite 行数线性增长。
- 不破坏现有 SQLite 真源（write-through）与 `update/create/rebuild_index` 的写路径语义。
- 提供**失效钩子**，让任何写入都能精准作废相关 key，避免读到过期记忆。
- 暴露命中率 / 失效率 / 命中节省 SQL 次数的轻量指标，便于回归对照。

## 2. 当前现状（基于代码审计）

### 2.1 三个服务 + 一处索引

| 服务 | 真源 | 典型读 API | 调用点 | 是否已有缓存 |
| --- | --- | --- | --- | --- |
| `ExperienceMemoryService` | SQLite `experience_memories` | `recall / get / list` | `tools/recall_experience.py`、API `GET /memory/experiences`、Milvus 召回后回查 | **无**。`recall` 命中 Milvus 时仍然为每条候选执行 `self.get(id)`；退化路径 `_recall_from_sqlite` 是全表 + 文本相似度。`_find_merge_target` 也是 `list(limit=1000)` 后逐条相似度 |
| `ServiceKnowledgeService` | SQLite `services` + `service_baselines` + `service_relations` | `lookup / list_services / compare_metric` | `tools/lookup_service_knowledge.py`、API `GET /memory/services*`、以及未来 diagnose 阶段的频繁查询 | **无**。`lookup` 每次 3 条 SQL（主行 + baselines + relations），`compare_metric` 复用 `lookup`，等于把读放大 |
| `UserPreferenceService` | SQLite `user_preferences` | `get / format_for_prompt` | `harness/context.py` `HarnessContext._build_system_prompt`、`harness/loop.py` `_iter_knowledge_fallback`、`services/router_service.py` 路由阶段 | **无**。同一 owner_key 在一次会话可能被调用 2–3 次 |
| `ExperienceMemoryIndexService` | Milvus + 本地 `_local_store` | `recall / upsert / disable / rebuild` | `experience_memory_service.recall`、API `POST /memory/experiences/rebuild-index` | 仅 `_local_store` 充当进程内快照，无 TTL/失效，写入路径覆盖 |

### 2.2 已知热点 / 风险

- **R-hot-1** `user_preference_service.format_for_prompt(owner_key)`：每轮对话至少 2 次（router_service + harness context，harness 主循环里 `_iter_knowledge_fallback` 还可能再 1 次）。SQLite 命中便宜，但 JSON 解析 + prompt 拼接每轮重复执行，浪费明显且会随 prompt 模板扩张变贵。
- **R-hot-2** `experience_memory_service.recall`：harness 多步取证时可能多次调用（每个 tool 决策都可能 recall 一次），Milvus 退化后是 1000 行全表相似度。
- **R-hot-3** `service_knowledge_service.lookup`：服务诊断工具 `lookup_service_knowledge` 在多步取证场景下会反复查同一 `service_name`，每次 3 条 SQL。
- **R-hot-4** `_find_merge_target` 写入路径里的全表扫：用户每次反馈触发相似度匹配 → 在条目增长后写延迟也会上升。
- **R-risk-1** SQLite 单连接串行化（`@contextmanager` + `connect/close`），长事务 + 高并发 recall 会互相阻塞。
- **R-risk-2** `experience_memory_index_service._local_store` 的"假缓存"：进程级 dict，没有 TTL/失效，`disable` 之外的字段（confidence、root_cause）变更不会反映到本地副本。

## 3. 关键设计决策

### 3.1 缓存层级与职责

| 层级 | 位置 | 内容 | 失效策略 | 一致性 |
| --- | --- | --- | --- | --- |
| **L1 进程内（主目标）** | 新增 `app/services/memory_cache.py` | SQLite 行的反序列化对象 + `format_for_prompt` 的拼装结果 + Milvus 候选 → 完整 memory 的回填 | TTL + 显式 `invalidate` | 写穿 SQLite 后调用对应 invalidate |
| **L2 索引层（已有/扩）** | `experience_memory_index_service._local_store` + 未来 `service_knowledge` 索引 | 项目级只读快照，启动或 `rebuild_*` 时构建 | 整体替换 + `version` 字段 | 仅供退化路径兜底 |
| **L3 进程内连接池（次要）** | SQLite `connect()` 替换为线程局部连接池（暂不在 P0 范围） | 长连接复用 | — | — |

L1 是本规划的主战场。L2 / L3 作为未来方向，不在第一阶段交付。

### 3.2 Key 命名约定

统一前缀 `memory:` + 域 + 参数，避免散落 magic string：

- `memory:user_pref:{owner_key}` → `format_for_prompt` 字符串结果（同时缓存反序列化的 dict，标注 `cache_kind=prompt` / `cache_kind=dict`）
- `memory:user_pref:dict:{owner_key}` → `get` 返回 dict
- `memory:exp:get:{experience_id}` → 单条 memory dict
- `memory:exp:list:{project_id}:{enabled}:{limit}` → `list` 结果
- `memory:exp:recall:{project_id}:{hash(query)[:16]}:{top_k}` → `recall` 返回 list（仅 Milvus 命中路径）
- `memory:svc:lookup:{project_id}:{service_name}:{environment}` → `lookup` 完整 dict
- `memory:svc:list:{project_id}:{environment or "*"}` → `list_services` 结果
- `memory:svc:metric:{project_id}:{service_name}:{environment}:{metric_name}` → `compare_metric` 结果

**重要：key 必须带 DB 维度。** 三个 service 都接受 `db_path=` 构造参数，且 `memory_cache` 计划做成进程级全局单例；若 key 不含 db 维度，则不同 DB 文件（尤其测试用临时库）会在同一全局缓存里串号。约定在所有 key 前再加一段稳定的 DB 短哈希：`memory:{db_tag}:...`，其中 `db_tag = sha1(str(resolved_db_path))[:8]`。生产单库时该段恒定、零成本；测试/多实例时天然隔离。

### 3.3 失效（Invalidation）矩阵

| 写操作 | 需要作废的 key |
| --- | --- |
| `experience_memory_service.create_*` / `_merge_into` | `memory:exp:list:{project_id}:*` 全部 + `memory:exp:get:{id}` + `memory:exp:recall:{project_id}:*`（保守全部失效，仅 top-k 召回受影响） |
| `experience_memory_service.update(enabled / confidence)` | `memory:exp:get:{id}` + `memory:exp:list:{project_id}:*` + `memory:exp:recall:{project_id}:*` |
| `experience_memory_service._increment_hit` | **不失效**（hit_count 是计数器，前端 UI 不依赖绝对值；如下游真依赖，再单独失效 `memory:exp:get:{id}`）。注意配合下方 §3.3.1 的 recall 缓存决策 |
| `service_knowledge_service.upsert_service` / `upsert_baseline` / `delete_baseline` | `memory:svc:lookup:{project_id}:{service_name}:{env}` + `memory:svc:list:{project_id}:{env or *}` + `memory:svc:metric:*:*:env:*`（仅当 metric_name 匹配） |
| `service_knowledge_service.upsert_relation` | **（评审补充）** `lookup` 返回体含 `relations`，新增 relation 必须失效 `memory:svc:lookup:{project_id}:{source_service}:{env}` + `memory:svc:list:{project_id}:{env or *}`；否则 lookup 缓存会 stale 到 TTL |
| `user_preference_service.upsert` | `memory:user_pref:*:{owner_key}` 全部 |

实现：让每个 service 持有 `MemoryCache` 引用，写方法尾部调 `cache.invalidate(prefix_set)`。
`import_from_monitor_mcp` 在循环里逐条 `upsert_service`，应在导入结束后**批量失效一次**（`invalidate_prefix("memory:{db_tag}:svc:")`），避免每条触发一次 O(N) 全缓存扫的失效风暴。

#### 3.3.1 recall 缓存与 hit_count 的取舍（评审补充，放行条件之一）

`recall` 命中缓存就不会再走 `_increment_hit`（一个写）——**缓存 recall 等于让 hit_count 停止增长**，这是隐性行为变更。二选一并在实现前拍板：

- **方案 A（推荐，默认）**：`recall` **不进 L1 缓存**。理由：返回体里的 `similarity` 随 query 变、`conflict_count` 随语料变，缓存价值低且语义脆；真正的热点是退化路径 `_recall_from_sqlite` 与写路径 `_find_merge_target` 的全表扫，缓存 `list()`（见 §7 R4 与下方 ROI 提示）已能覆盖。
- **方案 B**：缓存 recall，但命中时仍补一次 `_increment_hit`（hit_count 语义不变，代价是命中也产生一次轻量写）。

### 3.4 TTL 与容量

- 默认 TTL：**长 TTL**（偏好 5 min，经验/服务知识 60 s；可由 config 覆盖）。
- 默认容量：**LRU 1024** 个 key（按条目，process 内单实例足够）。
- 命中率：保留 `cache_stats()` → `{hits, misses, invalidations, evicted, hit_ratio}`，可通过 `GET /health` 或新加 `/admin/memory-cache-stats` 暴露。

### 3.5 失败与降级

- 缓存本身**不可阻塞业务路径**：序列化失败 / 反序列化失败 → 当作 miss，直接走 SQLite。
- 写入失败 → 仍允许 SQLite 写入成功，缓存层仅 `try/except` 记 warning。
- 进程重启 / fork → 全部 miss，可接受（SQLite 是真源）。
- 关闭开关：`MEMORY_CACHE_ENABLED=0` 时所有缓存方法变成 passthrough。

### 3.6 别名安全：copy-on-read（评审补充，放行条件之一）

**这是最高优先级的正确性约束。** 调用方会就地改写读出来的对象：

- `experience_memory_service.recall` 对 `self.get(id)` 的返回写入 `memory["similarity"]` / `memory["conflict_count"]`；
- `_recall_from_sqlite` / `_find_merge_target` 对 `self.list(...)` 的每个元素同样改写。

若 `get`/`list` 命中后返回缓存内的**同一引用**，缓存对象会被业务路径污染（脏键累积、similarity 串值），命中越多脏得越快。

约定：

- `MemoryCache.get` 返回前做 **deep copy**（dict/list 结构简单，`copy.deepcopy` 或 `json` round-trip 均可），或缓存只存不可变快照、读出时重建。
- prompt 字符串等不可变值无需拷贝。
- 单测必须显式覆盖"命中后修改返回值不影响下一次命中"的场景。

### 3.7 放行条件（评审，进入实现前必须落地）

1. **别名防护**：`get`/`list` 命中返回 deep copy（§3.6），含单测覆盖。
2. **key/测试隔离**：所有 key 带 `db_tag`（§3.2），并提供 `memory_cache.clear()` 供测试 fixture 在每个用例前重置。
3. **`upsert_relation` 失效**：补进失效矩阵（§3.3）。
4. **recall 命中时 hit_count 决策**：在 §3.3.1 的方案 A / B 中拍板（默认 A）。

连接池（R-risk-1）保持出范围（见 §3.8）。

### 3.8 不在本期范围

- 分布式缓存（Redis / Memcached）—— 当前是单进程 FastAPI，无该需求。
- 写穿透到外部 KV。
- `service_knowledge_service` 的 vector 索引（暂用 SQLite + LRU 缓存即可）。
- L3 连接池。

## 4. 任务拆解

| 阶段 | 任务 | 涉及文件 |
| --- | --- | --- |
| P0 评审 | 决策 TTL 默认值、是否暴露 `/admin/memory-cache-stats`、是否在 P0 做 `_local_store` 治理 | 本文档 §3 |
| P1 接口与配置 | 新增 `app/services/memory_cache.py`（TypedDict key + LRU + TTL + stats）；在 `config.py` 加 `memory_cache_enabled / memory_cache_ttl_* / memory_cache_max_entries` | `app/services/memory_cache.py`（新）、`app/config.py` |
| P2 服务接入 | 在三个 service 中嵌入缓存：read 路径先查 cache、未命中回填；write 路径尾调用 `invalidate`。**ROI 优先级：先做 `list()`（同时惠及退化 recall 与写路径 `_find_merge_target`）与 `svc:lookup`；`format_for_prompt` 价值最低，可降级为可选** | `experience_memory_service.py`、`service_knowledge_service.py`、`user_preference_service.py` |
| P3 指标与日志 | `cache.stats()` 通过 `app/api/health.py` 新增端点暴露；关键 warn（eviction rate 异常、invalidate 风暴）走 loguru | `app/api/health.py`、`app/core/metrics.py` |
| P4 测试 | 单测：TTL 过期、LRU 淘汰、invalidate 矩阵、stats 计数、降级路径 | `tests/test_memory_cache.py`（新） |
| P5 基准 | 录制脚本：在 `volumes/long_term_memory.db` 灌 500/5000 行后，对比 recall / lookup / format_for_prompt 的 P50/P95 | `scripts/bench_memory_cache.py`（新） |

### 4.1 P1 接口草稿

```python
# app/services/memory_cache.py
from __future__ import annotations
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Generic, Hashable, Optional, TypeVar

V = TypeVar("V")

@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    invalidations: int = 0
    evictions: int = 0
    errors: int = 0

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class TTLRUCache(Generic[V]):
    def __init__(self, *, max_entries: int = 1024, default_ttl: float = 60.0):
        self._data: "OrderedDict[Hashable, tuple[V, float]]" = OrderedDict()
        self._max_entries = max_entries
        self._default_ttl = default_ttl
        self._lock = RLock()
        self.stats = CacheStats()

    def get(self, key: Hashable) -> Optional[V]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            value, expires_at = entry
            if expires_at and expires_at < time.monotonic():
                self._data.pop(key, None)
                self.stats.misses += 1
                return None
            self._data.move_to_end(key)
            self.stats.hits += 1
            # 别名防护（§3.6）：dict/list 必须 deep copy 后返回，避免调用方就地改写
            # 污染缓存内对象；不可变值（str 等）可直接返回。
            return _safe_copy(value)

    def set(self, key: Hashable, value: V, ttl: Optional[float] = None) -> None:
        with self._lock:
            expires_at = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)
                self.stats.evictions += 1

    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            if self._data.pop(key, None) is not None:
                self.stats.invalidations += 1

    def invalidate_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._data if isinstance(k, str) and k.startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
                self.stats.invalidations += 1
            return len(keys)

    def clear(self) -> None:  # 测试 fixture 用：每个用例前重置，规避全局单例串号
        with self._lock:
            self._data.clear()
```

`_safe_copy` 约定：对 `dict`/`list` 做 `copy.deepcopy`（结构简单，开销可忽略），其余类型原样返回。recall key 里的 query 不能用内置 `hash()`（返回 int 不可切片，且跨进程随机化）——用 `hashlib.sha1(_normalize(query).encode()).hexdigest()[:16]`，即 §3.2 的 `memory:{db_tag}:exp:recall:...` 写法。

注入点（示例，`user_preference_service`）：

```python
# app/services/user_preference_service.py
from app.services.memory_cache import memory_cache

class UserPreferenceService:
    def get(self, owner_key: str) -> dict[str, Any] | None:
        key = f"memory:user_pref:dict:{owner_key}"
        cached = memory_cache.get(key)
        if cached is not None:
            return cached
        with self._connection() as conn:
            row = conn.execute(...).fetchone()
        value = _row_to_dict(row) if row else {}
        memory_cache.set(key, value)
        return value or None

    def format_for_prompt(self, owner_key: str) -> str:
        key = f"memory:user_pref:{owner_key}"
        cached = memory_cache.get(key)
        if cached is not None:
            return cached
        prompt = self._build_prompt(self.get(owner_key))
        memory_cache.set(key, prompt)
        return prompt

    def upsert(self, ...) -> dict[str, Any]:
        result = self._raw_upsert(...)
        memory_cache.invalidate_prefix(f"memory:user_pref:")
        memory_cache.invalidate(f"memory:user_pref:dict:{owner_key}")
        return result
```

### 4.2 P1 配置草稿（追加到 `app/config.py`）

```python
# Long-term memory cache
memory_cache_enabled: bool = True
memory_cache_max_entries: int = 1024
memory_cache_ttl_user_preference_seconds: float = 300.0
memory_cache_ttl_experience_seconds: float = 60.0
memory_cache_ttl_service_knowledge_seconds: float = 60.0
```

### 4.3 P3 暴露点

- `GET /admin/memory-cache-stats`（仅 owner_key 命中）→ `{hits, misses, hit_ratio, evictions, invalidations, size}`。
- 不引入新的 metrics endpoint；只在日志里加 INFO 摘要（每 1000 次操作一次）。

## 5. 验证方式

- 单元测试（`tests/test_memory_cache.py`，新建）：
  - TTL 过期：`time.sleep` 或注入 fake clock。
  - LRU 淘汰：写满 1024 + 1 → 验证最旧 key 消失且 `evictions += 1`。
  - `invalidate_prefix`：前缀匹配准确、不误伤其它前缀。
  - **别名防护（放行条件 1）**：命中后修改返回的 dict/list，再次命中应拿到未被污染的副本。
  - 降级：注入异常让 `set` 抛错 → stats.errors += 1、read 仍可继续走 SQLite。
  - 并发：`threading` 启 8 线程各做 1k 次 get/set，验证无死锁、stats 计数等于实际命中 + miss。
- **测试隔离（放行条件 2）**：新增 autouse fixture，在每个用例前 `memory_cache.clear()`，规避全局单例跨用例串号；并确认 key 含 `db_tag`，不同 `db_path` 实例互不影响。
- 接入测试（更新既有）：
  - `tests/test_harness_service.py` 增加 1 个用例：连续调用 `format_for_prompt` 100 次后，SQLite `sqlite3.connect` 调用次数 == 1（依赖上面的 clear fixture，否则会被前序用例预热而 flaky）。
  - `service_knowledge_service`：`upsert_relation` 后 `lookup` 不再返回 stale relations（放行条件 3）。
  - recall 缓存决策（放行条件 4）：若采方案 B，验证缓存命中时 `hit_count` 仍递增；若方案 A，验证 recall 不进缓存。
  - `tests/test_*memory*`（如未来新增）确认 `update / upsert` 后缓存被失效。
- 基准：`scripts/bench_memory_cache.py` 在 500 / 5000 行下输出 recall/lookup/format_for_prompt 的 P50 / P95；目标 ≥ 5× 加速（缓存命中时）。
- 端到端：灰度分支 `feature/memory-cache-layer`，手测一次对话（多步取证 + recall_experience + lookup_service_knowledge）无回归。

## 6. 回滚与降级

- **代码回滚**：本次改动集中在 1 个新文件 + 3 个 service 各加几行 + 1 个新测试文件，可一键 Revert。
- **运行时降级**：`MEMORY_CACHE_ENABLED=0` 让 `memory_cache.get/set` 全部 passthrough，立即回到现状。
- **写路径不受影响**：所有 invalidate 失败都仅 warning，不会阻断 SQLite 写入。

## 7. 风险与阻塞

- **B1**：P0 决策未拍板（TTL 默认值、是否暴露 `/admin/memory-cache-stats`、是否纳入 P0 治理 `experience_memory_index_service._local_store`）。
- **R1**：`format_for_prompt` 的拼装依赖 `preference` 字段全量；若有新增字段且忘了同步更新 key，可能短暂读到旧 prompt。建议在 `UserPreferenceService.upsert` 里 `invalidate_prefix(f"memory:user_pref:")` 而不是单 key。
- **R2**：`experience_memory_service.recall` 缓存 key 用 `hash(query)`，但 `query` 可能在 harness 主循环里被改写。需在 `_recall_from_sqlite` / Milvus 召回路径都使用**原始字符串**的稳定归一化结果（`_normalize(query)`）作为 hash 输入。
- **R3**：LRU + TTL 在低 QPS 下命中率偏低；如发现 `hit_ratio < 0.3`，先观察再决定是否上调 TTL（不要盲目延 TTL 防 stale）。
- **R4**：`_find_merge_target` 的全表扫是**写路径**，缓存不能直接帮忙，但缓存 `_list(limit=1000)` 结果能减少重复扫表 → 写入路径也获益。
- **R5**：未来引入 Redis 后，本设计的 key 命名约定要保持稳定（仅前缀 + 序列化协议变），否则会出现双层缓存不一致。

## 8. 待用户授权方可进入实现

按 CLAUDE.md，本 Plan 仅做规划与风险说明。需在用户明确授权后，才进入 P1 实施阶段。

## 9. 待确认项（P0 评审）与评审建议

| # | 待确认项 | 评审建议 |
| --- | --- | --- |
| 1 | TTL 默认值（偏好 5 min / 经验 60 s / 服务知识 60 s）是否接受？ | **接受**。已有全量显式失效，TTL 只作兜底，不靠它保证一致性，勿盲目延长。 |
| 2 | 是否在 `/admin/memory-cache-stats` 暴露命中率？ | **要，但不在 P1**。先把 stats 挂到既有 `/health`，避免新开鉴权路由的 scope creep。 |
| 3 | 是否在 P0 一并治理 `_local_store`（加 TTL + 失效）？ | **不要**。保持出范围，单独排期。 |
| 4 | 缓存容量 1024 是否合适？ | **合适**。 |
| 5 | 是否在 P1 顺带做连接池（SQLite 长连接）？ | **不要**。连接池是 R-risk-1 的独立问题，耦合进来会放大爆炸半径、破坏"一键 Revert"承诺。P0 只做缓存层。 |

放行条件（必须落地后方可进入实现）见 §3.7。