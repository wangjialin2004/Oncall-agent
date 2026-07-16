# Completion Review Report: 路由层关键词分级 + 弱词进语义

- Review date: 2026-06-21
- Review scope: `app/services/router_service.py`、`app/config.py`、`tests/test_harness_service.py`、`app/agent/harness/loop.py`（路由调用点），对照 `plan/2026-06-21-router-keyword-tiering.md`
- Source material: 计划文档 `plan/2026-06-21-router-keyword-tiering.md`；用户请求"检查完成状态"
- Verification commands:
  - `pytest tests/test_harness_service.py -k router --no-cov` → **5 passed, 22 deselected**
  - `pytest tests/test_harness_service.py --no-cov` → **27 passed**
  - `git status --short` → `M app/config.py`、`M app/services/router_service.py`、`?? tests/test_harness_service.py`
  - 仓库级 grep `matched_` / `route_reason` 下游硬匹配排查

## Overall Conclusion

**结论：已完成（功能与测试层面），但带一个需签字确认的判断项——可视为"条件性完成"。**

计划方向 A（关键词分级 + 弱词进语义）已按阶段 1–3 完整落地：新增 `STRONG_KEYWORDS` / `WEAK_KEYWORDS` 两层词表与派生 `KEYWORDS`、`RouteDecision.hints` 字段、`router_keyword_tiering_enabled` 回滚开关；`route_message` 改为"仅单一强类别命中才短路"，弱/冲突命中携带 `hints` 进入语义层，并把提示注入 LLM prompt；短路门控由 `matched_` 收紧为 `matched_strong_`。5 个新增/重定义的路由用例全部通过，整文件 27 用例全绿。修复同时覆盖旧 `RouterService` 路径与 harness 路径（[loop.py:101](../app/agent/harness/loop.py#L101) 复用 `_resolve_route`），活跃路径无遗漏。`reason` 前缀变更经核查无任何下游硬匹配依赖（仅作 `route_reason` 透传展示）。

唯一实质性遗留：计划中被标为**阻塞前置、需领域签字**的"阶段 0 强/弱词表划分"，实现里被直接定稿，且划分与计划候选**有明显偏差**——`cpu/内存/memory/磁盘/disk/延迟/耗时/负载/端口` 被归入 WEAK（计划候选建议归 STRONG）。这把一批常见且歧义较低的指标查询推向了 LLM 语义层，与本次"降延迟为主"的既定基调存在张力。功能正确，但该取舍应在验收前由领域同学确认，否则可能在线上放大延迟。

## Findings

### P2 - 强/弱词划分把常见指标词降级为弱词，与"降延迟为主"目标相悖，且未见签字

- Evidence:
  - 实现：[router_service.py:88-92](../app/services/router_service.py#L88-L92) 将 `cpu 内存 memory 磁盘 disk 延迟 耗时 资源 负载 端口` 等归入 `WEAK_KEYWORDS["metric"]`；强词仅保留 `告警 报警 指标 监控 错误率 qps prometheus 水位`（[router_service.py:69-72](../app/services/router_service.py#L69-L72)）。
  - 计划候选（待签字）建议 metric 强词包含 `cpu 内存 memory 磁盘 disk 延迟 耗时 负载 端口`：见 `plan/2026-06-21-router-keyword-tiering.md` 阶段 0。
  - 后果示例：`cpu 飙升`、`磁盘满了`、`内存泄漏` 这类几乎无歧义的指标查询，现在不再短路、改走 LLM 语义层。
- Impact: 本次基调是"降延迟为主"。把高频指标词降级为弱词会让相当比例的典型 metric 查询每次都付出一次 LLM 调用延迟，方向上与目标冲突。划分还存在内部不一致（`水位` 强、`负载` 弱；`错误率` 强、`延迟/耗时` 弱），口径需统一。
- Recommendation: 由领域同学对强/弱词表逐词复核签字；优先把"几乎无歧义"的指标词（`内存/磁盘/延迟/负载/端口` 等）回归 STRONG，仅保留真正跨域歧义词（如 `cpu` 兼指概念、`资源/状态/健康/检查`）为 WEAK。无需改逻辑，只调词表常量即可。
- Verification: 调整后跑 `pytest tests/test_harness_service.py -k router`，并人工抽样 `cpu 飙升`/`磁盘满了`/`内存泄漏` 应回到强词短路（`matched_strong_metric_keyword`）。

### P3 - 短路要求 `len(hints)==1`，任一跨类别弱词都会取消强词短路

- Evidence: [router_service.py:176](../app/services/router_service.py#L176) 短路条件为 `len(strong_matched) == 1 and len(hints) == 1`。即出现任意一个**其他类别**的弱词即 `len(hints) > 1`，强单类命中也被打回语义层。
- Impact: 例如 `prometheus 指标 为什么这么高`——已有强 metric 信号，却因 `为什么`（diagnosis 弱词）被推向 LLM。延迟优先场景下可能过度转语义。属设计取舍，非缺陷。
- Recommendation: 可考虑放宽为"仅当存在第二个**强**类别冲突时才放弃短路"，弱词只作 hint 不否决强短路；是否调整取决于线上对延迟/准确率的实际权衡。先记录，结合 P2 一并定夺。
- Verification: 若调整，补一条用例覆盖"强词 + 跨类别弱词"应仍短路。

### P3 - 阶段 4 离线评估缺失，无改造前后误路由率的量化

- Evidence: 计划阶段 4（可选）：用标注样本对比误路由率与"进入语义比例"。仓库未见相关脚本或样本集。
- Impact: 当前完成度基于单元测试与逻辑推演，缺乏对真实分布的量化佐证，词表调参缺少数据支撑。
- Recommendation: 若有/可建标注样本，补一个轻量离线评估；否则以人工抽样替代并在验收记录中说明。
- Verification: 评估脚本产出 before/after 指标对比。

### P3 - 回滚（关闭分级）路径相比纯旧行为多透传了 hints

- Evidence: 关闭开关时 [router_service.py:168-169](../app/services/router_service.py#L168-L169) 的 `ambiguous_keywords` 仍带 `hints=tuple(matched)`，并经 [router_service.py:266](../app/services/router_service.py#L266) 透传给语义层；而改造前旧逻辑不向语义层传 hints。
- Impact: 极小且良性（hints 仅作软提示，只会帮助而非误导）。但严格意义上"回滚到旧行为"不是逐位等价。
- Recommendation: 可接受，作为已知差异记录即可；如需严格等价，关闭分级时不附带 hints。
- Verification: 无需额外动作。

## Completion Matrix

| Item | Status | Notes |
| --- | --- | --- |
| 阶段1 数据结构：STRONG/WEAK/派生 KEYWORDS | Complete | [router_service.py:69-112](../app/services/router_service.py#L69-L112) |
| 阶段1 `RouteDecision.hints` 字段 | Complete | [router_service.py:50](../app/services/router_service.py#L50) |
| 阶段1 配置开关 `router_keyword_tiering_enabled` | Complete | [config.py:49](../app/config.py#L49)，默认 True |
| 阶段2 `route_message` 分级逻辑（强单命中短路 / 弱+冲突进语义携 hints） | Complete | [router_service.py:172-185](../app/services/router_service.py#L172-L185) |
| 阶段2 `_resolve_route` 短路收紧为 `matched_strong_` + 透传 hints | Complete | [router_service.py:261-266](../app/services/router_service.py#L261-L266) |
| 阶段2 语义层注入 hints 提示 | Complete | [router_service.py:207-211](../app/services/router_service.py#L207-L211) |
| 阶段2 注入式 semantic_router 兼容 hints（签名探测） | Complete | [router_service.py:227-237](../app/services/router_service.py#L227-L237) |
| 阶段3 重定义/新增测试 | Complete | 5 用例全过（状态弱词不短路 / 强词短路 / 强词冲突 / 提示注入 / 开关回滚） |
| 回滚降级方案（开关置 False） | Complete | `test_router_keyword_tiering_can_be_disabled` |
| 覆盖 harness 活跃路径 | Complete | [loop.py:101](../app/agent/harness/loop.py#L101) 复用 `_resolve_route` |
| `reason` 前缀外溢核查 | Complete | 仅 `route_reason` 透传，无硬匹配 |
| 阶段0 强/弱词表划分签字 | Partial | 已实现一版划分但未见签字，且偏离计划候选（见 P2） |
| 阶段4 离线误路由评估（可选） | Missing | 未做（见 P3） |

## Test And Verification Notes

- `pytest tests/test_harness_service.py -k router --no-cov` → 5 passed, 22 deselected。
- `pytest tests/test_harness_service.py --no-cov` → 27 passed（无失败、无报错）。
- 路由用例已覆盖：弱词不短路并产生 hint、强词仍短路、强词冲突走语义并携多 hint、hint 注入语义 prompt、开关关闭回退旧行为。
- 未覆盖：跨类别"强词 + 弱词"是否短路（与 P3 相关）；真实分布下的误路由率（阶段 4）。

## Next Steps

1. （P2，最高优先）领域同学对强/弱词表逐词签字；优先把 `内存/磁盘/延迟/负载/端口` 等低歧义指标词回归 STRONG，校正与"降延迟为主"的冲突。仅调词表常量，不动逻辑。
2. （P3）结合 P2 决定是否放宽短路条件，使跨类别弱词不否决强单命中；如调整则补对应用例。
3. （P3）如有标注样本，补阶段 4 离线评估，量化改造前后误路由率与转语义比例；否则以人工抽样记录替代。
4. 词表定稿后回归 `pytest tests/test_harness_service.py -k router`，并人工抽样核对典型指标/诊断/知识类查询。
