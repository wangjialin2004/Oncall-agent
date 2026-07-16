# 助手消息工具协议泄漏与中文乱码修复进度

> 日期：2026-07-16  
> 对应计划：[2026-07-16-assistant-tool-protocol-and-mojibake-guard.md](./2026-07-16-assistant-tool-protocol-and-mojibake-guard.md)  
> 状态：**已实现；自动化验证通过，真实历史页浏览器截图受登录会话限制**

## 完成内容

1. 新增 `app/agent/harness/output_safety.py`：
   - 识别 `to=multi_tool_use.parallel` / `to=functions.*` 内部工具协议；
   - 仅在明确协议标记存在时移除协议块；
   - 普通业务 JSON、Markdown 与 `recipient_name` 业务字段保持可见。
2. `app/agent/harness/llm_turns.py` 新增决策回合收集入口：
   - 带工具的 planner/step 文本先缓冲；
   - 最终结构化响应确认后再决定是否允许进入用户回答；
   - 安全的最终正文仍按原分块发出，保持既有流式事件契约。
3. `app/agent/harness/stream_inner.py`：
   - 结构化工具调用前的旁白不再进入 SSE/最终答案；
   - 文本化工具协议不解析、不执行；
   - 记录 `tool_protocol_degraded` 后走无工具安全收口。
4. `app/agent/harness/close_path.py`：
   - 同步覆盖 re-evidence 与 replan 两个工具决策入口；
   - verify、ContextState 写入和最终持久化前统一净化答案。
5. 修复运行时乱码：
   - 证据自检提示；
   - token 压缩占位符；
   - Harness 异常摘要；
   - Context view 省略提示。
6. 前端新增 `frontend/src/utils/assistantContent.ts`：
   - 对已经持久化的旧消息修复已知证据自检乱码首行；
   - 隐藏历史消息中的内部工具协议块；
   - 仅对 assistant Markdown 渲染生效，不修改用户消息或数据库原文。

## 计划偏差

- 原计划重点列出 `stream_inner.py`，实施时确认 `close_path.py` 的 re-evidence/replan 也独立消费 `_stream_chat_turn` 并直接推送文本，因此一并纳入修复。该扩展属于同一输出边界问题，不改变产品范围。
- 未直接迁移 `volumes/long_term_memory.db`；旧脏数据通过前端兼容层恢复可读，回滚时不会涉及数据库恢复。

## 验证结果

### 后端

```powershell
.venv\Scripts\python.exe -m pytest tests/test_harness_output_safety.py tests/test_llm_client_stream.py tests/test_harness_service.py -q
```

结果：通过；覆盖新增输出安全测试、LLM SSE 解析和现有 Harness 主回归。测试过程中仍有仓库既存的 SQLite ResourceWarning 与 PyMilvus deprecation warning，无新增失败。

```powershell
.venv\Scripts\python.exe -m py_compile app/agent/harness/output_safety.py app/agent/harness/llm_turns.py app/agent/harness/stream_inner.py app/agent/harness/close_path.py app/agent/harness/events_emit.py app/agent/context/views.py tests/test_harness_output_safety.py
```

结果：通过。

### 前端

```powershell
cd frontend
npm test
```

结果：`13` 个测试文件、`73` 项测试全部通过。

```powershell
cd frontend
npm run build
```

结果：TypeScript 与 Vite production build 通过。保留一个既存的 ineffective dynamic import 警告，不影响构建产物。

### 静态检查

- 本次范围的已知乱码特征扫描无命中；
- Harness 文件行数红线通过：`close_path.py` 894 行、`stream_inner.py` 843 行，均小于 1000；
- 新增/修改 Python 文件通过 Ruff 格式化；`stream_inner.py` 的历史 wrapper/import 结构按现状保留，定向 Ruff 检查忽略其既存 E402/F821/F401 后通过。

### 浏览器 QA

目标流：聊天页 → 打开包含截图问题的历史会话 → 只显示可读中文诊断，不显示内部工具协议。

- 页面身份：`http://127.0.0.1:5173/`，标题“智能 OnCall 运维平台”；
- 页面非空、无框架错误层；
- console error/warn：0；
- 登录表单交互：用户名/密码输入后登录按钮可用，未提交测试凭据；
- 限制：当前 in-app Browser 没有已登录会话，且强密码不应由 Agent 读取或代填，因此未进入真实历史页截图；目标消息渲染由 `assistantContent.test.ts` 与 `ChatWorkspace.test.tsx` 的截图等价 fixture 覆盖。

## 回滚

回退本次代码与前端兼容层即可。没有 schema、env 或数据库数据迁移。

## 剩余风险

1. 新出现、且不含当前明确 `to=...` 标记的其他 provider 私有协议形态不会被自动删除，这是为避免误删业务正文而保留的保守边界。
2. 真实已登录历史页尚未完成浏览器截图验证；用户登录后可补一次页面级确认。
