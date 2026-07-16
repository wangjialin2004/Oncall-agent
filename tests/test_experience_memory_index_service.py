from app.services.experience_memory_index_service import _milvus_row


def test_milvus_row_includes_required_experience_schema_fields() -> None:
    row = _milvus_row(
        {
            "experience_id": "exp-1",
            "project_id": "proj",
            "environment": "prod",
            "service_name": "api",
            "source_type": "manual",
            "symptoms": "db pool exhausted",
            "root_cause": "connection leak",
            "resolution": "recycle pool",
            "source_event_ids": ["case-1"],
            "confidence": 0.9,
            "enabled": True,
        },
        [0.1, 0.2],
    )

    assert row["memory_type"] == "manual"
    assert row["root_cause"] == "connection leak"
    assert row["resolution"] == "recycle pool"
    assert row["source_case_ids_json"] == '["case-1"]'
