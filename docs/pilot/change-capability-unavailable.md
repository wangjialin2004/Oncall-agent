# 变更能力状态说明（正式决策 · M2 W7）

- 初版日期：2026-07-12（L1 试点 D2）
- **正式决策日期**：2026-07-14（M2 W7 / 路线图 WP-C4）
- **决策**：**选项 B — 永久降权 / 明确不可用**（直至独立 epic 接入只读源）

---

## 1. 决策结论

| 选项 | 含义 | 结果 |
|---|---|---|
| A | 接入最小只读变更源 | ❌ 本阶段不做（无 CI/CD / CMDB / 工单凭证与优先级） |
| **B** | 确认不做真源，产品与工具统一 gap | ✅ **采纳** |

禁止再将变更能力标为「骨架但可能有数据」。在另开 epic 之前：

- `CHANGE_SOURCE_AVAILABLE = False`（硬编码）
- `CHANGE_SOURCE_POLICY=unavailable`（默认；`future` 仅表示路线图预留，**仍无数据**）
- 工具 `query_recent_changes` 始终返回 `gap=missing_change_datasource`
- **禁止编造**版本号、操作人、发布时间

---

## 2. 现状

| 项 | 值 |
|---|---|
| 工具 | `app/tools/change_tool.py` → `query_recent_changes` |
| 专家 | `app/agent/experts/change.py` |
| 标志 | `CHANGE_SOURCE_AVAILABLE = False` |
| 策略 env | `CHANGE_SOURCE_POLICY=unavailable` |
| 行为 | 结构化 `source_available=false` + 明确 message |

---

## 3. 产品含义

- change 路由可进入，结论必须声明「变更证据缺失」
- diagnosis 宽工具集可含 `query_recent_changes`，但结果只能当 **缺口证据**
- 评测 N6 / R3 / S4 以「缺口声明 / 不编造」为通过标准
- 变更 **不是** L1/L1.5/L2 主证据路径

---

## 4. 未来若做选项 A（另开 epic）

1. 实现 `_query_recent_changes` 真实集成（只读）
2. 将 `CHANGE_SOURCE_AVAILABLE` 改为 `True`（仅代码评审后）
3. `CHANGE_SOURCE_POLICY` 扩展或删除
4. 更新本说明、runbook、评测 expected
5. 安全：仍禁止写操作 / 自动回滚

---

## 5. 相关文档

- [M2 W7 计划](../../plan/2026-07-14-m2-w7-shared-kernel-change-decision.md)
- [3 个月路线图 WP-C4](../../plan/2026-07-13-complete-agent-system-3-month-roadmap.md)
- 评测：`N6-change-missing`、`R3-change-recent`
