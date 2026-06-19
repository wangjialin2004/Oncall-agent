"""智能运维监控 MCP Server.

提供面向本项目自身运行环境的本地监控工具。
"""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import socket
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP

try:
    from dotenv import load_dotenv

    # 独立进程启动时（make start-monitor）也读取项目根 .env，
    # 以便 MONITOR_TARGET_MODE / PROMETHEUS_* 配置生效。
    load_dotenv()
except ImportError:  # pragma: no cover - dotenv 为可选依赖
    pass

try:
    import psutil
except ImportError:  # pragma: no cover - fallback for minimal installs
    psutil = None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("Monitor_MCP_Server")

mcp = FastMCP("Monitor")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONITORED_PORTS = [
    {"service_name": "aiops-assistant-api", "host": "127.0.0.1", "port": 9900},
    {"service_name": "cls-mcp-server", "host": "127.0.0.1", "port": 8003},
    {"service_name": "monitor-mcp-server", "host": "127.0.0.1", "port": 8004},
    {"service_name": "milvus", "host": "127.0.0.1", "port": 19530},
]

# ---------------------------------------------------------------------------
# 监控数据源模式（MONITOR_TARGET_MODE）
#   self / local : 用本机 psutil 快照（默认，监控助手自身运行环境）
#   prometheus   : 服务指标走真实 Prometheus PromQL（/api/v1/query_range）
# PromQL 模板用 {service} 占位服务名、{range} 占位速率窗口；通过 str.replace 注入，
# 避免与 PromQL 自身的花括号标签选择器冲突。默认模板对应本项目应用 /metrics 暴露的
# app_cpu_usage_percent / app_memory_usage_percent（见 app/core/metrics.py）；接入其它
# exporter 时按自己的指标名/标签覆盖（见 mcp_servers/README.md「Prometheus 数据源」）。
# ---------------------------------------------------------------------------
DEFAULT_PROMETHEUS_BASE_URL = "http://127.0.0.1:9090"
DEFAULT_PROMETHEUS_TIMEOUT = "10"
DEFAULT_PROMETHEUS_RATE_WINDOW = "5m"
DEFAULT_CPU_QUERY = 'app_cpu_usage_percent{service="{service}"}'
DEFAULT_MEMORY_QUERY = 'app_memory_usage_percent{service="{service}"}'

# Prometheus 单序列结果数据点上限：CPU/内存模板应聚合为一条序列，仅作越界保护
PROMETHEUS_MAX_SERIES = 20


class PrometheusQueryError(RuntimeError):
    """查询 Prometheus 失败（网络/HTTP/解析/非 success 状态）时抛出。"""


def _env(name: str, default: str) -> str:
    """读取环境变量，空字符串视为未设置。"""

    value = os.getenv(name)
    return value if value not in (None, "") else default


def _current_monitor_mode() -> str:
    """当前监控数据源模式（小写规范化）。"""

    return _env("MONITOR_TARGET_MODE", "self").strip().lower()


def log_tool_call(func):
    """记录工具调用的日志，包括方法名、参数和返回状态。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        method_name = func.__name__
        logger.info("=" * 80)
        logger.info("调用方法: %s", method_name)
        if kwargs:
            try:
                logger.info("参数信息:\n%s", json.dumps(kwargs, ensure_ascii=False, indent=2))
            except (TypeError, ValueError):
                logger.info("参数信息: %s", kwargs)
        else:
            logger.info("参数信息: 无")

        try:
            result = func(*args, **kwargs)
            logger.info("返回状态: SUCCESS")
            if isinstance(result, dict):
                summary = {
                    k: v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} with {len(v)} items>"
                    for k, v in list(result.items())[:5]
                }
                logger.info("返回结果摘要: %s", json.dumps(summary, ensure_ascii=False))
            else:
                logger.info("返回结果: %s", result)
            logger.info("=" * 80)
            return result
        except Exception as e:
            logger.error("返回状态: ERROR")
            logger.error("错误信息: %s", e)
            logger.error("=" * 80)
            raise

    return wrapper


def parse_time_or_default(time_str: str | None, default_offset_hours: int = 0) -> datetime:
    """解析 ``YYYY-MM-DD HH:MM:SS`` 时间字符串，失败时返回相对当前时间。"""

    if time_str:
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return datetime.now() + timedelta(hours=default_offset_hours)


def _interval_to_minutes(interval: str) -> int:
    if interval.endswith("m"):
        return max(1, int(interval[:-1]))
    if interval.endswith("h"):
        return max(1, int(interval[:-1]) * 60)
    return 1


def _evidence(payload: dict[str, Any], source: str = "local-machine") -> dict[str, Any]:
    return {
        "status": "success",
        "source": source,
        "evidence_id": f"monitor-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        **payload,
    }


def _timed_evidence(start_time: float, payload: dict[str, Any], source: str = "local-machine") -> dict[str, Any]:
    return _evidence(
        {
            "duration_ms": round((time.perf_counter() - start_time) * 1000, 2),
            **payload,
        },
        source=source,
    )


def _query_error(start_time: float, message: str, source: str = "prometheus", **payload: Any) -> dict[str, Any]:
    """指标查询失败时的错误证据（status=error，保留 evidence/duration 字段）。"""

    return {
        "status": "error",
        "source": source,
        "evidence_id": f"monitor-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "duration_ms": round((time.perf_counter() - start_time) * 1000, 2),
        "error": message,
        "message": f"查询指标失败: {message}",
        **payload,
    }


def _port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class SelfMonitorProvider:
    """读取本机资源与本项目服务状态的 Provider。"""

    source = "local-machine"

    def __init__(self, project_root: Path = PROJECT_ROOT):
        self.project_root = project_root

    def get_resource_usage(self) -> dict[str, Any]:
        if psutil is not None:
            cpu_percent = float(psutil.cpu_percent(interval=0.05))
            memory = psutil.virtual_memory()
            return {
                "cpu": {
                    "usage_percent": round(cpu_percent, 2),
                    "count": psutil.cpu_count(logical=True) or os.cpu_count() or 0,
                },
                "memory": {
                    "usage_percent": round(float(memory.percent), 2),
                    "total_bytes": int(memory.total),
                    "used_bytes": int(memory.used),
                    "available_bytes": int(memory.available),
                },
            }

        disk = shutil.disk_usage(self.project_root)
        return {
            "cpu": {
                "usage_percent": 0.0,
                "count": os.cpu_count() or 0,
                "note": "psutil 未安装，无法读取系统 CPU 使用率",
            },
            "memory": {
                "usage_percent": 0.0,
                "total_bytes": 1,
                "used_bytes": 0,
                "available_bytes": 1,
                "note": "psutil 未安装，无法读取系统内存使用率",
            },
            "disk_fallback": {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
            },
        }

    def get_disk_usage(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self.project_root)
        used_percent = round((usage.used / usage.total) * 100, 2) if usage.total else 0.0
        return {
            "path": str(self.project_root),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "usage_percent": used_percent,
        }

    def get_port_status(self) -> list[dict[str, Any]]:
        statuses = []
        for target in MONITORED_PORTS:
            open_now = _port_open(str(target["host"]), int(target["port"]))
            statuses.append(
                {
                    **target,
                    "status": "open" if open_now else "closed",
                    "reachable": open_now,
                }
            )
        return statuses

    def get_python_processes(self) -> list[dict[str, Any]]:
        if psutil is None:
            return [
                {
                    "pid": os.getpid(),
                    "name": "python",
                    "cmdline": " ".join(os.sys.argv),
                    "note": "psutil 未安装，仅返回当前进程",
                }
            ]

        processes = []
        for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_percent"]):
            try:
                info = proc.info
                name = str(info.get("name") or "")
                cmdline = " ".join(info.get("cmdline") or [])
                if "python" not in name.lower() and "python" not in cmdline.lower():
                    continue
                processes.append(
                    {
                        "pid": info.get("pid"),
                        "name": name,
                        "cmdline": cmdline,
                        "cpu_percent": round(float(info.get("cpu_percent") or 0), 2),
                        "memory_percent": round(float(info.get("memory_percent") or 0), 2),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return processes

    def cpu_series(
        self, service_name: str, start_dt: datetime, end_dt: datetime, interval_minutes: int
    ) -> dict[str, Any]:
        """本机 CPU 快照扩展为时间序列（service_name 仅用于兼容签名）。"""

        cpu = self.get_resource_usage()["cpu"]
        usage = round(float(cpu["usage_percent"]), 2)
        points = _constant_series(
            usage, start_dt, end_dt, interval_minutes, extra={"cpu_count": cpu.get("count")}
        )
        return {"data_points": points, "retrieval_type": "local_resource_snapshot", "source": self.source}

    def memory_series(
        self, service_name: str, start_dt: datetime, end_dt: datetime, interval_minutes: int
    ) -> dict[str, Any]:
        """本机内存快照扩展为时间序列（service_name 仅用于兼容签名）。"""

        memory = self.get_resource_usage()["memory"]
        usage = round(float(memory["usage_percent"]), 2)
        total_gb = round(float(memory["total_bytes"]) / (1024**3), 2)
        used_gb = round(float(memory["used_bytes"]) / (1024**3), 2)
        points = _constant_series(
            usage, start_dt, end_dt, interval_minutes, extra={"used_gb": used_gb, "total_gb": total_gb}
        )
        return {"data_points": points, "retrieval_type": "local_resource_snapshot", "source": self.source}


monitor_provider = SelfMonitorProvider()


class PrometheusProvider:
    """通过 Prometheus HTTP API（GET /api/v1/query_range）拉取真实服务指标。

    CPU/内存查询使用可配置 PromQL 模板（{service}/{range} 占位）；模板应聚合为单条
    返回百分比（0-100）的序列，以复用现有阈值/统计逻辑。任意其它指标（延迟、错误率、
    QPS 等）走 ``range_query_all`` 由 :func:`query_prometheus_range` 工具直接传入 PromQL。
    """

    source = "prometheus"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        cpu_query: str | None = None,
        memory_query: str | None = None,
        rate_window: str | None = None,
    ):
        self.base_url = (base_url or _env("PROMETHEUS_BASE_URL", DEFAULT_PROMETHEUS_BASE_URL)).rstrip("/")
        self.timeout = timeout if timeout is not None else float(_env("PROMETHEUS_REQUEST_TIMEOUT", DEFAULT_PROMETHEUS_TIMEOUT))
        self.cpu_query = cpu_query or _env("PROMETHEUS_CPU_QUERY", DEFAULT_CPU_QUERY)
        self.memory_query = memory_query or _env("PROMETHEUS_MEMORY_QUERY", DEFAULT_MEMORY_QUERY)
        self.rate_window = rate_window or _env("PROMETHEUS_RATE_WINDOW", DEFAULT_PROMETHEUS_RATE_WINDOW)

    def _render(self, template: str, service_name: str) -> str:
        # 用 replace 而非 str.format，避免 PromQL 标签选择器的 {} 触发 format 解析错误
        return template.replace("{service}", service_name).replace("{range}", self.rate_window)

    def _request_range(
        self, promql: str, start_dt: datetime, end_dt: datetime, step_seconds: int
    ) -> dict[str, Any]:
        params = {
            "query": promql,
            "start": start_dt.timestamp(),
            "end": end_dt.timestamp(),
            "step": f"{step_seconds}s",
        }
        url = f"{self.base_url}/api/v1/query_range"
        logger.info("Querying Prometheus range: %s | query=%s", url, promql)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as e:
            raise PrometheusQueryError(f"failed to query Prometheus: {e}") from e
        except json.JSONDecodeError as e:
            raise PrometheusQueryError(f"failed to parse Prometheus response: {e}") from e
        if body.get("status") != "success":
            msg = body.get("error") or body.get("errorType") or "Prometheus returned non-success status"
            raise PrometheusQueryError(str(msg))
        return body

    @staticmethod
    def _values_to_points(values: list[Any]) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        for pair in values or []:
            try:
                ts, val = pair[0], pair[1]
                fval = round(float(val), 2)
            except (TypeError, ValueError, IndexError):
                continue
            points.append(
                {"timestamp": datetime.fromtimestamp(float(ts)).strftime("%H:%M"), "value": fval}
            )
        return points

    def range_query(
        self, promql: str, start_dt: datetime, end_dt: datetime, step_seconds: int
    ) -> list[dict[str, Any]]:
        """运行 PromQL 区间查询，返回首条序列的数据点（模板应聚合为单序列）。"""

        body = self._request_range(promql, start_dt, end_dt, step_seconds)
        result = (body.get("data") or {}).get("result") or []
        if not result:
            return []
        return self._values_to_points(result[0].get("values") or [])

    def range_query_all(
        self, promql: str, start_dt: datetime, end_dt: datetime, step_seconds: int
    ) -> list[dict[str, Any]]:
        """运行 PromQL 区间查询，返回全部序列（含 labels），用于任意指标。"""

        body = self._request_range(promql, start_dt, end_dt, step_seconds)
        result = (body.get("data") or {}).get("result") or []
        series: list[dict[str, Any]] = []
        for item in result[:PROMETHEUS_MAX_SERIES]:
            series.append(
                {"metric": item.get("metric") or {}, "data_points": self._values_to_points(item.get("values") or [])}
            )
        return series

    def cpu_series(
        self, service_name: str, start_dt: datetime, end_dt: datetime, interval_minutes: int
    ) -> dict[str, Any]:
        promql = self._render(self.cpu_query, service_name)
        points = self.range_query(promql, start_dt, end_dt, interval_minutes * 60)
        return {
            "data_points": points,
            "retrieval_type": "prometheus_query_range",
            "source": self.source,
            "promql": promql,
        }

    def memory_series(
        self, service_name: str, start_dt: datetime, end_dt: datetime, interval_minutes: int
    ) -> dict[str, Any]:
        promql = self._render(self.memory_query, service_name)
        points = self.range_query(promql, start_dt, end_dt, interval_minutes * 60)
        return {
            "data_points": points,
            "retrieval_type": "prometheus_query_range",
            "source": self.source,
            "promql": promql,
        }


def get_metric_provider() -> SelfMonitorProvider | PrometheusProvider:
    """按 MONITOR_TARGET_MODE 选择指标 Provider。

    self/local 复用本机 Provider 单例（便于测试 monkeypatch 与端口/磁盘工具共享状态）；
    prometheus 每次构造新的 PrometheusProvider（无共享状态，按需读取最新配置）。
    """

    if _current_monitor_mode() == "prometheus":
        return PrometheusProvider()
    return monitor_provider


def _constant_series(
    value: float,
    start_dt: datetime,
    end_dt: datetime,
    interval_minutes: int,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    data_points = []
    current_time = start_dt
    while current_time <= end_dt:
        point = {
            "timestamp": current_time.strftime("%H:%M"),
            "value": value,
        }
        if extra:
            point.update(extra)
        data_points.append(point)
        current_time += timedelta(minutes=interval_minutes)
    return data_points


def _stats(values: list[float], high_threshold: float) -> dict[str, Any]:
    if not values:
        return {}
    sorted_values = sorted(values)
    p95_index = min(len(sorted_values) - 1, int(len(sorted_values) * 0.95))
    return {
        "avg": round(sum(values) / len(values), 2),
        "max": max(values),
        "min": min(values),
        "p95": round(sorted_values[p95_index], 2),
        "threshold_exceeded": max(values) > high_threshold,
    }


@mcp.tool()
@log_tool_call
def get_local_resource_usage() -> dict[str, Any]:
    """获取本机 CPU 与内存快照。"""

    start = time.perf_counter()
    snapshot = monitor_provider.get_resource_usage()
    return _timed_evidence(start, snapshot)


@mcp.tool()
@log_tool_call
def get_service_ports_status() -> dict[str, Any]:
    """检查本项目相关服务端口状态。"""

    start = time.perf_counter()
    ports = monitor_provider.get_port_status()
    return _timed_evidence(start, {"ports": ports, "total": len(ports)})


@mcp.tool()
@log_tool_call
def check_api_health() -> dict[str, Any]:
    """检查 FastAPI 服务端口是否可达。"""

    start = time.perf_counter()
    target = next(item for item in MONITORED_PORTS if item["service_name"] == "aiops-assistant-api")
    reachable = _port_open(str(target["host"]), int(target["port"]))
    return _timed_evidence(
        start,
        {
            "service_name": target["service_name"],
            "host": target["host"],
            "port": target["port"],
            "reachable": reachable,
            "status": "healthy" if reachable else "unreachable",
        },
    )


@mcp.tool()
@log_tool_call
def check_mcp_health() -> dict[str, Any]:
    """检查本地 MCP Server 端口是否可达。"""

    start = time.perf_counter()
    targets = [item for item in MONITORED_PORTS if item["service_name"].endswith("mcp-server")]
    services = []
    for target in targets:
        reachable = _port_open(str(target["host"]), int(target["port"]))
        services.append({**target, "reachable": reachable, "status": "healthy" if reachable else "unreachable"})
    return _timed_evidence(start, {"services": services, "total": len(services)})


@mcp.tool()
@log_tool_call
def check_milvus_health() -> dict[str, Any]:
    """检查 Milvus 端口是否可达。"""

    start = time.perf_counter()
    target = next(item for item in MONITORED_PORTS if item["service_name"] == "milvus")
    reachable = _port_open(str(target["host"]), int(target["port"]))
    return _timed_evidence(
        start,
        {
            "service_name": target["service_name"],
            "host": target["host"],
            "port": target["port"],
            "reachable": reachable,
            "status": "healthy" if reachable else "unreachable",
        },
    )


@mcp.tool()
@log_tool_call
def get_python_processes() -> dict[str, Any]:
    """列出本机 Python 进程。"""

    start = time.perf_counter()
    processes = monitor_provider.get_python_processes()
    return _timed_evidence(start, {"processes": processes, "total": len(processes)})


@mcp.tool()
@log_tool_call
def get_disk_usage() -> dict[str, Any]:
    """获取项目所在磁盘使用情况。"""

    start = time.perf_counter()
    return _timed_evidence(start, {"disk": monitor_provider.get_disk_usage()})


@mcp.tool()
@log_tool_call
def query_cpu_metrics(
    service_name: str,
    start_time: str | None = None,
    end_time: str | None = None,
    interval: str = "1m",
) -> dict[str, Any]:
    """查询服务 CPU 使用率（百分比时间序列）。

    数据源由 MONITOR_TARGET_MODE 决定：self/local 返回本机资源快照扩展的序列；
    prometheus 走真实 PromQL（/api/v1/query_range，按 PROMETHEUS_CPU_QUERY 模板）。
    保留旧工具签名以兼容现有 Agent 计划。
    """

    start = time.perf_counter()
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)
    interval_minutes = _interval_to_minutes(interval)
    provider = get_metric_provider()
    try:
        series = provider.cpu_series(service_name, start_dt, end_dt, interval_minutes)
    except PrometheusQueryError as e:
        return _query_error(start, str(e), service_name=service_name, metric_name="cpu_usage_percent")

    data_points = series["data_points"]
    values = [float(item["value"]) for item in data_points]
    alert_triggered = bool(values and max(values) > 80.0)
    payload = {
        "service_name": service_name,
        "metric_name": "cpu_usage_percent",
        "retrieval_type": series["retrieval_type"],
        "interval": interval,
        "data_points": data_points,
        "statistics": _stats(values, high_threshold=80.0),
        "alert_info": {
            "triggered": alert_triggered,
            "threshold": 80.0,
            "message": "CPU 使用率超过 80% 阈值" if alert_triggered else "CPU 使用率正常",
        },
    }
    if "promql" in series:
        payload["promql"] = series["promql"]
    if not data_points:
        payload["note"] = "未返回数据点，请检查 service_name 与 PromQL 模板是否匹配实际指标"
    return _timed_evidence(start, payload, source=series["source"])


@mcp.tool()
@log_tool_call
def query_memory_metrics(
    service_name: str,
    start_time: str | None = None,
    end_time: str | None = None,
    interval: str = "1m",
) -> dict[str, Any]:
    """查询服务内存使用率（百分比时间序列）。

    数据源由 MONITOR_TARGET_MODE 决定：self/local 返回本机资源快照扩展的序列；
    prometheus 走真实 PromQL（/api/v1/query_range，按 PROMETHEUS_MEMORY_QUERY 模板）。
    """

    start = time.perf_counter()
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)
    interval_minutes = _interval_to_minutes(interval)
    provider = get_metric_provider()
    try:
        series = provider.memory_series(service_name, start_dt, end_dt, interval_minutes)
    except PrometheusQueryError as e:
        return _query_error(start, str(e), service_name=service_name, metric_name="memory_usage_percent")

    data_points = series["data_points"]
    values = [float(item["value"]) for item in data_points]
    alert_triggered = bool(values and max(values) > 70.0)
    payload = {
        "service_name": service_name,
        "metric_name": "memory_usage_percent",
        "retrieval_type": series["retrieval_type"],
        "interval": interval,
        "data_points": data_points,
        "statistics": {
            **_stats(values, high_threshold=70.0),
            "memory_pressure": alert_triggered,
        },
        "alert_info": {
            "triggered": alert_triggered,
            "threshold": 70.0,
            "message": "内存使用率超过 70% 阈值，存在内存压力" if alert_triggered else "内存使用率正常",
        },
    }
    if "promql" in series:
        payload["promql"] = series["promql"]
    if not data_points:
        payload["note"] = "未返回数据点，请检查 service_name 与 PromQL 模板是否匹配实际指标"
    return _timed_evidence(start, payload, source=series["source"])


@mcp.tool()
@log_tool_call
def query_prometheus_range(
    query: str,
    start_time: str | None = None,
    end_time: str | None = None,
    interval: str = "1m",
) -> dict[str, Any]:
    """对 Prometheus 执行任意 PromQL 区间查询（GET /api/v1/query_range）。

    适用场景：CPU/内存以外的服务指标，如请求延迟分位（p95/p99）、错误率、QPS、连接数等。
    `query` 直接传入完整 PromQL，例如：
        histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))

    仅在 MONITOR_TARGET_MODE=prometheus 时可用；否则返回未启用提示（不报错）。
    时间窗口由 start_time/end_time（``YYYY-MM-DD HH:MM:SS``，缺省取最近 1 小时）与 interval（步长）控制。

    Returns:
        dict: 成功含 series（每条含 metric labels 与 data_points）与 series_count；失败含 status=error 与 error。
    """

    start = time.perf_counter()
    if _current_monitor_mode() != "prometheus":
        return {
            "status": "error",
            "source": "monitor",
            "error": "prometheus_disabled",
            "message": "当前 MONITOR_TARGET_MODE 非 prometheus，未启用 PromQL 查询；请配置后重试。",
        }

    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)
    interval_minutes = _interval_to_minutes(interval)
    provider = PrometheusProvider()
    try:
        series = provider.range_query_all(query, start_dt, end_dt, interval_minutes * 60)
    except PrometheusQueryError as e:
        return _query_error(start, str(e), query=query)

    payload = {
        "query": query,
        "interval": interval,
        "series": series,
        "series_count": len(series),
    }
    if not series:
        payload["note"] = "PromQL 未返回任何序列，请检查表达式与时间窗口"
    return _timed_evidence(start, payload, source="prometheus")


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8004, path="/mcp")
