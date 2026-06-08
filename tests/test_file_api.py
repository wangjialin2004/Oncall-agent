import pytest

from app.api.file import _sanitize_filename


@pytest.mark.asyncio
async def test_upload_reports_completed_indexing(monkeypatch, tmp_path, api_client):
    import app.api.file as file_api

    monkeypatch.setattr(file_api, "UPLOAD_DIR", tmp_path)

    def fake_index_single_file(file_path):
        return {"status": "completed", "chunk_count": 2, "error_message": ""}

    monkeypatch.setattr(file_api.vector_index_service, "index_single_file", fake_index_single_file)

    response = await api_client.post(
        "/api/upload",
        files={"file": ("note.md", b"# hello", "text/markdown")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["filename"] == "note.md"
    assert data["indexing_status"] == "completed"
    assert data["indexed_chunks"] == 2
    assert data["indexing_error"] == ""


@pytest.mark.asyncio
async def test_upload_reports_failed_indexing_without_failing_upload(
    monkeypatch, tmp_path, api_client
):
    import app.api.file as file_api

    monkeypatch.setattr(file_api, "UPLOAD_DIR", tmp_path)

    def fake_index_single_file(file_path):
        raise RuntimeError("Milvus unavailable")

    monkeypatch.setattr(file_api.vector_index_service, "index_single_file", fake_index_single_file)

    response = await api_client.post(
        "/api/upload",
        files={"file": ("note.md", b"# hello", "text/markdown")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["indexing_status"] == "failed"
    assert data["indexed_chunks"] == 0
    assert data["indexing_error"] == "Milvus unavailable"


@pytest.mark.asyncio
async def test_upload_rejects_oversized_replacement_without_deleting_existing_file(
    monkeypatch, tmp_path, api_client
):
    import app.api.file as file_api

    existing_file = tmp_path / "note.md"
    existing_file.write_bytes(b"original content")
    monkeypatch.setattr(file_api, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(file_api, "MAX_FILE_SIZE", 4)

    response = await api_client.post(
        "/api/upload",
        files={"file": ("note.md", b"too large", "text/markdown")},
    )

    assert response.status_code == 400
    assert existing_file.read_bytes() == b"original content"


def test_sanitize_filename_prefixes_windows_reserved_device_names():
    assert _sanitize_filename("CON.md") == "_CON.md"
    assert _sanitize_filename("nul.txt") == "_nul.txt"


def test_sanitize_filename_replaces_control_characters():
    assert _sanitize_filename("bad\n\tname.md") == "bad__name.md"


@pytest.mark.asyncio
async def test_index_directory_reports_invalid_path_as_client_error(tmp_path, api_client):
    missing_dir = tmp_path / "missing"

    response = await api_client.post(
        "/api/index_directory",
        params={"directory_path": str(missing_dir)},
    )

    assert response.status_code == 400
    assert "目录不存在或不是有效目录" in response.json()["detail"]
