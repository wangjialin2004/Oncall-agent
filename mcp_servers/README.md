# MCP Servers

为 AIOps 智能诊断提供日志查询和监控数据工具。

## 📚 服务列表

### CLS Server (`cls_server.py`)
**日志查询服务** - 端口 8003

**核心工具：**
- `get_current_timestamp` - 获取当前时间戳
- `get_topic_info_by_name` - 查询日志主题
- `search_log` - 日志搜索
- `search_service_logs` - 服务日志查询（支持级别筛选）
- `analyze_log_pattern` - 日志模式分析

### Monitor Server (`monitor_server.py`)
**监控数据服务** - 端口 8004

**核心工具：**
- `query_cpu_metrics` - CPU 使用率查询（时间序列，百分比）
- `query_memory_metrics` - 内存使用查询（时间序列，百分比）
- `query_prometheus_range` - 任意 PromQL 区间查询（延迟/错误率/QPS 等，仅 prometheus 模式可用）
- `get_local_resource_usage` / `get_disk_usage` / `get_python_processes` - 本机资源与进程
- `get_service_ports_status` / `check_api_health` / `check_mcp_health` / `check_milvus_health` - 端口/健康检查

> 指标数据源由环境变量 `MONITOR_TARGET_MODE` 切换：`self`/`local`（默认，本机 psutil 快照）或
> `prometheus`（走真实 PromQL）。详见下方「Prometheus 数据源」。

## 🚀 快速开始

### 安装依赖
```bash
pip install fastmcp
```

### 启动服务

**方式一：使用 Makefile（推荐）**
```bash
make mcp-start   # 启动所有 MCP 服务
make mcp-stop    # 停止所有 MCP 服务
make mcp-status  # 查看服务状态
```

**方式二：手动启动**
```bash
python mcp_servers/cls_server.py
python mcp_servers/monitor_server.py
```

## 💡 使用示例

### AIOps 诊断场景

```
用户: data-sync-service 出现告警，请排查

Agent 自动执行:
1. list_all_services() → 查看所有服务状态
2. get_service_info("data-sync-service") → 获取服务详情
3. query_cpu_metrics("data-sync-service") → CPU 趋势分析
4. search_service_logs("data-sync-service", level="error") → 错误日志
5. analyze_log_pattern("data-sync-service") → 日志模式分析
6. search_historical_tickets(service_name="data-sync-service") → 历史工单
7. 综合分析 → 生成诊断报告和修复建议
```

### 工具参数示例

**查询 CPU 指标：**
```python
query_cpu_metrics(
    service_name="data-sync-service",
    start_time="2024-02-14 02:00:00",
    interval="1m"
)
```

**搜索错误日志：**
```python
search_service_logs(
    service_name="data-sync-service",
    log_level="error",
    keyword="timeout",
    limit=100
)
```

**搜索历史工单：**
```python
search_historical_tickets(
    service_name="data-sync-service",
    issue_type="cpu",
    limit=10
)
```

## 🔧 高级配置

### 接入真实 API

当前使用本地数据适配实现。接入真实 API 步骤：

**腾讯云 CLS：**
```bash
# 安装 SDK
pip install tencentcloud-sdk-python

# 配置环境变量
export TENCENTCLOUD_SECRET_ID="your-id"
export TENCENTCLOUD_SECRET_KEY="your-key"

# 在 cls_server.py 中集成
from tencentcloud.cls.v20201016 import cls_client
```

### Prometheus 数据源

把 monitor MCP 的指标工具从本机快照切换为真实 Prometheus（PromQL）。本仓库已自带一条
**开箱即用**的链路：应用 `/metrics` → Prometheus 抓取 → monitor MCP 查询。

**端到端跑通（推荐顺序）：**

```bash
# 1) 启动应用（在宿主机 9900 暴露 /metrics，由 app/core/metrics.py 提供）
make start            # 或 python -m app.main
curl -s localhost:9900/metrics | grep app_cpu_usage_percent   # 自检

# 2) 启动 Prometheus（容器，抓取宿主机 /metrics；配置见 deploy/prometheus/）
make start-prometheus            # = docker compose -f monitoring.yml up -d
# 打开 http://localhost:9090/targets 确认 aiops-assistant-api 为 UP

# 3) 让 monitor MCP 走真实 PromQL，并重启它
export MONITOR_TARGET_MODE=prometheus
make stop-monitor && make start-monitor
```

应用暴露的指标：`app_cpu_usage_percent` / `app_memory_usage_percent`（0-100 Gauge）、
`http_request_duration_seconds`（请求耗时直方图）。默认 PromQL 模板已对齐这些指标，**无需额外配置**。

**接入你自己的 exporter（覆盖默认模板）：**

```bash
export PROMETHEUS_BASE_URL=http://your-prometheus:9090
export PROMETHEUS_REQUEST_TIMEOUT=10          # 秒，可选
# {service} 替换为入参 service_name，{range} 替换为速率窗口；模板应聚合为「单条、0-100 百分比」序列
export PROMETHEUS_RATE_WINDOW='5m'
export PROMETHEUS_CPU_QUERY='app_cpu_usage_percent{service="{service}"}'
export PROMETHEUS_MEMORY_QUERY='app_memory_usage_percent{service="{service}"}'
```

- 以上变量可写入项目根 `.env`，monitor MCP 启动时自动加载（依赖 `python-dotenv`）。
- 延迟分位/错误率/QPS 等任意指标无需建模板，直接用 `query_prometheus_range(query=...)` 传完整 PromQL，例如：
  `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{service="aiops-assistant-api"}[5m])) by (le))`
- `MONITOR_TARGET_MODE` 同时被主应用读取（`/health` 会回显当前模式）。
- `deploy/prometheus/alerts.yml` 内置 CPU>80%/内存>70% 告警，触发后可被 `query_prometheus_alerts` 读取。

**其他监控系统：** Grafana、云监控（腾讯云/阿里云/AWS）、自建监控平台同理，可在各 Server 内新增 Provider。

### 自定义数据源

修改各 Server 文件中的数据获取逻辑，适配实际运维场景。

## 📚 参考资料

- [FastMCP 文档](https://github.com/jlowin/fastmcp)
- [MCP 协议](https://modelcontextprotocol.io/)
- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [主项目 README](../README.md)

---

**注意**: 生产环境建议接入真实日志、监控和工单 API。
