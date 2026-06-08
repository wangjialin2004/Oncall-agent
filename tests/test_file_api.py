from fastapi.testclient import TestClient

from app.main import app


def test_upload_reports_completed_indexing(monkeypatch, tmp_path):
    import app.api.file as file_api

    monkeypatch.setattr(file_api, "UPLOAD_DIR", tmp_path)

    def fake_index_single_file(file_path):
        return {"status": "completed", "chunk_count": 2, "error_message": ""}

    monkeypatch.setattr(file_api.vector_index_service, "index_single_file", fake_index_single_file)

    client = TestClient(app)
    response = client.post(
        "/api/upload",
        files={"file": ("note.md", b"# hello", "text/markdown")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["filename"] == "note.md"
    assert data["indexing_status"] == "completed"
    assert data["indexed_chunks"] == 2
    assert data["indexing_error"] == ""


def test_upload_reports_failed_indexing_without_failing_upload(monkeypatch, tmp_path):
    import app.api.file as file_api

    monkeypatch.setattr(file_api, "UPLOAD_DIR", tmp_path)

    def fake_index_single_file(file_path):
        raise RuntimeError("Milvus unavailable")

    monkeypatch.setattr(file_api.vector_index_service, "index_single_file", fake_index_single_file)

    client = TestClient(app)
    response = client.post(
        "/api/upload",
        files={"file": ("note.md", b"# hello", "text/markdown")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["indexing_status"] == "failed"
    assert data["indexed_chunks"] == 0
    assert data["indexing_error"] == "Milvus unavailable"


def test_upload_rejects_oversized_replacement_without_deleting_existing_file(monkeypatch, tmp_path):
    import app.api.file as file_api

    existing_file = tmp_path / "note.md"
    existing_file.write_bytes(b"original content")
    monkeypatch.setattr(file_api, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(file_api, "MAX_FILE_SIZE", 4)

    client = TestClient(app)
    response = client.post(
        "/api/upload",
        files={"file": ("note.md", b"too large", "text/markdown")},
    )

    assert response.status_code == 400
    assert existing_file.read_bytes() == b"original content"
