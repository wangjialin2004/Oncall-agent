from datetime import datetime, timedelta

from mcp_servers import monitor_server


def test_get_local_resource_usage_returns_real_snapshot_with_evidence_fields():
    result = monitor_server.get_local_resource_usage()

    assert result["status"] == "success"
    assert result["source"] == "local-machine"
    assert result["evidence_id"].startswith("monitor-")
    assert result["duration_ms"] >= 0
    assert 0 <= result["cpu"]["usage_percent"] <= 100
    assert 0 <= result["memory"]["usage_percent"] <= 100
    assert result["memory"]["total_bytes"] > 0


def test_query_cpu_metrics_uses_resource_provider_snapshot(monkeypatch):
    sample = {
        "cpu": {"usage_percent": 42.5, "count": 8},
        "memory": {"usage_percent": 21.0, "total_bytes": 100, "used_bytes": 21},
    }

    monkeypatch.setattr(monitor_server.monitor_provider, "get_resource_usage", lambda: sample)

    now = datetime.now().replace(microsecond=0)
    result = monitor_server.query_cpu_metrics(
        service_name="local-machine",
        start_time=(now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"),
        end_time=now.strftime("%Y-%m-%d %H:%M:%S"),
        interval="1m",
    )

    assert result["source"] == "local-machine"
    assert result["retrieval_type"] == "local_resource_snapshot"
    assert result["statistics"]["avg"] == 42.5
    assert all(point["value"] == 42.5 for point in result["data_points"])


def test_get_service_ports_status_reports_configured_ports():
    result = monitor_server.get_service_ports_status()

    ports = {item["port"] for item in result["ports"]}
    assert {9900, 8003, 8004, 19530}.issubset(ports)
    assert result["source"] == "local-machine"
    assert result["evidence_id"].startswith("monitor-")
