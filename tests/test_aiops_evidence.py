import importlib

from langchain_core.messages import ToolMessage

from app.agent.aiops.evidence import append_evidence_summary, build_tool_evidence

executor_module = importlib.import_module("app.agent.aiops.executor")


def test_build_tool_evidence_extracts_auditable_fields_from_tool_messages():
    tool_message = ToolMessage(
        content=(
            '{"status":"success","source":"local_logs","evidence_id":"cls-20260602120000-abcd1234",'
            '"duration_ms":12.5,"total":1,'
            '"logs":[{"level":"ERROR","message":"Milvus connection failed"}]}'
        ),
        name="search_app_logs",
        tool_call_id="call-1",
    )

    evidence = build_tool_evidence([tool_message])

    assert evidence == [
        {
            "tool_name": "search_app_logs",
            "tool_call_id": "call-1",
            "evidence_id": "cls-20260602120000-abcd1234",
            "source": "local_logs",
            "success": True,
            "duration_ms": 12.5,
            "summary": "status=success; total=1; logs[1]: ERROR Milvus connection failed",
        }
    ]


def test_append_evidence_summary_adds_traceable_markdown_block():
    result = append_evidence_summary(
        "初步判断 Milvus 不可用。",
        [
            {
                "tool_name": "search_app_logs",
                "tool_call_id": "call-1",
                "evidence_id": "cls-20260602120000-abcd1234",
                "source": "local_logs",
                "success": True,
                "duration_ms": 12.5,
                "summary": "logs[1]: ERROR Milvus connection failed",
            }
        ],
    )

    assert "初步判断 Milvus 不可用。" in result
    assert "## 工具证据摘要" in result
    assert "证据ID: cls-20260602120000-abcd1234" in result
    assert "工具: search_app_logs" in result
    assert "来源: local_logs" in result


def test_executor_result_formatter_appends_tool_evidence_summary():
    tool_message = ToolMessage(
        content='{"status":"success","source":"local-machine","evidence_id":"monitor-1","duration_ms":3,"total":4}',
        name="get_service_ports_status",
        tool_call_id="call-ports",
    )

    result = executor_module.format_executor_result("端口状态已检查。", [tool_message])

    assert "端口状态已检查。" in result
    assert "证据ID: monitor-1" in result
    assert "工具: get_service_ports_status" in result
