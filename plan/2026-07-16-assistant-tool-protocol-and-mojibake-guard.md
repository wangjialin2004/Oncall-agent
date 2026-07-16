# 助手消息工具协议泄漏与中文乱码修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除聊天回答中泄漏的内部工具调用 JSON/协议文本，修复用户可见中文乱码，并让已持久化的旧问题消息在前端恢复为可读内容。

**Architecture:** 后端把“带工具的模型决策回合”和“面向用户的最终回答”严格分离：决策回合内容先缓冲，只有确认它不是工具调用协议且确实是最终回答时才允许进入 SSE 与会话历史。前端增加窄范围的展示兼容层，仅清理已知协议泄漏形态和已知损坏的证据自检首行，使历史脏数据无需直接修改数据库即可正常显示。

**Tech Stack:** Python 3.12、asyncio、OpenAI-compatible SSE、pytest、React 18、TypeScript、Vitest、React Testing Library、Vite。

**状态:** 已实现；自动化验证通过，真实历史页浏览器截图受登录会话限制。详见 [进度](./2026-07-16-assistant-tool-protocol-and-mojibake-guard-progress.md)。

---

## Context（现状与问题）

截图对应的会话记录已在 `volumes/long_term_memory.db` 中定位到。该回答同时包含：

1. `app/agent/harness/events_emit.py::_apply_corrective_notice` 中已经以乱码形式写入源码的“证据自检”提示；
2. 模型以普通 `content` 返回的 `to=multi_tool_use.parallel`、`tool_uses`、`recipient_name` 等内部工具协议；
3. `app/agent/harness/stream_inner.py` 在带工具的决策回合尚未结束、尚未确认 `response.tool_calls` 前，就把 `content` 追加到用户回答并通过 SSE 发给前端；
4. 前端 `ChatWorkspace` 无条件用 Markdown 渲染持久化回答，因此历史脏数据会一直复现。

这不是字体或 CSS 问题，也不是 `TextDecoder` 的 UTF-8 流拆包问题。修复必须优先阻断后端产生新脏数据，同时兼容已有历史记录。

## 设计决策（含默认开关）

1. **决策回合内容默认不直出。** 带 `tools` 的 `_stream_chat_turn` 内容在内存中缓冲；如果最终响应包含结构化 `tool_calls`，缓冲文本只保留在内部消息上下文，不进入用户回答。
2. **文本化工具协议不执行。** 如果响应没有结构化 `tool_calls`，但内容命中内部协议标记，则视为 provider/model 格式降级，不从文本 JSON 反序列化并执行工具，避免绕过现有工具定义、参数校验和只读策略。
3. **格式降级后安全收口。** 记录一个 degraded 过程事件，然后使用现有无工具 final-answer 路径基于已经取得的证据生成干净回答。
4. **最终回答增加服务端净化。** 新增纯函数，只移除明确的内部协议片段；普通 JSON、Markdown 代码块和业务参数必须保留。
5. **前端仅做历史兼容。** 展示层调用同等窄范围的纯函数，修复旧会话中的协议片段和已知乱码首行，不修改数据库原始数据。
6. **不新增功能开关。** 这是输出安全与可读性修复，默认直接生效；回滚方式是回退本次提交。

## 范围与非目标

### 范围

- 修复证据自检、Harness 异常摘要等本次链路可触达的用户可见乱码字符串；
- 阻止 planner/step 工具决策文本进入 SSE、最终答案和会话持久化；
- 检测并隔离已知 `to=...` / `tool_uses` / `recipient_name` 文本协议；
- 前端兼容截图中已经保存的历史脏数据；
- 添加后端、前端和页面回归验证。

### 非目标

- 不解析并执行模型输出的文本化工具调用；
- 不批量迁移或重写 `volumes/long_term_memory.db`；
- 不修改 SSE 事件 `type` 枚举或工具 schema；
- 不处理普通业务 JSON 展示，不把所有 JSON 一概隐藏；
- 不重构 Harness 主循环或更换 LLM provider。

## 文件结构与职责

- Create: `app/agent/harness/output_safety.py`
  - 识别内部工具协议、净化用户可见回答、提供无副作用纯函数。
- Modify: `app/agent/harness/stream_inner.py`
  - 缓冲决策回合内容；结构化工具调用时不直出；文本协议降级时安全收口。
- Modify: `app/agent/harness/llm_turns.py`
  - 修复 token 压缩占位符乱码；保持流式客户端适配职责单一。
- Modify: `app/agent/harness/events_emit.py`
  - 修复证据自检提示乱码，并在最终事件边界应用服务端净化。
- Modify: `app/agent/context/views.py`
  - 修复上下文视图中的已知乱码省略提示，防止其再次进入模型上下文。
- Create: `tests/test_harness_output_safety.py`
  - 覆盖协议识别、普通 JSON 保留、乱码提示、决策回合不泄漏、降级收口。
- Create: `frontend/src/utils/assistantContent.ts`
  - 历史消息展示兼容，不改变原始会话对象。
- Modify: `frontend/src/components/ChatWorkspace.tsx`
  - Markdown 渲染前调用展示净化函数。
- Create: `frontend/src/utils/__tests__/assistantContent.test.ts`
  - 覆盖截图形态、普通 Markdown/JSON、分段协议和旧乱码提示。
- Modify: `frontend/src/components/__tests__/ChatWorkspace.test.tsx`
  - 验证组件不显示协议字段且保留最终中文诊断内容。
- Create after implementation: `plan/2026-07-16-assistant-tool-protocol-and-mojibake-guard-progress.md`
  - 记录实现结果、测试命令、浏览器证据和剩余风险。
- Modify after implementation: `AGENTS.md`
  - 将本计划状态更新为已实现并链接进度文档。

## 实施步骤（可验收）

### Task 1：建立输出安全纯函数和失败测试

**Files:**
- Create: `app/agent/harness/output_safety.py`
- Create: `tests/test_harness_output_safety.py`

- [x] **Step 1: 写协议识别失败测试**

```python
def test_detects_internal_tool_protocol_without_hiding_business_json() -> None:
    leaked = '先补证。 to=multi_tool_use.parallel json\n{"tool_uses":[{"recipient_name":"functions.search_app_logs","parameters":{}}]}## 现象\n正常结论'
    assert contains_internal_tool_protocol(leaked) is True
    assert contains_internal_tool_protocol('业务返回：{"status":"ok"}') is False
```

- [x] **Step 2: 写净化失败测试**

```python
def test_sanitize_removes_protocol_block_and_keeps_final_answer() -> None:
    cleaned = sanitize_user_visible_answer(LEAKED_PAYMENT_SERVICE_ANSWER)
    assert "to=multi_tool_use.parallel" not in cleaned
    assert "tool_uses" not in cleaned
    assert "recipient_name" not in cleaned
    assert "## 现象" in cleaned
    assert "payment-service" in cleaned
```

- [x] **Step 3: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_harness_output_safety.py -q`

Expected: FAIL，原因是 `output_safety` 尚不存在。

- [x] **Step 4: 实现最小纯函数**

`output_safety.py` 提供：

```python
def contains_internal_tool_protocol(text: str) -> bool: ...
def sanitize_user_visible_answer(text: str) -> str: ...
```

约束：

- 仅匹配 `to=multi_tool_use.parallel`、`to=functions.<name>`、`"tool_uses"` 与 `"recipient_name"` 的协议组合；
- 优先保留协议块之后的 Markdown 标题或结构化诊断正文；
- 不删除普通 JSON 代码块或业务接口返回；
- 输入为空时返回空字符串；函数不得抛出异常。

- [x] **Step 5: 运行纯函数测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_harness_output_safety.py -q`

Expected: PASS。

### Task 2：阻断 Harness 决策回合文本直出

**Files:**
- Modify: `app/agent/harness/stream_inner.py`
- Test: `tests/test_harness_output_safety.py`

- [x] **Step 1: 写结构化工具调用不泄漏测试**

构造 fake streaming client：先发送 `content="我先查一下"`，最终返回带 `tool_calls` 的 `LLMResponse`。断言：

```python
assert "我先查一下" not in visible_content
assert "我先查一下" not in complete_event["answer"]
assert executed_tool_names == ["search_app_logs"]
```

- [x] **Step 2: 写文本协议降级测试**

构造 fake streaming client：发送截图中的 `to=multi_tool_use.parallel` 与 JSON，但最终 `tool_calls=[]`。断言：

```python
assert not any("tool_uses" in chunk for chunk in visible_chunks)
assert any(event.get("stage") == "tool_protocol_degraded" for event in events)
assert final_answer == "基于现有证据生成的干净结论"
assert executed_tool_names == []
```

- [x] **Step 3: 运行定向测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_harness_output_safety.py -q`

Expected: FAIL，现有代码会把决策内容加入 `answer` 并发出 `content` SSE。

- [x] **Step 4: 修改决策回合控制流**

在 `stream_inner.py` 的 `_stream_chat_turn` 消费处：

```python
decision_parts: list[str] = []
...
if chunk:
    decision_parts.append(str(chunk))
...
decision_text = "".join(decision_parts)
```

随后按最终响应分支：

- `response.tool_calls` 非空：执行结构化工具调用，不向用户发出 `decision_text`；
- 无工具调用且命中内部协议：追加 `tool_protocol_degraded` 事件，走现有 `_stream_final_answer` 无工具收口；
- 无工具调用且为普通回答：净化后作为最终回答发出一次 `content`，并结束 step loop。

- [x] **Step 5: 运行定向测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_harness_output_safety.py -q`

Expected: PASS。

### Task 3：修复用户可见乱码并守住最终事件边界

**Files:**
- Modify: `app/agent/harness/events_emit.py`
- Modify: `app/agent/harness/llm_turns.py`
- Modify: `app/agent/harness/stream_inner.py`
- Modify: `app/agent/context/views.py`
- Test: `tests/test_harness_output_safety.py`

- [x] **Step 1: 写乱码回归测试**

```python
def test_corrective_notice_is_readable_chinese() -> None:
    answer = HarnessEventsEmitMixin._apply_corrective_notice("正文", result)
    assert "⚠️ 证据自检：置信度 medium" in answer
    assert "本次回答存在以下证据缺口，请谨慎采用" in answer
    assert not contains_known_mojibake(answer)
```

- [x] **Step 2: 替换已确认的运行时乱码字符串**

目标文本：

```text
> ⚠️ 证据自检：置信度 {confidence}，本次回答存在以下证据缺口，请谨慎采用：
[早期上下文已压缩以控制 token 预算]
…(+N more)
Harness 主循环执行出错：{exc}
```

- [x] **Step 3: 最终事件前净化**

在写入 `state.answer`、`conversation_service.append_turn` 和 `_complete_event` 前，统一调用 `sanitize_user_visible_answer`，确保新数据不会把协议片段保存进历史。

- [x] **Step 4: 运行后端定向回归**

Run: `.venv\Scripts\python.exe -m pytest tests/test_harness_output_safety.py tests/test_harness_service.py tests/test_llm_client_stream.py -q`

Expected: PASS；普通结构化工具调用、最终回答流式输出和会话保存行为不回退。

### Task 4：兼容已持久化的历史脏消息

**Files:**
- Create: `frontend/src/utils/assistantContent.ts`
- Create: `frontend/src/utils/__tests__/assistantContent.test.ts`
- Modify: `frontend/src/components/ChatWorkspace.tsx`
- Modify: `frontend/src/components/__tests__/ChatWorkspace.test.tsx`

- [x] **Step 1: 写前端纯函数失败测试**

```ts
expect(sanitizeAssistantContent(leakedHistory)).not.toContain("tool_uses");
expect(sanitizeAssistantContent(leakedHistory)).not.toContain("recipient_name");
expect(sanitizeAssistantContent(leakedHistory)).toContain("## 现象");
expect(sanitizeAssistantContent('{"status":"ok"}')).toBe('{"status":"ok"}');
```

- [x] **Step 2: 写组件失败测试**

向 `ChatWorkspace` 传入截图形态的历史 assistant content，断言页面显示 `现象` 和 `payment-service`，但找不到 `tool_uses`、`recipient_name` 与乱码首行。

- [x] **Step 3: 运行测试确认失败**

Run: `npm test -- --run src/utils/__tests__/assistantContent.test.ts src/components/__tests__/ChatWorkspace.test.tsx`

Workdir: `frontend`

Expected: FAIL，当前组件直接渲染原始字符串。

- [x] **Step 4: 实现展示兼容函数并接入 Markdown**

```tsx
<ReactMarkdown remarkPlugins={[remarkGfm]}>
  {sanitizeAssistantContent(item.content)}
</ReactMarkdown>
```

兼容函数只对 assistant content 生效，保持用户消息原样显示。

- [x] **Step 5: 运行前端测试确认通过**

Run: `npm test -- --run src/utils/__tests__/assistantContent.test.ts src/components/__tests__/ChatWorkspace.test.tsx`

Workdir: `frontend`

Expected: PASS。

### Task 5：完整回归、浏览器 QA 与进度文档

**Files:**
- Create: `plan/2026-07-16-assistant-tool-protocol-and-mojibake-guard-progress.md`
- Modify: `AGENTS.md`

- [x] **Step 1: 后端回归**

Run: `.venv\Scripts\python.exe -m pytest tests/test_harness_output_safety.py tests/test_harness_service.py tests/test_llm_client_stream.py -q`

Expected: PASS。

- [x] **Step 2: 前端全量测试与构建**

Run: `npm test`

Workdir: `frontend`

Expected: 全部 PASS。

Run: `npm run build`

Workdir: `frontend`

Expected: TypeScript 与 Vite build 成功。

- [~] **Step 3: 浏览器验证（登录页健康通过；真实历史页受登录会话限制）**

The flow under test is: 打开聊天页 -> 进入包含截图问题的历史会话或注入等价 fixture -> 消息卡片只显示可读中文诊断，不显示内部工具协议与 JSON。

按 Browser skill 检查：页面身份、非空页面、无框架错误层、控制台健康、截图证据、历史会话点击交互；桌面视口必测，移动视口在布局允许时补测。

- [x] **Step 4: 静态乱码扫描**

Run: `rg -n "鈥|鏃|鍘|璇佹嵁鑷|涓诲惊鐜" app/agent/harness app/agent/context frontend/src`

Expected: 本次范围内不再出现已确认的运行时乱码文本；注释中的历史乱码也应一并修正。

- [x] **Step 5: 写进度与索引状态**

进度文档记录：根因、改动文件、测试结果、浏览器截图、未迁移数据库的说明、回滚方法。`AGENTS.md` 将本计划标记为“已实现”，并链接进度文档。

## 开关一览

| 开关 | 默认值 | 说明 |
|---|---:|---|
| 新增开关 | 无 | 输出安全修复默认生效，不增加配置复杂度 |
| `HARNESS_PARALLEL_TOOL_CALLS` | 保持现状 | 仍控制结构化工具调用执行方式，不影响本次协议隔离 |
| `LLM_PLANNER_MODEL` / `LLM_REASONER_MODEL` | 保持现状 | 不通过更换模型掩盖输出边界问题 |

## 验证方式与出口标准

必须同时满足：

1. 截图中的 `to=multi_tool_use.parallel`、`tool_uses`、`recipient_name` 不再出现在聊天气泡；
2. 新产生的会话历史和 `complete.answer` 不包含内部协议片段；
3. 已保存的截图历史消息无需改数据库即可在前端正常显示；
4. 证据自检提示显示为可读中文；
5. 普通业务 JSON、Markdown 表格和代码块仍正常显示；
6. 结构化工具调用仍能执行，文本化工具协议不会被执行；
7. 后端定向回归、前端全量测试、前端 build 全部通过；
8. 浏览器页面无相关 console error，并有截图证明修复后的消息卡片。

## 风险与回滚

- **误删业务 JSON:** 通过“协议标记 + 工具字段组合”窄匹配，并用普通 JSON 回归测试约束。
- **降低实时感:** 只缓冲带工具的 planner/step 决策回合；真正的 `_stream_final_answer` 保持流式。
- **模型格式降级导致少执行一次工具:** 不执行文本化调用是安全边界；系统记录 degraded 事件并基于已有证据收口，优先保证不泄漏、不绕过策略。
- **旧历史形态超出已知模式:** 前端兼容函数保持保守；无法可靠识别的内容不自动删除，避免破坏用户正文。
- **回滚:** 回退本次代码提交即可；本计划不迁移数据库、不改 schema、不改 env。

## 自检

- [x] 覆盖截图中的 JSON 泄漏与中文乱码两个问题；
- [x] 同时覆盖新消息源头修复与历史消息展示兼容；
- [x] 明确不解析执行文本化工具调用；
- [x] 保留普通 JSON/Markdown；
- [x] 包含后端、前端、构建和浏览器验收；
- [x] 无 TBD/TODO 占位项。
