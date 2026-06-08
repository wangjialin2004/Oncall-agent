import re
from pathlib import Path

from app.api.file import MAX_FILE_SIZE
from app.services.document_extraction_service import SUPPORTED_EXTENSIONS


def test_static_upload_accepts_backend_supported_document_extensions():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    index_html = Path("static/index.html").read_text(encoding="utf-8")

    expected_extensions = sorted(SUPPORTED_EXTENSIONS)
    allowed_match = re.search(r"allowedExtensions\s*=\s*\[(?P<items>[^\]]+)\]", app_js)
    assert allowed_match is not None
    allowed_extensions = sorted(re.findall(r"'([^']+)'", allowed_match.group("items")))

    accept_match = re.search(r'id="fileInput"\s+accept="(?P<accept>[^"]+)"', index_html)
    assert accept_match is not None
    accept_extensions = sorted(item.strip() for item in accept_match.group("accept").split(","))

    assert allowed_extensions == expected_extensions
    assert accept_extensions == expected_extensions


def test_static_upload_size_limit_matches_backend_limit():
    app_js = Path("static/app.js").read_text(encoding="utf-8")

    max_size_match = re.search(
        r"const\s+maxSize\s*=\s*(?P<mb>\d+)\s*\*\s*1024\s*\*\s*1024",
        app_js,
    )
    assert max_size_match is not None
    max_size_mb = int(max_size_match.group("mb"))

    assert max_size_mb * 1024 * 1024 == MAX_FILE_SIZE
    assert f"文件大小不能超过{max_size_mb}MB" in app_js


def test_static_upload_message_reflects_indexing_status():
    app_js = Path("static/app.js").read_text(encoding="utf-8")

    assert "uploadResult.indexing_status" in app_js
    assert "文件已上传，但知识库索引失败" in app_js
    assert "索引分片" in app_js


def test_static_frontend_uses_same_origin_api_base():
    app_js = Path("static/app.js").read_text(encoding="utf-8")

    assert "this.apiBaseUrl = '/api'" in app_js
    assert "http://localhost:9900/api" not in app_js
