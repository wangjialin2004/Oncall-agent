import re

from app.services.document_extraction_service import SUPPORTED_EXTENSIONS


def test_static_upload_accepts_backend_supported_document_extensions():
    app_js = open("static/app.js", encoding="utf-8").read()
    index_html = open("static/index.html", encoding="utf-8").read()

    expected_extensions = sorted(SUPPORTED_EXTENSIONS)
    allowed_match = re.search(r"allowedExtensions\s*=\s*\[(?P<items>[^\]]+)\]", app_js)
    assert allowed_match is not None
    allowed_extensions = sorted(re.findall(r"'([^']+)'", allowed_match.group("items")))

    accept_match = re.search(r'id="fileInput"\s+accept="(?P<accept>[^"]+)"', index_html)
    assert accept_match is not None
    accept_extensions = sorted(item.strip() for item in accept_match.group("accept").split(","))

    assert allowed_extensions == expected_extensions
    assert accept_extensions == expected_extensions
