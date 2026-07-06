"""Storage adapter abstraction + local filesystem implementation.

Backends are registered via ``STORAGE_BACKENDS`` dict so the factory
``get_storage_adapter()`` can look them up from
``config.storage_backend``.

The local backend writes to ``config.storage_local_root``; S3-compatible
backends (MinIO / Aliyun OSS / AWS S3 / Tencent COS) are implemented in
``storage_s3_adapter.py`` using ``httpx`` + AWS SigV4 signing so we don't
introduce ``boto3`` / ``oss2`` / ``cos-python-sdk`` dependencies.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

import aiofiles
from loguru import logger

from app.config import config

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class StorageAdapter(ABC):
    """Abstract byte-store interface used by ``FileStorageService``."""

    backend_name: ClassVar[str] = ""

    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        """Store ``data`` at ``key``; return the canonical storage key used."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Return raw bytes for a key. Raise ``FileNotFoundError`` on miss."""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete the object; return ``True`` if it existed."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check whether an object exists."""

    @abstractmethod
    async def size(self, key: str) -> int:
        """Return object size in bytes; ``-1`` if unknown."""

    @abstractmethod
    def get_public_url(self, key: str, expires: int = 3600) -> str:
        """Return an HTTP URL the client can use to fetch the object.

        Local backend returns ``/api/files/{file_id}/download``.
        Remote backends return a presigned URL when possible.
        """


# ---------------------------------------------------------------- local backend


class LocalStorageAdapter(StorageAdapter):
    """Stores bytes under ``storage_local_root`` with relative ``key`` paths."""

    backend_name: ClassVar[str] = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        """Resolve ``key`` against ``self.root`` while preventing traversal."""
        clean = key.replace("\\", "/").lstrip("/")
        full = (self.root / clean).resolve()
        try:
            full.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"invalid storage key: {key}") from exc
        return full

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        logger.debug("local storage put: key={} bytes={}", key, len(data))
        return key

    async def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise FileNotFoundError(key)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def delete(self, key: str) -> bool:
        path = self._resolve(key)
        if path.exists() and path.is_file():
            path.unlink()
            return True
        return False

    async def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    async def size(self, key: str) -> int:
        path = self._resolve(key)
        return path.stat().st_size if path.exists() else -1

    def get_public_url(self, key: str, expires: int = 3600) -> str:
        """Local backend relies on the controlled ``/api/files/{id}/download``
        endpoint. ``key`` is ``<owner_key>/<file_id><ext>`` so we can recover
        the public ``file_id`` from it.
        """
        parts = key.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"cannot derive file_id from local storage key: {key}")
        return f"/api/files/{parts[1]}/download"


# ---------------------------------------------------------------- factory


STORAGE_BACKENDS: dict[str, type[StorageAdapter]] = {
    "local": LocalStorageAdapter,
}


def _build_backend(backend: str) -> StorageAdapter:
    cls = STORAGE_BACKENDS.get(backend)
    if cls is None:
        raise RuntimeError(
            f"unknown storage backend '{backend}'. "
            f"Available: {sorted(STORAGE_BACKENDS.keys())}"
        )
    if backend == "local":
        return cls(config.storage_local_root)

    # S3-compatible (minio/oss/s3/cos). Imported lazily so the local-only
    # path doesn't require httpx at runtime in case httpx is ever stripped.
    return cls(  # type: ignore[call-arg]
        endpoint=config.storage_endpoint,
        region=config.storage_region,
        access_key=config.storage_access_key,
        secret_key=config.storage_secret_key,
        bucket=config.storage_bucket,
        use_ssl=config.storage_use_ssl,
        url_style=config.storage_url_style,
        public_url_base=config.storage_public_url_base,
    )


_storage_adapter: StorageAdapter | None = None


def get_storage_adapter() -> StorageAdapter:
    """Module-level singleton, lazily initialized."""
    global _storage_adapter
    if _storage_adapter is None:
        backend = (config.storage_backend or "local").lower()
        _storage_adapter = _build_backend(backend)
        logger.info("storage adapter initialized: backend={}", backend)
    return _storage_adapter


def set_storage_adapter(adapter: StorageAdapter) -> None:
    """Test seam — overwrite the singleton."""
    global _storage_adapter
    _storage_adapter = adapter


def safe_storage_name(original_name: str) -> str:
    """Sanitize an original filename for echoing back in
    ``Content-Disposition``. Strips any directory parts and keeps only
    ``[A-Za-z0-9._-]``.
    """
    base = Path(original_name).name
    cleaned = _FILENAME_SAFE_RE.sub("_", base)
    return cleaned or "download.bin"


def run_sync(coro):
    """Helper to run an async adapter method from sync code.

    The upload endpoint (async) awaits directly. This helper exists for
    CLI / test scripts that need to call adapter methods from synchronous
    contexts (e.g. ``rebuild`` or migration jobs).
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return loop.create_task(coro)
    except RuntimeError:
        pass
    return asyncio.run(coro)