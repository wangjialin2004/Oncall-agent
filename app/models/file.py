"""File upload API request/response models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class FileStatus(str, Enum):
    """File lifecycle status persisted in uploaded_files.status."""

    PENDING = "pending"   # 已落存储 / 元数据，未尝试索引
    INDEXED = "indexed"   # 已成功索引到 Milvus
    FAILED = "failed"     # 索引失败
    DELETED = "deleted"   # 软删除（不再出现在默认列表）


class FileMetadata(BaseModel):
    """File metadata returned by upload / list / get endpoints."""

    file_id: str
    owner_key: str
    original_name: str
    storage_backend: str
    storage_key: str
    size_bytes: int
    mime_type: str | None = None
    file_hash: str
    status: FileStatus
    indexed_chunks: int = 0
    indexing_error: str | None = None
    auto_indexed: bool = False
    created_at: str
    updated_at: str


class FileListResponse(BaseModel):
    """Paginated list response for GET /api/files."""

    items: list[FileMetadata]
    total: int
    page: int
    page_size: int


class UploadResult(BaseModel):
    """Returned by POST /api/files."""

    file: FileMetadata
    deduplicated: bool = Field(
        False,
        description="True if hit by hash dedup and no new bytes were stored",
    )