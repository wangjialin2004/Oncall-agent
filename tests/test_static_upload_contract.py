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


def test_static_upload_uses_fastapi_error_detail_on_http_error():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    upload_start = app_js.index("async uploadFile(file)")
    upload_end = app_js.index("formatFileSize(bytes)")
    upload_file = app_js[upload_start:upload_end]

    assert upload_file.index("const data = await response.json();") < upload_file.index(
        "if (!response.ok)"
    )
    assert "data?.detail" in upload_file
    assert "throw new Error(errorMessage)" in upload_file


def test_static_frontend_uses_same_origin_api_base():
    app_js = Path("static/app.js").read_text(encoding="utf-8")

    assert "this.apiBaseUrl = '/api'" in app_js
    assert "http://localhost:9900/api" not in app_js


def test_static_delete_history_uses_fastapi_error_detail_on_http_error():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    delete_start = app_js.index("async deleteChatHistory(historyId)")
    delete_end = app_js.index("toggleModeDropdown()", delete_start)
    delete_history = app_js[delete_start:delete_end]

    assert delete_history.index("const result = await response.json();") < delete_history.index(
        "if (!response.ok)"
    )
    assert "result?.detail" in delete_history
    assert "throw new Error(errorMessage)" in delete_history


def test_static_quick_chat_uses_unified_assistant_endpoint():
    app_js = Path("static/app.js").read_text(encoding="utf-8")

    assert "fetch(`${this.apiBaseUrl}/assistant`" in app_js
    assert "fetch(`${this.apiBaseUrl}/chat`" not in app_js


def test_static_quick_chat_uses_assistant_error_envelope_on_http_error():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    quick_chat_start = app_js.index("async sendQuickMessage(message)")
    quick_chat_end = app_js.index("async sendStreamMessage(message)")
    quick_chat = app_js[quick_chat_start:quick_chat_end]

    assert quick_chat.index("const data = await response.json();") < quick_chat.index(
        "if (!response.ok)"
    )
    assert "data?.data?.errorMessage" in quick_chat
    assert "throw new Error(errorMessage)" in quick_chat


def test_static_backend_history_maps_assistant_role_to_assistant_message_type():
    app_js = Path("static/app.js").read_text(encoding="utf-8")

    assert "msg.role === 'assistant' ? 'assistant'" in app_js
    assert "msg.role === 'user' ? 'user' : 'bot'" not in app_js
