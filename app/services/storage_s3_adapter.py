"""S3 v4 signed HTTP adapter — works for MinIO / Aliyun OSS / Tencent COS / AWS S3.

All four providers expose the same S3 v4 REST surface; only ``endpoint``,
``bucket``, and (optionally) URL style differ. We implement the canonical
``PUT / GET / HEAD / DELETE`` flows with ``httpx`` so we don't need to
pull in ``boto3`` / ``oss2`` / ``cos-python-sdk``.

Signing implementation follows AWS SigV4:
  https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import ClassVar
from urllib.parse import quote, urlparse

import httpx
from loguru import logger

from app.services.storage_adapter import STORAGE_BACKENDS, StorageAdapter


class _S3Signer:
    """AWS SigV4 signer for object-level S3 operations."""

    def __init__(self, access_key: str, secret_key: str, region: str, service: str = "s3"):
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.service = service

    def sign(
        self,
        method: str,
        url: str,
        *,
        body: bytes = b"",
        content_type: str = "",
        headers_extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Return a header dict with ``Authorization`` populated.

        Used for body-bearing requests (PUT, GET, HEAD, DELETE). Presigned
        query-string URLs are not currently emitted — the API layer always
        proxies downloads through ``/api/files/{id}/download`` instead.
        """
        parsed = urlparse(url)
        host = parsed.netloc
        canonical_uri = parsed.path or "/"

        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        payload_hash = hashlib.sha256(body).hexdigest()

        headers: dict[str, str] = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if content_type:
            headers["content-type"] = content_type
        if headers_extra:
            headers.update({k.lower(): v for k, v in headers_extra.items()})

        # Sort headers lexicographically for canonical form.
        sorted_header_names = sorted(headers.keys())
        canonical_headers = "".join(
            f"{k}:{headers[k].strip()}\n" for k in sorted_header_names
        )
        signed_headers = ";".join(sorted_header_names)

        canonical_request = (
            f"{method}\n"
            f"{canonical_uri}\n"
            f"\n"  # empty canonical querystring
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{payload_hash}"
        )

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/{self.service}/aws4_request"
        string_to_sign = (
            f"{algorithm}\n"
            f"{amz_date}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def _hmac(key: bytes, data: str) -> bytes:
            return hmac.new(key, data.encode(), hashlib.sha256).digest()

        k_date = _hmac(f"AWS4{self.secret_key}".encode(), date_stamp)
        k_region = _hmac(k_date, self.region)
        k_service = _hmac(k_region, self.service)
        k_signing = _hmac(k_service, "aws4_request")
        signature = hmac.new(
            k_signing, string_to_sign.encode(), hashlib.sha256
        ).hexdigest()

        authorization = (
            f"{algorithm} "
            f"Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        headers["Authorization"] = authorization
        return headers


class S3CompatibleAdapter(StorageAdapter):
    """S3 v4 signed adapter.

    The same code path serves ``minio``, ``oss``, ``s3``, and ``cos`` —
    point ``endpoint``/``bucket``/``url_style`` at the right host.
    """

    backend_name: ClassVar[str] = ""  # set by subclass

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        use_ssl: bool = False,
        url_style: str = "path",
        public_url_base: str = "",
    ):
        if not endpoint or not bucket:
            raise RuntimeError(
                "endpoint and bucket are required for S3-compatible adapter"
            )
        if not access_key or not secret_key:
            raise RuntimeError(
                "access_key and secret_key are required for S3-compatible adapter"
            )
        self.bucket = bucket
        self.url_style = url_style  # 'path' or 'virtual-hosted'
        self.signer = _S3Signer(access_key, secret_key, region)
        scheme = "https" if use_ssl else "http"
        self.base = f"{scheme}://{endpoint}"
        self.public_url_base = public_url_base.rstrip("/") if public_url_base else ""
        self._client = httpx.Client(timeout=60.0)

    # -- URL helpers --------------------------------------------------------

    def _object_url(self, key: str) -> str:
        """Build the absolute URL for an object key."""
        clean = key.lstrip("/")
        if self.url_style == "virtual-hosted":
            parsed = urlparse(self.base)
            host = f"{self.bucket}.{parsed.netloc}"
            return f"{parsed.scheme}://{host}/{clean}"
        return f"{self.base}/{self.bucket}/{clean}"

    def _public_url_for(self, key: str, expires: int) -> str:
        """Return an unauthenticated URL hint for the object.

        We don't fully implement SigV4 query-string presigning here — the
        API layer always proxies through ``/api/files/{id}/download`` for
        downloads. For external integrations that need direct object URLs,
        ``public_url_base`` (e.g. a CDN domain) is used; otherwise we emit
        a path-style object URL the client can sign with its own SDK.
        """
        if self.public_url_base:
            return f"{self.public_url_base}/{self.bucket}/{key.lstrip('/')}"
        # Minimal informational URL (unsigned; clients must sign if bucket
        # is private). Useful for debugging.
        return self._object_url(key)

    # -- CRUD ---------------------------------------------------------------

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        url = self._object_url(key)
        headers = self.signer.sign("PUT", url, body=data, content_type=content_type)
        resp = self._client.put(url, content=data, headers=headers)
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"S3 PUT failed: status={resp.status_code} body={resp.text[:200]!r}"
            )
        logger.debug("S3 PUT ok: key={} bytes={}", key, len(data))
        return key

    async def get(self, key: str) -> bytes:
        url = self._object_url(key)
        headers = self.signer.sign("GET", url)
        resp = self._client.get(url, headers=headers)
        if resp.status_code == 404:
            raise FileNotFoundError(key)
        if resp.status_code != 200:
            raise RuntimeError(
                f"S3 GET failed: status={resp.status_code} body={resp.text[:200]!r}"
            )
        return resp.content

    async def delete(self, key: str) -> bool:
        url = self._object_url(key)
        headers = self.signer.sign("DELETE", url)
        resp = self._client.delete(url, headers=headers)
        # 204 No Content (deleted) or 404 (already gone) both count as success.
        return resp.status_code in (200, 204, 404)

    async def exists(self, key: str) -> bool:
        url = self._object_url(key)
        headers = self.signer.sign("HEAD", url)
        resp = self._client.head(url, headers=headers)
        return resp.status_code == 200

    async def size(self, key: str) -> int:
        url = self._object_url(key)
        headers = self.signer.sign("HEAD", url)
        resp = self._client.head(url, headers=headers)
        if resp.status_code != 200:
            return -1
        try:
            return int(resp.headers.get("content-length", "-1"))
        except ValueError:
            return -1

    def get_public_url(self, key: str, expires: int = 3600) -> str:
        return self._public_url_for(key, expires)


# ---------------------------------------------------------------- subclasses


class MinioStorageAdapter(S3CompatibleAdapter):
    backend_name: ClassVar[str] = "minio"


class OssStorageAdapter(S3CompatibleAdapter):
    backend_name: ClassVar[str] = "oss"


class S3StorageAdapter(S3CompatibleAdapter):
    backend_name: ClassVar[str] = "s3"


class CosStorageAdapter(S3CompatibleAdapter):
    backend_name: ClassVar[str] = "cos"


# Register subclasses with the factory in storage_adapter.
for _cls in (MinioStorageAdapter, OssStorageAdapter, S3StorageAdapter, CosStorageAdapter):
    STORAGE_BACKENDS[_cls.backend_name] = _cls