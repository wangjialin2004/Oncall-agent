"""Document splitting service without external framework dependencies."""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import config
from app.models.document import RetrievedDocument


class DocumentSplitterService:
    """Split extracted documents into searchable chunks."""

    def __init__(self):
        self.chunk_size = config.chunk_max_size
        self.chunk_overlap = config.chunk_overlap
        self.secondary_chunk_size = self.chunk_size * 2

        logger.info(
            f"Document splitter initialized: chunk_size={self.chunk_size}, "
            f"secondary_chunk_size={self.secondary_chunk_size}, overlap={self.chunk_overlap}"
        )

    def split_markdown(
        self,
        content: str,
        file_path: str = "",
        base_metadata: dict[str, Any] | None = None,
    ) -> list[RetrievedDocument]:
        """Split markdown-like content by h1/h2 headings and chunk size."""
        if not content or not content.strip():
            logger.warning(f"Markdown document is empty: {file_path}")
            return []

        try:
            sections = self._split_markdown_sections(content)
            docs_after_split = self._split_documents_by_size(sections)
            final_docs = self._merge_small_chunks(docs_after_split, min_size=300)
            self._apply_chunk_metadata(final_docs, file_path, base_metadata)

            logger.info(f"Markdown split complete: {file_path} -> {len(final_docs)} chunks")
            return final_docs
        except Exception as e:
            logger.error(f"Markdown split failed: {file_path}, error: {e}")
            raise

    def split_text(
        self,
        content: str,
        file_path: str = "",
        base_metadata: dict[str, Any] | None = None,
    ) -> list[RetrievedDocument]:
        """Split plain text content."""
        if not content or not content.strip():
            logger.warning(f"Text document is empty: {file_path}")
            return []

        try:
            docs = self._split_documents_by_size([RetrievedDocument(page_content=content)])
            self._apply_chunk_metadata(docs, file_path, base_metadata)

            logger.info(f"Text split complete: {file_path} -> {len(docs)} chunks")
            return docs
        except Exception as e:
            logger.error(f"Text split failed: {file_path}, error: {e}")
            raise

    def split_document(
        self,
        content: str,
        file_path: str = "",
        base_metadata: dict[str, Any] | None = None,
    ) -> list[RetrievedDocument]:
        """Split a document using markdown-aware logic for extracted rich documents."""
        if Path(file_path).suffix.lower() in {".md", ".markdown", ".pdf", ".docx"}:
            return self.split_markdown(content, file_path, base_metadata)
        return self.split_text(content, file_path, base_metadata)

    def _split_markdown_sections(self, content: str) -> list[RetrievedDocument]:
        docs: list[RetrievedDocument] = []
        current_lines: list[str] = []
        current_metadata: dict[str, Any] = {}
        active_h1 = ""
        active_h2 = ""

        def flush() -> None:
            text = "\n".join(current_lines).strip()
            if text:
                docs.append(RetrievedDocument(page_content=text, metadata=dict(current_metadata)))

        for line in content.splitlines():
            match = re.match(r"^(#{1,2})\s+(.+?)\s*$", line)
            if match:
                flush()
                current_lines = [line]
                level = len(match.group(1))
                title = match.group(2).strip()
                if level == 1:
                    active_h1 = title
                    active_h2 = ""
                else:
                    active_h2 = title
                current_metadata = {}
                if active_h1:
                    current_metadata["h1"] = active_h1
                if active_h2:
                    current_metadata["h2"] = active_h2
                continue

            current_lines.append(line)

        flush()

        if docs:
            return docs
        return [RetrievedDocument(page_content=content.strip(), metadata={})]

    def _split_documents_by_size(
        self,
        documents: list[RetrievedDocument],
    ) -> list[RetrievedDocument]:
        split_docs: list[RetrievedDocument] = []
        for doc in documents:
            for chunk in self._split_text_by_size(doc.page_content):
                split_docs.append(
                    RetrievedDocument(page_content=chunk, metadata=dict(doc.metadata))
                )
        return split_docs

    def _split_text_by_size(self, text: str) -> list[str]:
        text = text.strip()
        if len(text) <= self.secondary_chunk_size:
            return [text] if text else []

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + self.secondary_chunk_size, len(text))
            if end < len(text):
                boundary = self._find_split_boundary(text, start, end)
                if boundary > start:
                    end = boundary

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            if end >= len(text):
                break
            start = max(end - self.chunk_overlap, start + 1)

        return chunks

    def _find_split_boundary(self, text: str, start: int, end: int) -> int:
        min_boundary = start + max(1, self.secondary_chunk_size // 2)
        for separator in ["\n\n", "\n", "。", ". ", " "]:
            boundary = text.rfind(separator, min_boundary, end)
            if boundary != -1:
                return boundary + len(separator)
        return end

    def _merge_small_chunks(
        self,
        documents: list[RetrievedDocument],
        min_size: int = 300,
    ) -> list[RetrievedDocument]:
        """Merge very small adjacent chunks."""
        if not documents:
            return []

        merged_docs: list[RetrievedDocument] = []
        current_doc: RetrievedDocument | None = None

        for doc in documents:
            doc_size = len(doc.page_content)

            if current_doc is None:
                current_doc = doc
            elif doc_size < min_size and len(current_doc.page_content) < self.secondary_chunk_size:
                current_doc.page_content += "\n\n" + doc.page_content
            else:
                merged_docs.append(current_doc)
                current_doc = doc

        if current_doc is not None:
            merged_docs.append(current_doc)

        return merged_docs

    def _apply_chunk_metadata(
        self,
        documents: list[RetrievedDocument],
        file_path: str,
        base_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add consistent indexing metadata to each chunk."""

        source_path = file_path or (base_metadata or {}).get("source", "")
        path = Path(source_path)
        extension = path.suffix or (base_metadata or {}).get("extension", "")
        file_name = path.name or (base_metadata or {}).get("file_name", "")
        file_hash = (base_metadata or {}).get("file_hash") or self._fallback_hash(source_path)
        created_at = datetime.now(UTC).isoformat()

        for index, doc in enumerate(documents):
            metadata = {
                **(base_metadata or {}),
                **doc.metadata,
                "_source": source_path,
                "_extension": extension,
                "_file_name": file_name,
                "source": source_path,
                "file_name": file_name,
                "extension": extension,
                "file_hash": file_hash,
                "chunk_index": index,
                "chunk_id": f"{str(file_hash)[:12]}:{index}",
                "heading_path": self._heading_path(doc.metadata),
                "content_type": self._guess_content_type(doc.page_content),
                "created_at": created_at,
                "content_length": len(doc.page_content),
            }
            doc.metadata = metadata

    def _heading_path(self, metadata: dict[str, Any]) -> str:
        headers = []
        for key in ["h1", "h2", "h3"]:
            value = metadata.get(key)
            if value:
                headers.append(str(value))
        return " > ".join(headers)

    def _guess_content_type(self, content: str) -> str:
        has_image = "[鍥剧墖]" in content or bool(re.search(r"!\[[^\]]*\]\([^)]+\)", content))
        has_table = self._contains_markdown_table(content)
        if has_image and has_table:
            return "mixed"
        if has_table:
            return "table"
        if has_image:
            return "image"
        return "text"

    def _contains_markdown_table(self, content: str) -> bool:
        lines = [line.strip() for line in content.splitlines()]
        for index in range(len(lines) - 1):
            if lines[index].startswith("|") and self._is_markdown_table_separator(lines[index + 1]):
                return True
        return False

    def _is_markdown_table_separator(self, line: str) -> bool:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell)

    def _fallback_hash(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


document_splitter_service = DocumentSplitterService()
