"""File upload / management API.

Endpoints (mounted under ``/api`` by ``main.py``):

  POST   /files                  upload a file (multipart)
  GET    /files                  list current user's files
  GET    /files/{file_id}        fetch metadata
  GET    /files/{file_id}/download
                                 download raw bytes
  DELETE /files/{file_id}        soft delete (metadata + vectors + storage)
  POST   /files/{file_id}/reindex
                                 rebuild Milvus index
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response

from app.services.file_storage_service import file_storage_service
from app.services.session_scope_service import require_session_owner
from app.services.storage_adapter import safe_storage_name

router = APIRouter()


def _ok(data, message: str = "success", status: int = 200):
    return JSONResponse(
        status_code=status, content={"code": status, "message": message, "data": data}
    )


def _validate_file_id(file_id: str) -> None:
    """Reject anything that doesn't look like a UUID4-generated file id.

    This is a cheap front-line defense; the storage layer still scopes
    access by ``owner_key``, so even a known ID from another user returns
    404 via the service layer.
    """
    if not file_id.startswith("file_") or len(file_id) <= 5:
        raise HTTPException(status_code=400, detail="invalid file_id")


@router.post("/files")
async def upload_file(
    file: UploadFile = File(...),
    auto_index: bool = Form(
        False, description="If true, immediately index into Milvus after upload."
    ),
    owner_key: str = Depends(require_session_owner),
):
    """Upload a file. Returns ``{file, deduplicated}``.

    ``deduplicated=True`` means the bytes were already stored for this
    user (matched by SHA256); no new copy was written.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    try:
        metadata, dedup = await file_storage_service.upload(
            owner_key=owner_key,
            file_stream=file.file,
            original_name=file.filename,
            content_type=file.content_type or "",
            auto_index=bool(auto_index),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"upload failed: {exc}") from exc

    return _ok({"file": metadata, "deduplicated": dedup})


@router.get("/files")
async def list_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(
        None, description="Filter by status: pending | indexed | failed | deleted"
    ),
    owner_key: str = Depends(require_session_owner),
):
    items, total = file_storage_service.list(
        owner_key, page=page, page_size=page_size, status_filter=status
    )
    return _ok(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/files/{file_id}")
async def get_file_metadata(
    file_id: str,
    owner_key: str = Depends(require_session_owner),
):
    _validate_file_id(file_id)
    meta = file_storage_service.get(owner_key, file_id)
    return _ok({"file": meta})


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: str,
    owner_key: str = Depends(require_session_owner),
):
    _validate_file_id(file_id)
    data, meta, original_name = await file_storage_service.download(
        owner_key, file_id
    )
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{safe_storage_name(original_name)}"'
        ),
        "X-File-Id": meta["file_id"],
        "X-File-Hash": meta["file_hash"],
    }
    return Response(
        content=data,
        media_type=meta.get("mime_type") or "application/octet-stream",
        headers=headers,
    )


@router.delete("/files/{file_id}")
async def delete_file(
    file_id: str,
    owner_key: str = Depends(require_session_owner),
):
    _validate_file_id(file_id)
    await file_storage_service.delete(owner_key, file_id)
    return _ok({"file_id": file_id, "deleted": True})


@router.post("/files/{file_id}/reindex")
async def reindex_file(
    file_id: str,
    owner_key: str = Depends(require_session_owner),
):
    _validate_file_id(file_id)
    meta = await file_storage_service.reindex(owner_key, file_id)
    return _ok({"file": meta})