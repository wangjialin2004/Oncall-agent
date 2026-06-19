from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.config import config


class AuthService:
    token_version = "v1"

    def create_access_token(self, username: str) -> str:
        subject = username.strip()
        if not subject:
            raise ValueError("username is required")

        payload = {"sub": subject, "iat": int(time.time())}
        payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        payload_part = _b64encode(payload_bytes)
        signature = self._sign(payload_part)
        return f"{self.token_version}.{payload_part}.{signature}"

    def verify_access_token(self, token: str) -> str:
        try:
            version, payload_part, signature = token.split(".", 2)
        except ValueError as exc:
            raise ValueError("invalid token format") from exc

        if version != self.token_version:
            raise ValueError("unsupported token version")

        expected_signature = self._sign(payload_part)
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("invalid token signature")

        try:
            payload = json.loads(_b64decode(payload_part).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid token payload") from exc

        subject = str(payload.get("sub") or "").strip()
        if not subject:
            raise ValueError("token subject is required")
        return subject

    def owner_key_for_user(self, username: str) -> str:
        subject = username.strip()
        if not subject:
            raise ValueError("username is required")
        return hashlib.sha256(f"user:{subject}".encode("utf-8")).hexdigest()[:8]

    def _sign(self, payload_part: str) -> str:
        secret = config.auth_token_secret.encode("utf-8")
        digest = hmac.new(secret, payload_part.encode("utf-8"), hashlib.sha256).digest()
        return _b64encode(digest)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


auth_service = AuthService()
