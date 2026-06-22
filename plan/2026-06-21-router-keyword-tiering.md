# Plan：路由层关键词分级 + 弱词进语义

- 日期：2026-06-21
- 主题：消除"单个关键词带偏路由、无法进入语义判断"
- 选定方向：A（关键词分级 + 弱词作为提示进入语义层）
- 快路径目标基调：**降延迟为主**（保留强信号词短路，只让模糊/泛化查询付出 LLM 成本）
- 状态：待用户确认后进入实现（本文件只描述如何做，不含代码改动）

---

## 1. 背景与根因（一句话）

当前 `route_message` 只要恰好命中一个关键词类别就给 `confidence=0.9`，`_resolve_route` 据此**直接短路返回，语义分类器永不被调用**；而关键词表混入大量泛化词（`状态`/`健康`/`检查`/`为什么`/`综合`…），导致单个无关词即可劫持路由且无纠偏通道。

证据定位：

- 短路点：[app/services/router_service.py:186-187](../app/services/router_service.py#L186-L187)
- 单词即 0.9：[app/services/router_service.py:130-132](../app/services/router_service.py#L130-L132)
- 泛化词表：[app/services/router_service.py:68-90](../app/services/router_service.py#L68-L90)
- 行为被测试固化：[tests/test_harness_service.py:124-128](../tests/test_harness_service.py#L124-L128)

> 注：现状中"0 命中"与"多类别命中"都会落到语义层（confidence=0.3 < 0.9）。因此**唯一被短路的就是"单关键词命中"这一条路径**，改动面天然收敛于这一处门控。

---

## 2. 目标设计（要做成什么样）

将关键词分为两层：

- **强信号词（STRONG）**：高精度、几乎无歧义地指向某路由（如 `prometheus`/`qps`/`traceback`/`回滚`/`根因`）。命中后**保留短路**，守住延迟。
- **弱/泛化词（WEAK）**：高频、跨域、易误导（如 `状态`/`健康`/`检查`/`为什么`/`综合`/`是什么`）。命中后**不再短路**，改为携带为"初判提示"进入语义层，由 LLM 定夺。

路由决策新契约：

1. 空串 / 无字母数字 → `clarify`（不变）。
2. **恰好 1 个强类别命中**（且无其他强类别冲突）→ 短路信任，`confidence=0.9`，`reason=matched_strong_<route>_keyword`。
3. **0 个或 ≥2 个强类别命中** → 不短路；收集"强∪弱"命中类别作为 `hints`，进入语义层。
4. 语义层在 prompt 中注入 `hints`（"关键词初判倾向：metric、log，仅供参考可推翻"），其余 `min_confidence` / 未知路由 / 失败回退逻辑保持不变。

效果：泛化词查询**必定进入语义判断**（解决"进不去语义"）；强信号词查询仍走快路径（守住延迟）；语义层不再"盲判"（修复已有信号被浪费的缺口）。

---

## 3. 实施阶段与关键任务

### 阶段 0：关键词分级评审（前置，需用户/领域签字）

- 关键任务：对现有 `KEYWORDS` 逐词划分 STRONG / WEAK，输出对照表并定稿。
- 候选划分（**待签字，非最终**）：
  - metric · 强：`告警 报警 指标 监控 cpu 内存 memory 磁盘 disk 错误率 qps prometheus 水位 延迟 耗时 负载 端口`；弱：`资源 状态 健康 检测 检查 服务状态 可达 存活`
  - log · 强：`日志 log 堆栈 异常栈 traceback stacktrace 错误日志 栈信息`；弱：`报错`
  - change · 强：`变更 发布 上线 部署 deploy release 回滚 rollback 配置变更 灰度`；弱：`工单`
  - knowledge · 强：`文档 知识库 定义 含义`；弱：`说明 步骤 是什么 解释 介绍`
  - diagnosis · 强：`故障 诊断 根因 宕机 全链路 全面分析 不可用`；弱：`排查 综合 为什么 挂了`
- 产出：定稿的两层词表（落到代码常量）。
- 阻塞：未定稿前不进入阶段 2。

### 阶段 1：数据结构与开关

- 关键任务：
  1. 在 `RouterService` 内新增 `STRONG_KEYWORDS` 与 `WEAK_KEYWORDS` 两个 `dict[str, tuple[str, ...]]`；保留/派生 `KEYWORDS = strong ∪ weak`，使 `_matched_categories` 与"关闭分级"回滚路径仍可用。
  2. `RouteDecision` 增加可选字段 `hints: tuple[str, ...] = ()`（dataclass `slots=True`，新增字段需检查所有构造点是否按关键字传参，避免位置参数错位）。
  3. 新增配置开关 `router_keyword_tiering_enabled: bool = True`（沿用 [app/config.py:45-49](../app/config.py#L45-L49) 的命名与注释风格；默认开启以启用修复，置 False 即回滚到旧"单词短路"行为）。
- 涉及文件：`app/services/router_service.py`、`app/config.py`。

### 阶段 2：路由逻辑改造

- 关键任务：
  1. `route_message`：分别计算 `strong_matched` 与 `weak_matched`；按第 2 节新契约返回。`router_keyword_tiering_enabled=False` 时退化为旧逻辑（单类别命中即短路）。
  2. `_resolve_route`：短路条件改为 `reason.startswith("matched_strong_")`（或等价标志位）；其余分支把 `decision.hints` 透传给语义层。
  3. `_semantic_route_message(message, hints=())`：新增可选 `hints` 参数；非空时把"初判提示"拼入 LLM 输入（建议作为追加的 user/system 备注，明确"仅供参考、可推翻"）。注入式 `semantic_router` 测试替身需保持 `hints` 可选、向后兼容。
- 涉及文件：`app/services/router_service.py`。
- 注意：`reason` 字符串前缀变化（`matched_<route>_keyword` → `matched_strong_<route>_keyword`）会经 `make_route_event` 透出到前端展示，需确认下游无字符串硬匹配依赖（已确认仓库内仅测试引用该前缀）。

### 阶段 3：测试调整与补充

- 关键任务：
  1. 重定义 [tests/test_harness_service.py:124-128](../tests/test_harness_service.py#L124-L128)：`"你现在可以检测这个服务的状态怎么样"`（`检测`/`状态` 均为弱词）→ **不再短路到 metric**；新断言应验证"未短路、携带 metric 提示、进入语义"。
  2. 新增用例：
     - 强信号单词（如 `看下 prometheus 指标`）→ 仍 `matched_strong_metric_keyword` 短路。
     - 弱信号单词（如 `看下服务状态`）→ 不短路，`hints` 含 `metric`，解析走语义。
     - 强信号冲突（如 `发布之后 cpu 飙升`）→ ≥2 强类别 → 走语义，`hints` 含 `change`、`metric`。
     - `_semantic_route_message` 收到并注入 `hints`（用桩 `llm_client` 捕获消息内容断言）。
     - 开关关闭（`router_keyword_tiering_enabled=False`）→ 回到旧短路行为。
- 涉及文件：`tests/test_harness_service.py`。

### 阶段 4（可选）：离线误路由评估

- 关键任务：用一组人工标注的代表性消息集，对比改造前后误路由率与"进入语义层比例"，量化延迟/成本变化。
- 前置：需存在或新建标注样本集；无样本则降级为人工抽样核对。

---

## 4. 依赖与前置条件

- **阶段 0 词表定稿签字**（最关键前置）。
- 确认接受**修改既有测试** [tests/test_harness_service.py:124](../tests/test_harness_service.py#L124) 的期望路由（方向 A 必然要求；待用户最终确认）。
- 确认 `router_keyword_tiering_enabled` 默认值（建议 `True`）。
- 用户明确授权解除 CLAUDE.md 限制、进入实现阶段（当前仅计划）。

---

## 5. 验证方式

- 单元测试：`pytest tests/test_harness_service.py -k router`（含新增/调整用例全绿）。
- 行为抽样：对下列消息人工核对路由结果符合预期：
  - 强词：`prometheus 指标异常`、`回滚上次发布`、`看 traceback`。
  - 弱词单击：`看下服务状态`、`这个为什么`、`帮忙检查一下` → 均应进入语义层。
  - 冲突：`发布后 cpu 飙升、帮我排查` → 语义层裁决。
- 回归：开关置 False 后，旧用例行为保持不变。
- （可选）阶段 4 离线评估指标对比。

---

## 6. 回滚 / 降级方案

- **一键回滚**：`router_keyword_tiering_enabled=False` → 路由逻辑退化为现状（单关键词短路），无需回退代码。
- 语义层异常已有兜底：[app/services/router_service.py:191-197](../app/services/router_service.py#L191-L197) 失败回退综合诊断，分级改造不削弱该兜底。
- 数据结构新增字段 `hints` 默认空，旧调用路径不受影响。

---

## 7. 风险点与阻塞项

| 级别 | 风险 | 说明 / 缓解 |
| --- | --- | --- |
| 高 | 词表划分不当 | 强词误纳泛化词 → 仍带偏；泛化词误纳强词 → 该走快路径的查询多付 LLM 延迟。靠阶段 0 评审 + 阶段 3 用例 + 阶段 4 评估收敛。 |
| 中 | 延迟回升 | 弱词查询转向语义层会增加其延迟。与"降延迟为主"存在张力——设计上通过**保留强词快路径**把新增延迟限定在"本就被误路由的模糊查询"，属预期内取舍，需向干系人说明。 |
| 中 | `reason` 前缀变更外溢 | 前端/日志若硬匹配 `matched_*` 字符串会受影响。已确认仓库内仅测试引用；上线前再核对前端展示逻辑。 |
| 低 | `RouteDecision` 加字段 | `slots=True` dataclass，需检查所有构造点用关键字传参。 |
| 阻塞 | 词表未签字 | 阻塞阶段 2/3。 |
| 阻塞 | 未授权进入实现 | 当前停留在计划层。 |

---

## 8. 假设与待确认

1. 假设可修改 [tests/test_harness_service.py:124](../tests/test_harness_service.py#L124) 的期望路由（方向 A 的必然结果）。
2. 假设 `hints` 仅作"软提示"注入语义 prompt，不改变 `min_confidence` 阈值与回退策略。
3. 待确认：阶段 0 候选词表是否直接采用，或需领域同学逐词复核。
4. 待确认：是否需要阶段 4 离线评估（取决于是否有标注样本与精度要求）。
