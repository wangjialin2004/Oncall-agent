"""Prometheus 指标埋点

给 FastAPI 应用暴露 ``/metrics``，让本项目自身成为可被 Prometheus 抓取的目标，
从而打通「应用产生指标 → Prometheus 抓取/存储 → monitor MCP 走 PromQL 查询」的链路。

暴露的关键指标：
- ``app_cpu_usage_percent`` / ``app_memory_usage_percent``：本机 CPU/内存使用率（0-100，Gauge），
  与 monitor MCP 的 self 模式语义一致，可直接被 PROMETHEUS_CPU_QUERY/MEMORY_QUERY 默认模板查询。
- ``http_request_duration_seconds``：HTTP 请求耗时直方图（Histogram），用于延迟分位（p95/p99）等查询。

指标对象为模块级单例（只在导入时向默认 REGISTRY 注册一次）；:func:`setup_metrics`
只负责挂载中间件与 ``/metrics`` 路由，可安全地对任意 FastAPI 实例调用。
"""

from __future__ import annotations

import time

from fastapi import FastAPI
from prometheus_client import Gauge, Histogram, make_asgi_app

try:
    import psutil
except ImportError:  # pragma: no cover - psutil 为可选依赖，缺失时降级为 0
    psutil = None

METRICS_PATH = "/metrics"

# --- 资源使用率 Gauge（采集时惰性求值，避免后台线程）---------------------------
CPU_USAGE_PERCENT = Gauge(
    "app_cpu_usage_percent",
    "本机 CPU 使用率（0-100），由应用进程通过 psutil 采样",
)
MEMORY_USAGE_PERCENT = Gauge(
    "app_memory_usage_percent",
    "本机内存使用率（0-100），由应用进程通过 psutil 采样",
)


def _read_cpu_percent() -> float:
    if psutil is None:
        return 0.0
    # interval=None：返回自上次调用以来的非阻塞采样；Prometheus 周期抓取即可得到有意义的值
    return float(psutil.cpu_percent(interval=None))


def _read_memory_percent() -> float:
    if psutil is None:
        return 0.0
    return float(psutil.virtual_memory().percent)


CPU_USAGE_PERCENT.set_function(_read_cpu_percent)
MEMORY_USAGE_PERCENT.set_function(_read_memory_percent)

# --- HTTP 请求耗时直方图 -------------------------------------------------------
REQUEST_LATENCY_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求处理耗时（秒）",
    labelnames=("method", "route", "status"),
)


def _route_template(request) -> str:
    """优先用匹配到的路由模板（低基数），未匹配则归为 ``unmatched``。"""

    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


def setup_metrics(app: FastAPI) -> None:
    """为应用挂载 ``/metrics`` 端点与请求耗时中间件。

    需在应用开始处理请求前调用（通常在创建 app、注册路由后立即调用）。
    """

    # 预热 CPU 采样基线：首次 cpu_percent(interval=None) 总返回 0.0
    if psutil is not None:
        psutil.cpu_percent(interval=None)

    @app.middleware("http")
    async def _record_request_latency(request, call_next):
        # /metrics 自身不计入，避免抓取动作污染延迟分布
        if request.url.path.startswith(METRICS_PATH):
            return await call_next(request)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - start
            REQUEST_LATENCY_SECONDS.labels(
                request.method, _route_template(request), str(status_code)
            ).observe(elapsed)

    # 默认 REGISTRY 已包含上面定义的指标；make_asgi_app 负责渲染文本格式
    app.mount(METRICS_PATH, make_asgi_app())
