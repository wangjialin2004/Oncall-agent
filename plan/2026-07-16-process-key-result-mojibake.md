# 过程栏关键结果乱码与符号噪声修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“召回历史经验”的关键结果从带 Markdown 符号和旧乱码的原始文本，转换为清晰的摘要、经验 ID、置信度、相似度与症状字段。

**Architecture:** 后端在 `recall_experience` 输出边界清洗旧经验字段，避免历史乱码再次进入模型上下文和 SSE；前端为该工具增加领域化解析器，不再把多行工具文本压成一个段落。保留现有工具返回的文本契约，避免影响 Harness 与 LLM 工具消息格式。

**Tech Stack:** Python 3.13、pytest、React 18、TypeScript、Vitest、React Testing Library。

**状态:** 已实现并验证。

---

## Context（现状与问题）

截图中的内容来自 `recall_experience` 工具：

1. `app/tools/recall_experience.py` 用 `- experience_id`、缩进字段和 `>` Markdown 拼成纯文本；
2. 历史 `experience_memories.symptoms` 保存过旧乱码回答，所以召回结果本身已经包含乱码；
3. `frontend/src/components/agent-process/processContent.ts::toolResultPresentation` 对非 JSON 工具结果调用 `text()`，把换行压成空格并截断到 220 字；
4. `ProcessTimeline` 随后将整段作为普通 `<p>` 显示，导致 `-`、`>`、字段名、乱码和省略号挤在一起。

## 设计决策

1. 不修改 `recall_experience` 的返回类型，仍返回供 LLM 使用的文本，避免工具契约变更。
2. 后端清洗每个召回字段：删除已知乱码行、内部协议行和 Markdown 控制前缀；`symptoms` 只保留首个有效症状行，避免旧回答尾部污染。
3. 前端仅对 `event.tool === "recall_experience"` 使用专用解析器；其他纯文本工具维持现状。
4. 关键结果不显示原始 Markdown：
   - 摘要：`召回 N 条历史经验，仅供参考，需以当前证据复核。`
   - 字段：经验 ID、置信度、相似度；
   - 列表：症状、历史根因、处置建议、证据摘要。
5. 不迁移数据库。旧记录在召回时动态清洗；后续新经验仍按现有持久化流程写入。
6. 不新增配置开关；这是展示正确性与上下文卫生修复。

## 范围与非目标

### 范围

- `recall_experience` 输出字段清洗；
- 过程栏“关键结果”的历史经验结构化展示；
- 单条、多条、未命中、反模式经验测试；
- 中文乱码和 Markdown 控制符回归测试。

### 非目标

- 不批量修改 `experience_memories` 数据库；
- 不改变经验召回排序、相似度算法或置信度；
- 不重做过程栏布局与配色；
- 不隐藏普通工具返回中的业务符号或 JSON。

## 文件清单

- Modify: `app/tools/recall_experience.py`
- Create: `tests/test_recall_experience_output.py`
- Modify: `frontend/src/components/agent-process/processContent.ts`
- Modify: `frontend/src/components/agent-process/__tests__/processContent.test.ts`
- Create after implementation: `plan/2026-07-16-process-key-result-mojibake-progress.md`
- Modify: `AGENTS.md`

## Task 1：清洗后端历史经验召回文本

**Files:**
- Modify: `app/tools/recall_experience.py`
- Create: `tests/test_recall_experience_output.py`

- [x] **Step 1: 写失败测试**

```python
def test_recall_experience_drops_legacy_mojibake_and_markdown_noise(monkeypatch):
    monkeypatch.setattr(experience_memory_service, "recall", lambda **_: [{
        "experience_id": "exp-1",
        "confidence": 0.8,
        "similarity": 0.6,
        "symptoms": "payment-service 内存超过 85%\n> 鈿狅笍 璇佹嵁鑷检 medium",
        "root_cause": "CPU 使用率过高告警",
        "resolution": "补充实时证据",
        "evidence_summary": "历史只读诊断",
    }])
    result = _recall_experience("OOM")
    assert "鈿" not in result
    assert "璇佹嵁" not in result
    assert "symptoms: payment-service 内存超过 85%" in result
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recall_experience_output.py -q`

Expected: FAIL，当前输出保留乱码行。

- [x] **Step 3: 实现字段清洗函数**

在 `recall_experience.py` 内新增：

```python
def _clean_recalled_text(value: object, *, first_line_only: bool = False) -> str:
    """Return readable recalled text without legacy protocol or mojibake lines."""
```

行为：

- 按行处理，过滤包含私用区字符、`鈿`、`璇佹嵁`、`to=multi_tool_use`、`tool_uses` 的行；
- 清除行首 `> -#` 控制前缀并合并空白；
- `symptoms` 使用 `first_line_only=True`；
- 清洗为空时回退为 `未提供`，不抛异常。

- [x] **Step 4: 运行后端测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recall_experience_output.py tests/test_harness_output_safety.py -q`

Expected: PASS。

## Task 2：把关键结果解析为业务字段

**Files:**
- Modify: `frontend/src/components/agent-process/processContent.ts`
- Modify: `frontend/src/components/agent-process/__tests__/processContent.test.ts`

- [x] **Step 1: 写前端失败测试**

```ts
it("presents recalled experience as structured readable fields", () => {
  const result = section(recallEvent, "result");
  expect(result.summary).toBe("召回 1 条历史经验，仅供参考，需以当前证据复核。");
  expect(result.fields).toContainEqual({ label: "经验 ID", value: "exp-1" });
  expect(result.fields).toContainEqual({ label: "置信度", value: "0.80" });
  expect(result.items).toContain("症状：payment-service 内存超过 85%");
  expect(JSON.stringify(result)).not.toMatch(/鈿|璇佹嵁|>\s|^-\s/m);
});
```

- [x] **Step 2: 运行测试确认失败**

Run: `npm test -- --run src/components/agent-process/__tests__/processContent.test.ts`

Workdir: `frontend`

Expected: FAIL，当前 `result.summary` 是被压成一行的原始文本。

- [x] **Step 3: 实现专用解析器**

在 `processContent.ts` 增加：

```ts
function recallExperiencePresentation(value: unknown): {
  summary: string;
  fields: ProcessDetailField[];
  items: string[];
} | null
```

解析 `experience_id/confidence/similarity/symptoms/verified_root_cause/effective_resolution/evidence_summary`；未知行不进入摘要，多个经验最多展示 3 条，剩余数量沿用 `limitItems`。

- [x] **Step 4: 接入工具结果分支**

在 `toolResultPresentation` 解析通用 JSON 前先处理：

```ts
if (event.tool === "recall_experience") {
  const recalled = recallExperiencePresentation(payload.result);
  if (recalled) return recalled;
}
```

- [x] **Step 5: 运行前端定向测试确认通过**

Run: `npm test -- --run src/components/agent-process/__tests__/processContent.test.ts`

Workdir: `frontend`

Expected: PASS。

## Task 3：全量回归与页面验证

**Files:**
- Create: `plan/2026-07-16-process-key-result-mojibake-progress.md`
- Modify: `AGENTS.md`

- [x] **Step 1: 后端回归**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recall_experience_output.py tests/test_harness_output_safety.py tests/test_harness_service.py -q`

Expected: PASS。

- [x] **Step 2: 前端全量测试与构建**

Run: `npm test`

Run: `npm run build`

Workdir: `frontend`

Expected: 全部 PASS，production build 成功。

- [x] **Step 3: 浏览器 QA**

The flow under test is: 登录聊天页 -> 打开包含历史经验召回的步骤 -> 展开关键结果 -> 看到结构化字段且无乱码、`>`、`-` 原始控制符。

检查页面身份、非空页面、错误层、console、截图和展开交互；若 Browser 无登录会话，记录限制并以组件测试作为目标渲染证据。

- [x] **Step 4: 更新进度与索引**

记录测试结果、浏览器限制、未迁移数据库的说明，并把 `AGENTS.md` 状态更新为已实现。

## 验收标准

1. “关键结果”不再显示乱码字符；
2. 不再把 `> - experience_id ...` 原始 Markdown 压成一行；
3. 经验 ID、置信度、相似度、症状使用独立字段/列表展示；
4. 历史经验“仅供参考”提示保留；
5. 召回工具返回给 LLM 的文本也不含已知旧乱码；
6. 普通工具结果展示无回退；
7. 后端测试、前端测试与 build 通过。

## 风险与回滚

- **误删有效内容：** 只过滤明确乱码/协议行；症状保留首个有效行，其他字段保留所有有效行。
- **旧经验字段不完整：** 缺失字段不显示，摘要仍给出召回数量。
- **工具契约风险：** 返回类型保持 `str`，只改善字段内容。
- **回滚：** 回退本次代码即可；没有数据库迁移与配置变更。
