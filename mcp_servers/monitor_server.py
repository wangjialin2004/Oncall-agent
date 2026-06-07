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

from fastmcp import FastMCP

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


def _port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class SelfMonitorProvider:
    """读取本机资源与本项目服务状态的 Provider。"""

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


monitor_provider = SelfMonitorProvider()


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
    """查询服务 CPU 使用率。

    当前 Provider 以本项目运行主机为监控对象，返回真实本机资源快照扩展成时间序列，
    保留旧工具签名以兼容现有 Agent 计划。
    """

    start = time.perf_counter()
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)
    interval_minutes = _interval_to_minutes(interval)
    snapshot = monitor_provider.get_resource_usage()
    cpu = snapshot["cpu"]
    usage = round(float(cpu["usage_percent"]), 2)
    data_points = _constant_series(
        usage,
        start_dt,
        end_dt,
        interval_minutes,
        extra={"cpu_count": cpu.get("count")},
    )
    values = [float(item["value"]) for item in data_points]
    alert_triggered = bool(values and max(values) > 80.0)
    return _timed_evidence(
        start,
        {
            "service_name": service_name,
            "metric_name": "cpu_usage_percent",
            "retrieval_type": "local_resource_snapshot",
            "interval": interval,
            "data_points": data_points,
            "statistics": _stats(values, high_threshold=80.0),
            "alert_info": {
                "triggered": alert_triggered,
                "threshold": 80.0,
                "message": "CPU 使用率超过 80% 阈值" if alert_triggered else "CPU 使用率正常",
            },
        },
    )


@mcp.tool()
@log_tool_call
def query_memory_metrics(
    service_name: str,
    start_time: str | None = None,
    end_time: str | None = None,
    interval: str = "1m",
) -> dict[str, Any]:
    """查询服务内存使用率。"""

    start = time.perf_counter()
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)
    interval_minutes = _interval_to_minutes(interval)
    snapshot = monitor_provider.get_resource_usage()
    memory = snapshot["memory"]
    usage = round(float(memory["usage_percent"]), 2)
    total_gb = round(float(memory["total_bytes"]) / (1024**3), 2)
    used_gb = round(float(memory["used_bytes"]) / (1024**3), 2)
    data_points = _constant_series(
        usage,
        start_dt,
        end_dt,
        interval_minutes,
        extra={"used_gb": used_gb, "total_gb": total_gb},
    )
    values = [float(item["value"]) for item in data_points]
    alert_triggered = bool(values and max(values) > 70.0)
    return _timed_evidence(
        start,
        {
            "service_name": service_name,
            "metric_name": "memory_usage_percent",
            "retrieval_type": "local_resource_snapshot",
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
        },
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8004, path="/mcp")
