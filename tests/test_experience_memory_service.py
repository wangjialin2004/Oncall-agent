import sqlite3
from pathlib import Path

from app.config import config
from app.services.diagnosis_memory_service import DiagnosisMemoryService
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


class _NoopIndex:
    def __init__(self):
        self.upserts = []

    def find_similar(self, *, query, project_id, top_k):
        return []

    def upsert_memory(self, memory):
        self.upserts.append(memory)
        return memory["experience_id"]


class _SearchIndex:
    def __init__(self):
        self.search_calls = []
        self.disabled = []
        self.rebuilt = []

    def find_similar(self, *, query, project_id, top_k):
        self.search_calls.append({"query": query, "project_id": project_id, "top_k": top_k})
        return []

    def upsert_memory(self, memory):
        return memory["experience_id"]

    def disable_memory(self, experience_id):
        self.disabled.append(experience_id)

    def rebuild(self, memories):
        self.rebuilt.append(list(memories))
        return len(self.rebuilt[-1])


def test_create_or_merge_from_feedback_builds_rule_based_card(tmp_path):
    diagnosis = DiagnosisMemoryService(tmp_path / "diagnosis.sqlite3")
    case_id = diagnosis.create_case(
        session_id="session-1",
        user_input="diagnose Milvus timeout and slow API",
        case_id="case-1",
    )
    diagnosis.record_tool_evidence(
        case_id=case_id,
        session_id="session-1",
        evidence_records=[
            {
                "tool_name": "search_app_logs",
                "tool_call_id": "call-1",
                "evidence_id": "cls-1",
                "source": "local_logs",
                "success": True,
                "duration_ms": 5,
                "summary": "Milvus connection timeout repeated",
                "arguments": {"keyword": "timeout"},
                "raw_result": "timeout",
            }
        ],
    )
    diagnosis.complete_case(
        case_id,
        executed_steps=[("inspect logs", "timeout found")],
        final_report="Root cause: Milvus connection exhausted. Resolution: restart Milvus.",
    )
    feedback_id = diagnosis.record_feedback(
        case_id=case_id,
        session_id="session-1",
        user_accepted=True,
        actual_root_cause="Milvus connection exhausted",
        final_resolution="Restarted Milvus and reused client connections",
    )
    index = _NoopIndex()
    service = ExperienceMemoryService(
        db_path=tmp_path / "experience.sqlite3",
        diagnosis_memory_service=diagnosis,
        index_service=index,
    )

    experience_id = service.create_or_merge_from_feedback(
        case_id=case_id,
        feedback_id=feedback_id,
        project_id="super_biz_agent",
        environment="local",
        service_name="milvus",
    )

    memory = service.get_memory(experience_id)
    assert memory["symptoms"].startswith("diagnose Milvus timeout")
    assert memory["root_cause"] == "Milvus connection exhausted"
    assert memory["resolution"] == "Restarted Milvus and reused client connections"
    assert "search_app_logs" in memory["evidence_summary"]
    assert "cls-1" in memory["evidence_summary"]
    assert index.upserts[0]["experience_id"] == experience_id


def test_create_or_merge_from_feedback_uses_latest_accepted_feedback(tmp_path):
    diagnosis = DiagnosisMemoryService(tmp_path / "diagnosis.sqlite3")
    case_id = diagnosis.create_case(
        session_id="session-1",
        user_input="diagnose repeated timeout",
        case_id="case-1",
    )
    diagnosis.complete_case(
        case_id,
        executed_steps=[("inspect logs", "timeout found")],
        final_report="Root cause: earlier report. Resolution: earlier fix.",
    )
    diagnosis.record_feedback(
        case_id=case_id,
        session_id="session-1",
        user_accepted=True,
        actual_root_cause="Old root cause",
        final_resolution="Old resolution",
    )
    latest_feedback_id = diagnosis.record_feedback(
        case_id=case_id,
        session_id="session-1",
        user_accepted=True,
        actual_root_cause="Latest root cause",
        final_resolution="Latest resolution",
    )
    service = ExperienceMemoryService(
        db_path=tmp_path / "experience.sqlite3",
        diagnosis_memory_service=diagnosis,
        index_service=_NoopIndex(),
    )

    experience_id = service.create_or_merge_from_feedback(
        case_id=case_id,
        feedback_id=latest_feedback_id,
        project_id="super_biz_agent",
    )

    memory = service.get_memory(experience_id)
    assert memory["root_cause"] == "Latest root cause"
    assert memory["resolution"] == "Latest resolution"


def test_search_relevant_experiences_uses_project_filter_and_increments_hits(tmp_path):
    index = _SearchIndex()
    service = ExperienceMemoryService(db_path=tmp_path / "experience.sqlite3", index_service=index)
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
    )
    index.find_similar = lambda *, query, project_id, top_k: [
        {"experience_id": memory_id, "similarity": 0.9}
    ]

    results = service.search_relevant_experiences(
        query="API slow with Milvus timeout",
        project_id="super_biz_agent",
        top_k=3,
    )

    assert results[0]["experience_id"] == memory_id
    assert results[0]["similarity"] == 0.9
    assert service.get_memory(memory_id)["hit_count"] == 1


def test_disable_syncs_index_and_rebuild_indexes_enabled_memories(tmp_path):
    index = _SearchIndex()
    service = ExperienceMemoryService(db_path=tmp_path / "experience.sqlite3", index_service=index)
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
        project_id="super_biz_agent",
        environment="local",
        service_name="api",
        symptoms="API disk full",
        root_cause="disk saturation",
        resolution="expand disk",
        evidence_summary="evidence metric-1",
        source_case_id="case-2",
        source_feedback_id="feedback-2",
        confidence=0.8,
    )

    assert service.set_enabled(disabled_id, enabled=False) is True
    rebuilt_count = service.rebuild_index(project_id="super_biz_agent")

    assert index.disabled == [disabled_id]
    assert rebuilt_count == 1
    assert index.rebuilt[0][0]["experience_id"] == enabled_id


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
