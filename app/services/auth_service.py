from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.config import config


class AuthService:
    token_version = "v1"

    def authenticate(self, username: str, password: str) -> bool:
        """Validate credentials against the fixed AUTH_USERS account table."""
        subject = (username or "").strip()
        if not subject or not password:
            return False
        expected = config.auth_user_map.get(subject)
        if expected is None:
            return False
        return hmac.compare_digest(expected, password)

    def create_access_token(self, username: str) -> str:
        subject = username.strip()
        if not subject:
            raise ValueError("username is required")

        now = int(time.time())
        payload: dict[str, object] = {"sub": subject, "iat": now}
        ttl = int(getattr(config, "auth_token_ttl_seconds", 0) or 0)
        if ttl > 0:
            payload["exp"] = now + ttl
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

        exp = payload.get("exp")
        if exp is not None:
            try:
                exp_ts = int(exp)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid token expiry") from exc
            if int(time.time()) >= exp_ts:
                raise ValueError("token expired")

        return subject

    def owner_key_for_user(self, username: str) -> str:
        """Return the legacy storage key used by pre-migration rows."""
        subject = username.strip()
        if not subject:
            raise ValueError("username is required")
        return hashlib.sha256(f"user:{subject}".encode()).hexdigest()[:8]

    def stable_owner_key_for_user(self, username: str) -> str:
        """Return a collision-resistant owner identifier for new request scope."""
        subject = username.strip()
        if not subject:
            raise ValueError("username is required")
        return hashlib.sha256(f"owner:v2:{subject}".encode()).hexdigest()

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
