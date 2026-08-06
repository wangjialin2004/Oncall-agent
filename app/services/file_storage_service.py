"""Metadata CRUD + upload/download orchestration for the file-storage layer.

Owns the ``uploaded_files`` SQLite table and orchestrates a swappable
``StorageAdapter`` (``local`` by default, MinIO/OSS/S3/COS optional) plus
optional auto-indexing into Milvus via ``vector_index_service``.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import HTTPException
from loguru import logger

from app.config import config
from app.services.document_extraction_service import SUPPORTED_EXTENSIONS
from app.services.storage_adapter import (
    StorageAdapter,
    get_storage_adapter,
)
from app.services.vector_index_service import (
    SingleFileIndexingResult,
    vector_index_service,
)
from app.services.vector_store_manager import vector_store_manager

STATUS_PENDING = "pending"
STATUS_INDEXED = "indexed"
STATUS_FAILED = "failed"
STATUS_DELETED = "deleted"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gen_file_id() -> str:
    return f"file_{uuid.uuid4().hex}"


class FileStorageService:
    """Owner-scoped file metadata + storage adapter orchestration."""

    def __init__(self, adapter: StorageAdapter | None = None):
        self.adapter: StorageAdapter = adapter or get_storage_adapter()
        self._initialized = False

    # ------------------------------------------------------------------ schema
    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        db_path = Path(config.memory_db_path)
        if bool(getattr(config, "db_schema_enforcement_enabled", False)):
            from app.services.database_migration_service import DatabaseMigrationService

            DatabaseMigrationService(db_path).require_current()
            self._initialized = True
            return
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS uploaded_files (
                    id              TEXT PRIMARY KEY,
                    owner_key       TEXT NOT NULL,
                    original_name   TEXT NOT NULL,
                    stored_name     TEXT NOT NULL,
                    storage_backend TEXT NOT NULL,
                    storage_key     TEXT NOT NULL,
                    size_bytes      INTEGER NOT NULL,
                    mime_type       TEXT,
                    file_hash       TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    indexed_chunks  INTEGER NOT NULL DEFAULT 0,
                    indexing_error  TEXT,
                    auto_indexed    INTEGER NOT NULL DEFAULT 0,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    UNIQUE(owner_key, file_hash)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_uploaded_files_owner "
                "ON uploaded_files (owner_key)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_uploaded_files_hash "
                "ON uploaded_files (file_hash)"
            )
            # WAL mode helps avoid 'database is locked' under concurrent writes.
            try:
                connection.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
        self._initialized = True

    @contextmanager
    def _connection(self):
        self._ensure_schema()
        connection = sqlite3.connect(str(config.memory_db_path))
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _allowed_extensions() -> set[str]:
        cfg_exts = {
            e.strip().lower()
            for e in config.storage_allowed_extensions.split(",")
            if e.strip()
        }
        # Always intersect with the parser's supported set so we never let
        # through an extension that downstream extraction can't handle.
        parser_exts = {ext.lower() for ext in SUPPORTED_EXTENSIONS}
        return cfg_exts & parser_exts

    def _validate_extension(self, original_name: str) -> str:
        ext = Path(original_name).suffix.lower()
        if ext not in self._allowed_extensions():
            raise HTTPException(
                status_code=400,
                detail=f"unsupported file extension '{ext}'",
            )
        return ext

    @staticmethod
    def _make_storage_key(owner_key: str, file_id: str, ext: str) -> str:
        return f"{owner_key}/{file_id}{ext}"

    def _row_to_metadata(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "file_id": row["id"],
            "owner_key": row["owner_key"],
            "original_name": row["original_name"],
            "storage_backend": row["storage_backend"],
            "storage_key": row["storage_key"],
            "size_bytes": int(row["size_bytes"]),
            "mime_type": row["mime_type"],
            "file_hash": row["file_hash"],
            "status": row["status"],
            "indexed_chunks": int(row["indexed_chunks"]),
            "indexing_error": row["indexing_error"],
            "auto_indexed": bool(row["auto_indexed"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    async def _read_and_hash(
        src: BinaryIO, max_bytes: int
    ) -> tuple[bytes, str, int]:
        """Stream-read the upload, cap size, compute SHA256.

        Returns ``(data, sha256_hex, size)``. Raises HTTP 400 if the cap
        is exceeded. Empty payload also raises.
        """
        sha = hashlib.sha256()
        total = 0
        buf = bytearray()
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=400,
                    detail=f"file exceeds max size {max_bytes // (1024 * 1024)} MB",
                )
            buf.extend(chunk)
            sha.update(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="empty file")
        return bytes(buf), sha.hexdigest(), total

    # ------------------------------------------------------------------ public

    async def upload(
        self,
        *,
        owner_key: str,
        file_stream: BinaryIO,
        original_name: str,
        content_type: str,
        auto_index: bool,
    ) -> tuple[dict[str, Any], bool]:
        """Upload a file. Returns ``(metadata_dict, deduplicated_bool)``.

        ``deduplicated=True`` means a row with the same ``(owner_key, hash)``
        already existed and we did NOT write new bytes; the returned
        metadata belongs to the existing row.
        """
        if not original_name:
            raise HTTPException(status_code=400, detail="filename is required")

        ext = self._validate_extension(original_name)
        max_bytes = config.storage_max_file_size_mb * 1024 * 1024
        data, file_hash, size = await self._read_and_hash(file_stream, max_bytes)

        # 1) Dedup hit?
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM uploaded_files "
                "WHERE owner_key = ? AND file_hash = ? AND status != ?",
                (owner_key, file_hash, STATUS_DELETED),
            ).fetchone()
        if existing is not None:
            logger.info(
                "upload dedup hit: owner={} hash={} -> file_id={}",
                owner_key,
                file_hash,
                existing["id"],
            )
            return self._row_to_metadata(existing), True

        # 2) Write to storage backend.
        file_id = _gen_file_id()
        storage_key = self._make_storage_key(owner_key, file_id, ext)
        await self.adapter.put(storage_key, data, content_type=content_type)

        # 3) Persist metadata (status=pending until indexing finishes).
        stored_name = Path(storage_key).name
        now = _utc_now()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO uploaded_files (
                        id, owner_key, original_name, stored_name,
                        storage_backend, storage_key, size_bytes, mime_type,
                        file_hash, status, indexed_chunks, indexing_error,
                        auto_indexed, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        file_id,
                        owner_key,
                        original_name,
                        stored_name,
                        self.adapter.backend_name,
                        storage_key,
                        size,
                        content_type or None,
                        file_hash,
                        STATUS_PENDING,
                        0,
                        None,
                        1 if auto_index else 0,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            # Race: another request just inserted the same (owner, hash).
            # Drop the bytes we just wrote, return the existing row.
            try:
                await self.adapter.delete(storage_key)
            except Exception as exc:  # pragma: no cover - best effort cleanup
                logger.warning("orphan storage cleanup failed: {}", exc)
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM uploaded_files "
                    "WHERE owner_key = ? AND file_hash = ?",
                    (owner_key, file_hash),
                ).fetchone()
            if row is None:
                raise HTTPException(status_code=500, detail="dedup race lost")
            return self._row_to_metadata(row), True

        # 4) Optional auto-index (synchronous; failure only sets status=failed).
        if auto_index:
            await self._run_indexing(owner_key, file_id, storage_key)

        return self.get(owner_key, file_id), False

    def get(self, owner_key: str, file_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM uploaded_files WHERE owner_key = ? AND id = ?",
                (owner_key, file_id),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="file not found")
        return self._row_to_metadata(row)

    def list(
        self,
        owner_key: str,
        *,
        page: int = 1,
        page_size: int = 20,
        status_filter: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size
        params: list[Any] = [owner_key]
        # Default listing hides soft-deleted rows. When the caller asks for
        # a specific status (including "deleted"), honor that filter and
        # skip the implicit exclusion.
        if status_filter:
            where = "WHERE owner_key = ? AND status = ?"
            params.append(status_filter)
        else:
            where = "WHERE owner_key = ? AND status != 'deleted'"
        with self._connection() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM uploaded_files {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                f"SELECT * FROM uploaded_files {where} "
                f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
        return [self._row_to_metadata(r) for r in rows], int(total)

    async def delete(self, owner_key: str, file_id: str) -> bool:
        meta = self.get(owner_key, file_id)
        storage_key = meta["storage_key"]
        # Best-effort: clean Milvus first.
        try:
            vector_store_manager.delete_by_source(
                self._index_source_for(owner_key, file_id)
            )
        except Exception as exc:
            logger.warning("delete vectors failed: {}", exc)
        # Best-effort: delete storage object.
        try:
            await self.adapter.delete(storage_key)
        except Exception as exc:
            logger.warning("delete storage failed: key={} err={}", storage_key, exc)
        # Also clean up the local cache copy used for indexing.
        try:
            cached = self._cached_path(owner_key, file_id)
            if cached.exists() and cached.is_file():
                cached.unlink()
        except Exception as exc:
            logger.warning("delete cache failed: err={}", exc)
        # Soft-delete in metadata.
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                "UPDATE uploaded_files SET status=?, updated_at=? "
                "WHERE owner_key=? AND id=?",
                (STATUS_DELETED, now, owner_key, file_id),
            )
        return True

    async def download(
        self, owner_key: str, file_id: str
    ) -> tuple[bytes, dict[str, Any], str]:
        meta = self.get(owner_key, file_id)
        data = await self.adapter.get(meta["storage_key"])
        return data, meta, meta["original_name"]

    async def reindex(self, owner_key: str, file_id: str) -> dict[str, Any]:
        meta = self.get(owner_key, file_id)
        await self._run_indexing(owner_key, file_id, meta["storage_key"])
        return self.get(owner_key, file_id)

    # ------------------------------------------------------------------ internals

    def _index_source_for(self, owner_key: str, file_id: str) -> str:
        """The string passed to ``vector_store_manager.delete_by_source()``.

        Must match the ``source`` value used when ``index_single_file`` was
        first called. We always use the cached local copy's resolved
        posix path, so reindex/delete-from-vector match the first write.
        """
        return str(self._cached_path(owner_key, file_id).resolve().as_posix())

    def _cached_path(self, owner_key: str, file_id: str) -> Path:
        """Local cache file path used to feed ``vector_index_service``.

        The path is **stable** across uploads / reindexes for the same
        ``(owner_key, file_id)`` so Milvus ``_source`` keying stays
        consistent and ``delete_by_source`` works.
        """
        # Use the storage_key suffix (file_id + ext) so cached path mirrors
        # the stored name. Falling back to ``file_id`` (no ext) is safe.
        meta = self._get_meta_unchecked(owner_key, file_id)
        stored = Path(str(meta["storage_key"])).name if meta else file_id
        cache_dir = Path(config.storage_cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / f"{owner_key}__{stored}"

    def _get_meta_unchecked(
        self, owner_key: str, file_id: str
    ) -> dict[str, Any] | None:
        try:
            return self.get(owner_key, file_id)
        except HTTPException:
            return None

    async def _run_indexing(
        self, owner_key: str, file_id: str, storage_key: str
    ) -> None:
        """Run ``vector_index_service.index_single_file`` on a stable local
        copy. Failures are caught and recorded on the metadata row, but
        never bubble up — upload/return path is decoupled from indexing.
        """
        try:
            cached = self._cached_path(owner_key, file_id)
            cached.parent.mkdir(parents=True, exist_ok=True)
            data = await self.adapter.get(storage_key)
            cached.write_bytes(data)

            result: SingleFileIndexingResult = vector_index_service.index_single_file(
                str(cached.resolve()),
                scope_type="user",
                scope_id=owner_key,
            )
            status = (
                STATUS_INDEXED
                if result.status in ("completed", "skipped")
                else STATUS_FAILED
            )
            err = "" if status == STATUS_INDEXED else (
                result.error_message or "unknown error"
            )
            with self._connection() as connection:
                connection.execute(
                    "UPDATE uploaded_files "
                    "SET status=?, indexed_chunks=?, indexing_error=?, updated_at=? "
                    "WHERE owner_key=? AND id=?",
                    (
                        status,
                        int(result.chunk_count),
                        err or None,
                        _utc_now(),
                        owner_key,
                        file_id,
                    ),
                )
            logger.info(
                "reindex done: owner={} id={} status={} chunks={}",
                owner_key,
                file_id,
                status,
                result.chunk_count,
            )
        except Exception as exc:
            logger.exception("reindex failed: id={} err={}", file_id, exc)
            with self._connection() as connection:
                connection.execute(
                    "UPDATE uploaded_files "
                    "SET status=?, indexing_error=?, updated_at=? "
                    "WHERE owner_key=? AND id=?",
                    (STATUS_FAILED, str(exc), _utc_now(), owner_key, file_id),
                )


file_storage_service = FileStorageService()
