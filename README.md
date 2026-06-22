# OnCall Agent

> 面向运维值班场景的智能 OnCall 助手，提供实时对话、故障诊断、知识库检索、服务基线和长期经验记忆能力。

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-green.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-blue.svg)](https://react.dev/)
[![Milvus](https://img.shields.io/badge/Milvus-vector%20db-orange.svg)](https://milvus.io/)

## 核心能力

- 统一助手入口：`/api/assistant` 使用 SSE 流式返回路由、工具调用、诊断过程和最终回答。
- 多智能体 Harness：支持规划、上下文准备、专家路由、子任务执行、证据自检和降级收尾。
- 专家能力：知识库问答、指标/告警分析、日志分析、变更排查和综合诊断。
- RAG 知识库：支持文档上传、目录索引、Milvus 向量检索和混合检索配置。
- 长期记忆：沉淀诊断经验、服务知识、指标基线和用户偏好。
- Web 控制台：React + Vite 前端，包含登录、会话历史、实时过程面板和服务基线管理。
- MCP 集成：可接入日志查询和监控数据服务。

## 技术栈

- 后端：FastAPI、Pydantic Settings、SSE、Loguru
- Agent：LangChain、LangGraph、自研 Harness 编排
- 模型：OpenAI-compatible LLM 接口，兼容 DashScope
- 向量库：Milvus
- 前端：React 18、Vite、Vitest、TypeScript
- 数据：SQLite 本地会话/记忆库，Milvus 向量索引

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/wangjialin2004/Oncall-agent.git
cd Oncall-agent
```

### 2. 安装后端依赖

推荐使用 Python 3.11+。

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux/macOS
source .venv/bin/activate

pip install -e ".[dev]"
```

如果本机安装了 `uv`，也可以使用：

```bash
uv venv
uv pip install -e ".[dev]"
```

### 3. 配置本地环境变量

真实密钥只写入本地 `.env`，不要提交 `.env`。

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env.example` 是纯注释模板。使用时在 `.env` 中取消需要的行注释，并填入本地真实值，例如：

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
LLM_API_KEY=replace-with-local-llm-api-key
LLM_MODEL=your-model

DASHSCOPE_API_KEY=replace-with-local-dashscope-api-key
AUTH_TOKEN_SECRET=replace-with-a-local-random-secret
```

说明：

- `.env`、`.env.local`、`.env.*` 已被 `.gitignore` 忽略。
- `.env.example` 只提交占位示例，所有配置行默认注释。
- 日志、运行输出、Playwright 快照和本地数据库目录不会提交。

### 4. 启动 Milvus

```bash
docker compose -f vector-database.yml up -d
```

### 5. 启动后端

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900 --reload
```

访问：

- API: http://localhost:9900
- API 文档: http://localhost:9900/docs
- 健康检查: http://localhost:9900/health

### 6. 启动前端开发服务

```bash
cd frontend
npm install
npm run dev
```

前端默认访问 http://localhost:5173，并通过 Vite proxy 调用 `http://localhost:9900/api/*`。

### 7. 可选：启动 MCP 服务

```bash
python mcp_servers/cls_server.py
python mcp_servers/monitor_server.py
```

默认地址在 `.env.example` 中有示例：

- CLS MCP: `http://localhost:8003/mcp`
- Monitor MCP: `http://localhost:8004/mcp`

## Windows 一键脚本

项目保留了 Windows 批处理脚本：

```powershell
.\start-windows.bat
.\stop-windows.bat
```

脚本会在本地生成 `.log` 和 `.pid` 文件，这些文件已被 `.gitignore` 忽略。

## 常用命令

```bash
# 后端测试
python -m pytest

# 前端测试
cd frontend
npm test

# 前端构建
npm run build

# Makefile 管理命令（Linux/macOS 或已安装 make 的环境）
make up
make start
make stop
make restart
make test
make lint
```

## API 概览

### 认证

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/login` | 登录并返回 Bearer token |
| POST | `/api/auth/logout` | 退出登录 |

### 统一助手

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/assistant` | SSE 流式助手入口，自动完成路由、执行和回答 |

请求示例：

```bash
curl -N -X POST "http://localhost:9900/api/assistant" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"Id":"session-123","Question":"帮我诊断 checkout-api 的 CPU 告警"}'
```

SSE `message` 数据会包含不同类型的事件，例如：

- `route_selected`
- `agent_event`
- `tool_event`
- `decision_event`
- `content`
- `complete`
- `error`

### 会话历史

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/conversations` | 查询当前用户的会话列表 |
| GET | `/api/conversations/{session_id}` | 恢复指定会话 |
| DELETE | `/api/conversations/{session_id}` | 删除指定会话 |

### 文件和知识库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/upload` | 上传文件并创建向量索引 |
| POST | `/api/index_directory` | 索引受信目录内的文件 |

### 长期记忆和服务知识

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/memory/feedback` | 从用户反馈沉淀经验 |
| POST | `/api/memory/experiences` | 手动创建经验 |
| GET | `/api/memory/experiences` | 查询经验列表 |
| PATCH | `/api/memory/experiences/{experience_id}` | 更新经验状态或置信度 |
| POST | `/api/memory/experiences/rebuild-index` | 重建经验向量索引 |
| GET | `/api/memory/services` | 查询服务知识 |
| PUT | `/api/memory/services/{service_name}` | 写入服务知识 |
| PUT | `/api/memory/services/{service_name}/baselines` | 写入服务指标基线 |
| GET | `/api/memory/preferences` | 查询用户偏好 |
| PUT | `/api/memory/preferences` | 更新用户偏好 |

## 项目结构

```text
.
├── app/
│   ├── api/                 # FastAPI 路由
│   ├── agent/               # Agent 事件、专家、Harness 编排
│   ├── core/                # LLM、Milvus、指标和运行时工具
│   ├── models/              # Pydantic 请求/响应/记忆模型
│   ├── services/            # 会话、记忆、知识库、路由和向量服务
│   ├── tools/               # Agent 可调用工具
│   ├── utils/               # 日志、文本、时间、序列化工具
│   ├── config.py            # Pydantic Settings 配置入口
│   └── main.py              # FastAPI 应用入口
├── frontend/
│   ├── src/                 # React 应用
│   ├── package.json
│   └── vite.config.ts
├── mcp_servers/             # CLS / Monitor MCP 服务
├── aiops-docs/              # 示例知识库文档
├── scripts/                 # 调试、评测和索引脚本
├── tests/                   # 后端测试
├── docs/                    # 评审和设计文档
├── plan/                    # 实施计划文档
├── .env.example             # 注释模板，不包含真实密钥
├── .gitignore               # 忽略本地密钥、日志和运行产物
├── pyproject.toml
├── uv.lock
└── vector-database.yml
```

## 配置项重点

| 变量 | 用途 |
| --- | --- |
| `LLM_PROVIDER` | LLM 提供方，支持 `openai` / `azure` / `custom` |
| `LLM_BASE_URL` | OpenAI-compatible API 地址 |
| `LLM_API_KEY` | LLM API Key，只写入 `.env` |
| `LLM_MODEL` | 对话和诊断模型 |
| `DASHSCOPE_API_KEY` | DashScope API Key，用于兼容或 embedding |
| `DASHSCOPE_EMBEDDING_MODEL` | 向量模型 |
| `MILVUS_HOST` / `MILVUS_PORT` | Milvus 地址 |
| `HARNESS_ENABLED` | 是否启用统一 Harness 主循环 |
| `HARNESS_MCP_ENABLED` | 是否允许 Harness 调用 MCP 工具 |
| `PROMETHEUS_BASE_URL` | Prometheus 地址 |
| `AUTH_TOKEN_SECRET` | 本地 token 签名密钥，生产环境必须覆盖 |

完整示例见 `.env.example`。

## 安全和提交规则

- 不要提交 `.env`、`.env.local` 或任何真实密钥。
- 不要提交 `logs/`、`*.log`、`*.pid`、`output/`、`.playwright-*`。
- 如果真实 key 曾经进入提交历史，应立即去服务商后台吊销并重新生成。
- `.env.example` 保持全注释，只作为复制到 `.env` 后手动启用的模板。

## 故障排查

### API Key 未配置

```bash
# Linux/macOS
grep -E "^(LLM_API_KEY|DASHSCOPE_API_KEY)=" .env

# Windows PowerShell
Select-String -Path .env -Pattern "^(LLM_API_KEY|DASHSCOPE_API_KEY)="
```

### Milvus 连接失败

```bash
docker ps
docker compose -f vector-database.yml restart
```

### 端口被占用

```powershell
netstat -ano | findstr :9900
taskkill /F /PID <PID>
```

### 查看日志

```bash
tail -f logs/app_$(date +%Y-%m-%d).log
```

Windows PowerShell:

```powershell
Get-ChildItem logs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content -Tail 80
```

## 许可证

MIT License
