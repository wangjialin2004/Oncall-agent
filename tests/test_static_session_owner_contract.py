from pathlib import Path


def test_static_frontend_sends_session_owner_header_for_session_requests():
    app_js = Path("static/app.js").read_text(encoding="utf-8")

    assert "this.sessionOwnerToken = this.getSessionOwnerToken();" in app_js
    assert "localStorage.getItem('sessionOwnerToken')" in app_js
    assert "'X-Session-Owner': this.sessionOwnerToken" in app_js

    for marker in [
        "`${this.apiBaseUrl}/chat`",
        "`${this.apiBaseUrl}/chat_stream`",
        "`/api/chat/session/${historyId}`",
        "'/api/chat/clear'",
        "`${this.apiBaseUrl}/aiops`",
    ]:
        start = app_js.index(marker)
        snippet = app_js[start : start + 500]
        assert "this.sessionHeaders" in snippet
