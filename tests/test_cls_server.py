
from mcp_servers import cls_server


def test_get_log_files_lists_project_logs(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_file = logs_dir / "app_2026-06-02.log"
    log_file.write_text("2026-06-02 12:00:00 INFO service started\n", encoding="utf-8")

    monkeypatch.setattr(cls_server.log_provider, "logs_dir", logs_dir)

    result = cls_server.get_log_files()

    assert result["status"] == "success"
    assert result["source"] == "local_logs"
    assert result["evidence_id"].startswith("cls-")
    assert result["total"] == 1
    assert result["files"][0]["path"] == str(log_file)


def test_search_app_logs_filters_keyword_and_level(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "app_2026-06-02.log").write_text(
        "\n".join(
            [
                "2026-06-02 12:00:00 INFO service started",
                "2026-06-02 12:01:00 ERROR Milvus connection failed",
                "2026-06-02 12:02:00 WARNING retry Milvus connection",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cls_server.log_provider, "logs_dir", logs_dir)

    result = cls_server.search_app_logs(keyword="Milvus", level="ERROR", limit=5)

    assert result["status"] == "success"
    assert result["total"] == 1
    assert result["logs"][0]["level"] == "ERROR"
    assert "Milvus connection failed" in result["logs"][0]["message"]
    assert result["logs"][0]["source"] == str(logs_dir / "app_2026-06-02.log")


def test_search_log_reads_local_topic_logs(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "app_2026-06-02.log").write_text(
        "2026-06-02 12:01:00 ERROR local app failed\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cls_server.log_provider, "logs_dir", logs_dir)

    result = cls_server.search_log(
        topic_id="local-app-logs",
        start_time=0,
        end_time=9_999_999_999_999,
        query="ERROR",
        limit=10,
    )

    assert result["topic_id"] == "local-app-logs"
    assert result["total"] == 1
    assert result["source"] == "local_logs"
    assert result["logs"][0]["message"] == "local app failed"
