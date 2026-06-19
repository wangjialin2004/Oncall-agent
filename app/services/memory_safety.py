from __future__ import annotations

import re

SECRET_PATTERNS = [
    re.compile(r"(?i)(token|api[_-]?key|secret|password)\s*[:=]\s*([^\s,;]+)"),
]
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")


def redact_memory_text(text: str) -> str:
    value = text or ""
    for pattern in SECRET_PATTERNS:
        value = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    value = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
    value = PHONE_PATTERN.sub("[REDACTED_PHONE]", value)
    return value
