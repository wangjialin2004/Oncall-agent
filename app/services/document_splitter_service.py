"""文档分割服务模块 - 基于 LangChain 的智能文档分割"""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from loguru import logger

from app.config import config


class DocumentSplitterService:
    """文档分割服务 - 使用 LangChain 的分割器"""

    def __init__(self):
        """初始化文档分割服务"""
        self.chunk_size = config.chunk_max_size
        self.chunk_overlap = config.chunk_overlap

        # Markdown 标题分割器 (只按一级和二级标题分割，减少分片数)
        self.markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"),
                ("##", "h2"),
                # 不再按三级标题分割，避免过度碎片化
            ],
            strip_headers=False,  # 保留标题在内容中
        )

        # 递归字符分割器 (用于二次分割，使用更大的chunk_size)
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size * 2,  # 加倍chunk_size，减少分片数
            chunk_overlap=self.chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

        logger.info(
            f"文档分割服务初始化完成, chunk_size={self.chunk_size}, "
            f"secondary_chunk_size={self.chunk_size * 2}, "
            f"overlap={self.chunk_overlap}"
        )

    def split_markdown(
        self,
        content: str,
        file_path: str = "",
        base_metadata: dict[str, Any] | None = None,
    ) -> list[Document]:
        """
        分割 Markdown 文档 (两阶段分割 + 合并小片段)

        Args:
            content: Markdown 内容
            file_path: 文件路径 (用于元数据)

        Returns:
            List[Document]: 文档分片列表
        """
        if not content or not content.strip():
            logger.warning(f"Markdown 文档内容为空: {file_path}")
            return []

        try:
            # 第一阶段: 按标题分割
            md_docs = self.markdown_splitter.split_text(content)

            # 第二阶段: 按大小进一步分割
            docs_after_split = self.text_splitter.split_documents(md_docs)

            # 第三阶段: 合并太小的分片 (< 300字符)
            final_docs = self._merge_small_chunks(docs_after_split, min_size=300)

            self._apply_chunk_metadata(final_docs, file_path, base_metadata)

            logger.info(f"Markdown 分割完成: {file_path} -> {len(final_docs)} 个分片")
            return final_docs

        except Exception as e:
            logger.error(f"Markdown 分割失败: {file_path}, 错误: {e}")
            raise

    def split_text(
        self,
        content: str,
        file_path: str = "",
        base_metadata: dict[str, Any] | None = None,
    ) -> list[Document]:
        """
        分割普通文本文档

        Args:
            content: 文本内容
            file_path: 文件路径 (用于元数据)

        Returns:
            List[Document]: 文档分片列表
        """
        if not content or not content.strip():
            logger.warning(f"文本文档内容为空: {file_path}")
            return []

        try:
            # 直接使用递归字符分割器
            docs = self.text_splitter.create_documents(texts=[content], metadatas=[{}])
            self._apply_chunk_metadata(docs, file_path, base_metadata)

            logger.info(f"文本分割完成: {file_path} -> {len(docs)} 个分片")
            return docs

        except Exception as e:
            logger.error(f"文本分割失败: {file_path}, 错误: {e}")
            raise

    def split_document(
        self,
        content: str,
        file_path: str = "",
        base_metadata: dict[str, Any] | None = None,
    ) -> list[Document]:
        """
        智能分割文档 (根据文件类型选择分割器)

        Args:
            content: 文档内容
            file_path: 文件路径

        Returns:
            List[Document]: 文档分片列表
        """
        if Path(file_path).suffix.lower() in {".md", ".markdown", ".pdf", ".docx"}:
            return self.split_markdown(content, file_path, base_metadata)
        else:
            return self.split_text(content, file_path, base_metadata)

    def _merge_small_chunks(self, documents: list[Document], min_size: int = 300) -> list[Document]:
        """
        合并太小的分片

        Args:
            documents: 文档列表
            min_size: 最小分片大小 (字符数)

        Returns:
            List[Document]: 合并后的文档列表
        """
        if not documents:
            return []

        merged_docs = []
        current_doc = None

        for doc in documents:
            doc_size = len(doc.page_content)

            if current_doc is None:
                # 第一个文档
                current_doc = doc
            elif doc_size < min_size and len(current_doc.page_content) < self.chunk_size * 2:
                # 当前文档太小且合并后不会太大，则合并
                current_doc.page_content += "\n\n" + doc.page_content
                # 保留主文档的元数据
            else:
                # 保存当前文档，开始新文档
                merged_docs.append(current_doc)
                current_doc = doc

        # 添加最后一个文档
        if current_doc is not None:
            merged_docs.append(current_doc)

        return merged_docs

    def _apply_chunk_metadata(
        self,
        documents: list[Document],
        file_path: str,
        base_metadata: dict[str, Any] | None = None,
    ) -> None:
        """为每个 chunk 补充统一的索引元数据。"""

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
        has_image = "[图片]" in content or bool(re.search(r"!\[[^\]]*\]\([^)]+\)", content))
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


# 全局单例
document_splitter_service = DocumentSplitterService()
