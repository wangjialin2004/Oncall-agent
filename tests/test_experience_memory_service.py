import sqlite3
from pathlib import Path

from app.config import config
from app.services.experience_memory_service import ExperienceMemoryService


def test_experience_memory_service_uses_configured_default_db_path():
    service = ExperienceMemoryService()

    assert service.db_path == Path(config.experience_memory_db_path)


def test_experience_memory_service_schema_uses_experience_id_primary_key(tmp_path):
    db_path = tmp_path / "experience.sqlite3"
    service = ExperienceMemoryService(db_path=db_path)

    service.create_memory(
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
        symptoms="Milvus timeout",
        root_cause="connection pool exhausted",
        resolution="reuse client",
        evidence_summary="evidence cls-1",
        source_case_id="case-1",
        source_feedback_id="feedback-1",
        confidence=0.8,
    )

    connection = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]: {"type": row[2], "primary_key_order": row[5]}
            for row in connection.execute("PRAGMA table_info(experience_memories)")
        }
    finally:
        connection.close()

    assert "id" not in columns
    assert columns["experience_id"] == {"type": "TEXT", "primary_key_order": 1}
    assert columns["milvus_pk"] == {"type": "TEXT", "primary_key_order": 0}


def test_experience_memory_service_uses_supplied_experience_id(tmp_path):
    service = ExperienceMemoryService(db_path=tmp_path / "experience.sqlite3")

    memory_id = service.create_memory(
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
        symptoms="Milvus timeout",
        root_cause="connection pool exhausted",
        resolution="reuse client",
        evidence_summary="evidence cls-1",
        source_case_id="case-1",
        source_feedback_id="feedback-1",
        confidence=0.8,
        experience_id="exp-fixed",
    )

    memory = service.get_memory(memory_id)

    assert memory_id == "exp-fixed"
    assert memory["experience_id"] == "exp-fixed"
    assert memory["milvus_pk"] == "exp-fixed"


def test_experience_memory_service_creates_and_reads_memory(tmp_path):
    service = ExperienceMemoryService(db_path=tmp_path / "experience.sqlite3")

    memory_id = service.create_memory(
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
        symptoms="FastAPI latency increased with Milvus timeout logs",
        root_cause="Milvus connection pool exhausted",
        resolution="Restart Milvus and reuse client connections",
        evidence_summary="tool=search_app_logs evidence_id=cls-1 timeout observed",
        source_case_id="case-1",
        source_feedback_id="feedback-1",
        confidence=0.8,
    )

    memory = service.get_memory(memory_id)

    assert memory is not None
    assert memory["experience_id"] == memory_id
    assert memory["milvus_pk"] == memory_id
    assert memory["project_id"] == "super_biz_agent"
    assert memory["enabled"] is True
    assert memory["source_case_ids"] == ["case-1"]
    assert memory["source_feedback_ids"] == ["feedback-1"]
    assert memory["hit_count"] == 0
    assert memory["success_count"] == 0


def test_experience_memory_service_lists_filters_and_disables(tmp_path):
    service = ExperienceMemoryService(db_path=tmp_path / "experience.sqlite3")
    enabled_id = service.create_memory(
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
        symptoms="Milvus timeout",
        root_cause="connection pool exhausted",
        resolution="reuse client",
        evidence_summary="evidence cls-1",
        source_case_id="case-1",
        source_feedback_id="feedback-1",
        confidence=0.8,
    )
    disabled_id = service.create_memory(
        project_id="other_project",
        environment="prod",
        service_name="api",
        symptoms="API disk full",
        root_cause="disk saturation",
        resolution="expand disk",
        evidence_summary="evidence metric-2",
        source_case_id="case-2",
        source_feedback_id="feedback-2",
        confidence=0.8,
    )

    service.set_enabled(disabled_id, enabled=False)
    service.increment_hit_count(enabled_id)

    results = service.list_memories(project_id="super_biz_agent", enabled=True)

    assert [item["experience_id"] for item in results] == [enabled_id]
    assert results[0]["milvus_pk"] == enabled_id
    assert results[0]["hit_count"] == 1
    assert service.get_memory(disabled_id)["enabled"] is False
