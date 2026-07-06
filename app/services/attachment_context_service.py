"""Build per-request attachment context for the assistant."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from app.config import config
from app.services.document_extraction_service import document_extraction_service
from app.services.file_storage_service import file_storage_service


def _truncate(text: str, limit: int) -> str:
    normalized = (text or "").strip()
    if limit <= 0 or len(normalized) <= limit:
        return normalized
    omitted = len(normalized) - limit
    return f"{normalized[:limit]}\n\n[附件内容已截断，省略 {omitted} 个字符]"


@dataclass(frozen=True, slots=True)
class AttachmentDocument:
    file_id: str
    file_name: str
    status: str
    content: str


class AttachmentContextService:
    """Resolve uploaded files into a safe text block for one assistant turn."""

    def __init__(self, max_chars: int | None = None):
        self.max_chars = (
            max_chars
            if max_chars is not None
            else int(getattr(config, "harness_attachment_context_max_chars", 12000))
        )

    async def build_context(self, owner_key: str, attachment_ids: list[str]) -> str:
        documents = await self.load_documents(owner_key, attachment_ids)
        return self.build_context_from_documents(documents)

    async def load_documents(
        self, owner_key: str, attachment_ids: list[str]
    ) -> list[AttachmentDocument]:
        documents: list[AttachmentDocument] = []
        for file_id in attachment_ids:
            if not file_id:
                continue
            documents.append(await self._load_one(owner_key, file_id))
        return documents

    def build_context_from_documents(self, documents: list[AttachmentDocument]) -> str:
        sections = [self._render_document(document) for document in documents]
        return "\n\n".join(section for section in sections if section.strip())

    async def _load_one(self, owner_key: str, file_id: str) -> AttachmentDocument:
        if not file_id.startswith("file_"):
            raise HTTPException(status_code=400, detail=f"invalid attachment id: {file_id}")

        data, meta, original_name = await file_storage_service.download(owner_key, file_id)
        cache_path = file_storage_service._cached_path(owner_key, file_id)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)

        extracted = document_extraction_service.extract_file(str(cache_path))
        content = _truncate(extracted.content, self.max_chars)
        status = str(meta.get("status") or "")
        return AttachmentDocument(
            file_id=file_id,
            file_name=original_name,
            status=status,
            content=content,
        )

    @staticmethod
    def _render_document(document: AttachmentDocument) -> str:
        return (
            f"[附件 {document.file_name}]"
            f"\nfile_id: {document.file_id}"
            f"\nstatus: {document.status}"
            f"\ncontent:\n{document.content}"
        )


attachment_context_service = AttachmentContextService()
