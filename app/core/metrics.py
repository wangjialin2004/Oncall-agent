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

import hmac
import ipaddress
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from starlette.responses import PlainTextResponse

try:
    import psutil
except ImportError:  # pragma: no cover - psutil 为可选依赖，缺失时降级为 0
    psutil = None

METRICS_PATH = "/metrics"


def _request_headers(scope: dict[str, Any]) -> dict[bytes, bytes]:
    return {key.lower(): value for key, value in scope.get("headers", [])}


def metrics_access_allowed(scope: dict[str, Any]) -> bool:
    """Apply the fail-closed metrics access policy to an ASGI request scope."""

    mode = str(getattr(_get_config(), "metrics_access_mode", "internal") or "internal").strip().lower()
    config = _get_config()
    if mode == "public":
        return bool(config.debug and config.metrics_public_debug_enabled)
    if mode == "bearer":
        expected = str(config.metrics_bearer_token or "").strip()
        if not expected:
            return False
        actual = _request_headers(scope).get(b"authorization", b"").decode("latin-1")
        scheme, _, token = actual.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(token.strip(), expected)
    if mode != "internal":
        return False
    client = scope.get("client")
    host = client[0] if isinstance(client, (tuple, list)) and client else None
    try:
        address = ipaddress.ip_address(str(host))
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private)


def _get_config() -> Any:
    # Lazy import avoids making the metrics module's global Prometheus objects
    # depend on settings construction during package import.
    from app.config import config

    return config


class _ProtectedMetricsApp:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[None]]) -> None:
        if scope.get("type") != "http" or metrics_access_allowed(scope):
            await self.app(scope, receive, send)
            return
        mode = str(getattr(_get_config(), "metrics_access_mode", "internal") or "internal").strip().lower()
        status = 401 if mode == "bearer" else 403
        response = PlainTextResponse("metrics access denied", status_code=status)
        await response(scope, receive, send)

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

# --- Agent harness quality metrics (M3 W9) ------------------------------------
AGENT_RUNS_TOTAL = Counter(
    "agent_runs_total",
    "Harness runs by terminal status",
    labelnames=("status",),
)
AGENT_LATENCY_SECONDS = Histogram(
    "agent_latency_seconds",
    "Harness end-to-end latency seconds",
    buckets=(5, 15, 30, 60, 90, 120, 180, 240, 300),
)
AGENT_RE_EVIDENCE_TOTAL = Counter(
    "agent_re_evidence_total",
    "Re-evidence rounds triggered",
)
AGENT_REPLAN_TOTAL = Counter(
    "agent_replan_total",
    "Replan events triggered",
)
AGENT_DELEGATE_PARALLEL_TOTAL = Counter(
    "agent_delegate_parallel_total",
    "Parallel delegation fan-outs",
)

# --- Agent cost / tool metrics (M3 W11) ----------------------------------------
# Low-cardinality only: role ∈ {prompt,completion,total}; tool via whitelist.
AGENT_TOKENS_TOTAL = Counter(
    "agent_tokens_total",
    "LLM tokens consumed by harness runs",
    labelnames=("role",),
)
AGENT_TOOL_CALLS_TOTAL = Counter(
    "agent_tool_calls_total",
    "Harness tool calls by tool name and status",
    labelnames=("tool", "status"),
)

# Tool-name whitelist (unknown → "other") to avoid Prometheus cardinality blow-up.
_TOOL_NAME_WHITELIST = frozenset(
    {
        "delegate_to_expert",
        "delegate_parallel",
        "search_knowledge",
        "search_docs",
        "vector_search",
        "search_app_logs",
        "query_prometheus",
        "query_metrics",
        "get_metric",
        "list_metrics",
        "query_range",
        "get_change_events",
        "search_changes",
        "get_context",
        "update_context",
        "recall_experience",
        "other",
    }
)
_TOOL_STATUS_OK = frozenset({"completed", "success", "ok"})
_TOOL_STATUS_ERROR = frozenset({"failed", "error"})
_TOOL_STATUS_TIMEOUT = frozenset({"timeout", "cancelled", "canceled"})
_TOKEN_ROLES = frozenset({"prompt", "completion", "total"})


def _normalize_tool_name(tool: str) -> str:
    name = (tool or "").strip().lower() or "other"
    # Keep original casing only for known tools; map variants to lower for match.
    for known in _TOOL_NAME_WHITELIST:
        if name == known.lower():
            return known
    # Common aliases / substrings collapse into known buckets when exact miss.
    if "prometheus" in name or name.endswith("_metric") or "metrics" in name:
        return "query_prometheus" if "query_prometheus" in _TOOL_NAME_WHITELIST else "other"
    if "log" in name:
        return "search_app_logs"
    if "knowledge" in name or "vector" in name or "doc" in name:
        return "search_knowledge"
    if "change" in name:
        return "search_changes"
    if name.startswith("delegate"):
        return "delegate_to_expert"
    return "other"


def _normalize_tool_status(status: str) -> str:
    label = (status or "").strip().lower() or "other"
    if label in _TOOL_STATUS_OK:
        return "ok"
    if label in _TOOL_STATUS_ERROR:
        return "error"
    if label in _TOOL_STATUS_TIMEOUT:
        return "timeout"
    return "other"


def observe_agent_run(
    *,
    status: str,
    latency_seconds: float | None = None,
    re_evidence_rounds: int = 0,
    replan_times: int = 0,
    parallel_delegations: int = 0,
) -> None:
    """Best-effort agent metrics; never raises into the harness path."""
    try:
        label = (status or "unknown").strip().lower() or "unknown"
        if label not in {"completed", "degraded", "failed", "timeout", "fallback"}:
            label = "other"
        AGENT_RUNS_TOTAL.labels(status=label).inc()
        if latency_seconds is not None and latency_seconds >= 0:
            AGENT_LATENCY_SECONDS.observe(float(latency_seconds))
        if re_evidence_rounds > 0:
            AGENT_RE_EVIDENCE_TOTAL.inc(re_evidence_rounds)
        if replan_times > 0:
            AGENT_REPLAN_TOTAL.inc(replan_times)
        if parallel_delegations > 0:
            AGENT_DELEGATE_PARALLEL_TOTAL.inc(parallel_delegations)
    except Exception:  # pragma: no cover - metrics must not break product path
        return


def observe_agent_tokens(usage: dict | None) -> None:
    """Record prompt/completion/total tokens from harness ``usage_total``.

    Missing or non-numeric fields are skipped. Never raises.
    """
    if not usage:
        return
    try:
        mapping = (
            ("prompt", usage.get("prompt_tokens")),
            ("completion", usage.get("completion_tokens")),
            ("total", usage.get("total_tokens")),
        )
        for role, raw in mapping:
            if role not in _TOKEN_ROLES:
                continue
            if not isinstance(raw, (int, float)):
                continue
            value = int(raw)
            if value <= 0:
                continue
            AGENT_TOKENS_TOTAL.labels(role=role).inc(value)
    except Exception:  # pragma: no cover
        return


def observe_tool_call(*, tool: str, status: str, count: int = 1) -> None:
    """Increment one tool-call counter with cardinality-safe labels."""
    if count <= 0:
        return
    try:
        AGENT_TOOL_CALLS_TOTAL.labels(
            tool=_normalize_tool_name(tool),
            status=_normalize_tool_status(status),
        ).inc(int(count))
    except Exception:  # pragma: no cover
        return


def observe_tool_calls_from_timeline(events: list | tuple | None) -> None:
    """Scan harness timeline once and count tool_event rows.

    Prefer calling this at complete (not per event) to keep the hot path light.
    """
    if not events:
        return
    try:
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "tool_event":
                continue
            tool = str(event.get("tool") or event.get("tool_name") or "")
            status = str(event.get("status") or "")
            observe_tool_call(tool=tool, status=status, count=1)
    except Exception:  # pragma: no cover
        return


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

    # 默认 REGISTRY 已包含上面定义的指标；make_asgi_app 负责渲染文本格式。
    # The wrapper is required because a mounted ASGI app bypasses FastAPI route
    # dependencies, so a normal ``Depends(auth)`` cannot protect this endpoint.
    app.mount(METRICS_PATH, _ProtectedMetricsApp(make_asgi_app()))
