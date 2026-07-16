# 过程栏关键结果乱码与符号噪声修复进度

**日期：** 2026-07-16  
**状态：** 已实现并验证

## 已完成

- 在 `app/tools/recall_experience.py` 增加历史经验字段清洗：
  - 过滤私用区字符、已知中文乱码片段和内部工具协议行；
  - 清除引用、列表、标题等 Markdown 控制前缀；
  - 症状只保留第一条有效行，避免历史回答尾部污染后续上下文；
  - 字段清洗为空时显示“未提供”，工具返回类型仍保持 `str`。
- 在 `frontend/src/components/agent-process/processContent.ts` 增加 `recall_experience` 专用解析：
  - 将原始文本转换为召回摘要、经验 ID、置信度、相似度；
  - 分项显示症状、历史根因、处置建议和证据摘要；
  - 支持无命中、反模式和多条经验，最多结构化展示三条；
  - 对已经保存的旧事件同样动态过滤乱码和未知续行，无需迁移数据库。
- 新增后端和前端回归测试，覆盖截图中的乱码、Markdown 噪声、内部协议、无命中和反模式场景。

## 验证结果

- 后端：`.venv\Scripts\python.exe -m pytest tests/test_recall_experience_output.py tests/test_harness_output_safety.py tests/test_harness_service.py -q`
  - 结果：76 passed。
  - 现有测试仍报告 SQLite 连接资源警告和 PyMilvus 弃用警告，本次改动未新增失败。
- 前端：`npm test`
  - 结果：13 个测试文件、76 项测试全部通过。
- 前端：`npm run build`
  - 结果：生产构建成功。
  - 保留现有 `httpClient.ts` 动态与静态导入并存警告，不影响构建。
- 浏览器 QA：`http://127.0.0.1:5173/`
  - 页面标题和登录页正常，页面非空，无框架错误层；
  - 输入用户名和密码后登录按钮正常启用；
  - 控制台无 error/warn；
  - 当前浏览器没有登录会话，因此未直接打开历史聊天中的过程栏，目标展示由组件测试验证。

## 数据与兼容性

- 未执行数据库迁移，也未批量修改 `experience_memories` 历史数据。
- 新召回结果在后端出口清洗；旧时间线事件在前端展示时清洗。
- 普通工具结果继续使用原有通用展示逻辑。

## 环境限制

- 当前工作区的 `.git` 指针指向不存在的 worktree 元数据目录，无法运行 `git status` 或创建新的 Git worktree。本次仅修改计划范围内文件，未尝试修复 Git 元数据。
