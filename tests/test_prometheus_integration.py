"""Prometheus 指标链路测试

覆盖两端：
1. 应用侧：app/core/metrics.py 暴露的 /metrics 端点与请求耗时埋点。
2. 采集侧：mcp_servers/monitor_server.py 的 PrometheusProvider / 模式选择 / 工具行为。

不依赖真实 Prometheus：通过 mock httpx.Client 注入响应。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.metrics import setup_metrics
from mcp_servers import monitor_server

# --- httpx 替身 -----------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """最小 httpx.Client 替身：上下文管理 + get() 返回预置 payload 或抛出预置异常。"""

    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self._payload = payload
        self._error = error
        self.calls: list[dict] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def get(self, url: str, params: dict | None = None) -> _FakeResponse:
        self.calls.append({"url": url, "params": params or {}})
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._payload or {})


def _matrix_payload(values: list[list]) -> dict:
    return {
        "status": "success",
        "data": {"resultType": "matrix", "result": [{"metric": {"job": "svc"}, "values": values}]},
    }


def _install_fake_httpx(monkeypatch, payload=None, error=None) -> _FakeClient:
    client = _FakeClient(payload=payload, error=error)
    monkeypatch.setattr(monitor_server.httpx, "Client", lambda *a, **k: client)
    return client


# --- 应用侧：/metrics 端点 -------------------------------------------------


def _app_with_metrics() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    setup_metrics(app)
    return app


def test_metrics_endpoint_exposes_resource_gauges():
    client = TestClient(_app_with_metrics())

    body = client.get("/metrics").text

    assert "app_cpu_usage_percent" in body
    assert "app_memory_usage_percent" in body
    assert "http_request_duration_seconds" in body


def test_metrics_endpoint_records_request_latency():
    client = TestClient(_app_with_metrics())

    assert client.get("/ping").status_code == 200
    body = client.get("/metrics").text

    # 命中的请求被计入直方图，且使用低基数的路由模板而非具体 URL
    assert 'http_request_duration_seconds_count{' in body
    assert 'route="/ping"' in body


def test_metrics_endpoint_not_self_counted():
    client = TestClient(_app_with_metrics())

    client.get("/metrics")
    body = client.get("/metrics").text

    # /metrics 自身不应作为被观测路由出现
    assert 'route="/metrics"' not in body


# --- 采集侧：模式选择 ------------------------------------------------------


def test_get_metric_provider_defaults_to_self_singleton(monkeypatch):
    monkeypatch.delenv("MONITOR_TARGET_MODE", raising=False)
    assert monitor_server.get_metric_provider() is monitor_server.monitor_provider


def test_get_metric_provider_selects_prometheus_when_mode_set(monkeypatch):
    monkeypatch.setenv("MONITOR_TARGET_MODE", "Prometheus")  # 大小写不敏感
    assert isinstance(monitor_server.get_metric_provider(), monitor_server.PrometheusProvider)


# --- 采集侧：PrometheusProvider -------------------------------------------


def test_prometheus_provider_renders_template_without_format_error():
    provider = monitor_server.PrometheusProvider(
        cpu_query='app_cpu_usage_percent{service="{service}"}', rate_window="2m"
    )
    assert provider._render(provider.cpu_query, "payment") == 'app_cpu_usage_percent{service="payment"}'


def test_prometheus_provider_range_query_parses_first_series(monkeypatch):
    _install_fake_httpx(monkeypatch, payload=_matrix_payload([[1700000000, "42.5"], [1700000060, "55.0"]]))
    provider = monitor_server.PrometheusProvider(base_url="http://prom:9090")

    points = provider.range_query("up", datetime.now() - timedelta(minutes=2), datetime.now(), 60)

    assert [p["value"] for p in points] == [42.5, 55.0]
    assert all("timestamp" in p for p in points)


def test_prometheus_provider_raises_on_non_success(monkeypatch):
    _install_fake_httpx(monkeypatch, payload={"status": "error", "error": "bad query"})
    provider = monitor_server.PrometheusProvider()

    with pytest.raises(monitor_server.PrometheusQueryError, match="bad query"):
        provider.range_query("oops", datetime.now() - timedelta(minutes=1), datetime.now(), 60)


# --- 采集侧：工具行为 ------------------------------------------------------


def test_query_cpu_metrics_uses_prometheus_when_mode_set(monkeypatch):
    monkeypatch.setenv("MONITOR_TARGET_MODE", "prometheus")
    client = _install_fake_httpx(monkeypatch, payload=_matrix_payload([[1700000000, "12.0"], [1700000060, "18.0"]]))

    result = monitor_server.query_cpu_metrics(service_name="aiops-assistant-api", interval="1m")

    assert result["status"] == "success"
    assert result["source"] == "prometheus"
    assert result["retrieval_type"] == "prometheus_query_range"
    assert [p["value"] for p in result["data_points"]] == [12.0, 18.0]
    assert client.calls[0]["url"].endswith("/api/v1/query_range")
    # 默认模板已对齐应用指标，并注入 service_name
    assert client.calls[0]["params"]["query"] == 'app_cpu_usage_percent{service="aiops-assistant-api"}'


def test_query_cpu_metrics_returns_error_on_prometheus_failure(monkeypatch):
    monkeypatch.setenv("MONITOR_TARGET_MODE", "prometheus")
    _install_fake_httpx(monkeypatch, error=httpx.ConnectError("connection refused"))

    result = monitor_server.query_cpu_metrics(service_name="aiops-assistant-api")

    assert result["status"] == "error"
    assert result["source"] == "prometheus"
    assert "connection refused" in result["error"]


def test_query_cpu_metrics_self_mode_uses_local_snapshot(monkeypatch):
    monkeypatch.delenv("MONITOR_TARGET_MODE", raising=False)
    monkeypatch.setattr(
        monitor_server.monitor_provider,
        "get_resource_usage",
        lambda: {"cpu": {"usage_percent": 42.5, "count": 8}, "memory": {"usage_percent": 21.0, "total_bytes": 100, "used_bytes": 21}},
    )

    result = monitor_server.query_cpu_metrics(service_name="local-machine", interval="1m")

    assert result["source"] == "local-machine"
    assert result["retrieval_type"] == "local_resource_snapshot"
    assert result["statistics"]["avg"] == 42.5


def test_query_prometheus_range_disabled_in_self_mode(monkeypatch):
    monkeypatch.delenv("MONITOR_TARGET_MODE", raising=False)

    result = monitor_server.query_prometheus_range(query="up")

    assert result["status"] == "error"
    assert result["error"] == "prometheus_disabled"


def test_query_prometheus_range_returns_all_series(monkeypatch):
    monkeypatch.setenv("MONITOR_TARGET_MODE", "prometheus")
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {"metric": {"le": "0.5"}, "values": [[1700000000, "0.2"]]},
                {"metric": {"le": "1.0"}, "values": [[1700000000, "0.9"]]},
            ],
        },
    }
    _install_fake_httpx(monkeypatch, payload=payload)

    result = monitor_server.query_prometheus_range(query="histogram_quantile(0.95, x)")

    assert result["status"] == "success"
    assert result["series_count"] == 2
    assert result["series"][0]["metric"] == {"le": "0.5"}
