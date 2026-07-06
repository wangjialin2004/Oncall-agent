from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from app.config import config
from app.services.file_storage_service import FileStorageService
from app.services.storage_adapter import StorageAdapter


class DummyStorageAdapter(StorageAdapter):
    backend_name: ClassVar[str] = "dummy"

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        return key

    async def get(self, key: str) -> bytes:
        return b""

    async def delete(self, key: str) -> bool:
        return True

    async def exists(self, key: str) -> bool:
        return True

    async def size(self, key: str) -> int:
        return 0

    def get_public_url(self, key: str, expires: int = 3600) -> str:
        return key


def test_cached_path_uses_storage_key_filename(monkeypatch, tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(config, "storage_cache_dir", str(cache_dir))

    service = FileStorageService(adapter=DummyStorageAdapter())
    monkeypatch.setattr(
        service,
        "_get_meta_unchecked",
        lambda owner_key, file_id: {
            "file_id": file_id,
            "owner_key": owner_key,
            "storage_key": f"{owner_key}/{file_id}.txt",
        },
    )

    assert service._cached_path("owner1", "file_abc") == cache_dir / "owner1__file_abc.txt"
