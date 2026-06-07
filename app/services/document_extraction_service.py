"""文档抽取服务：把多种文件统一转换为可切分、可检索的 Markdown 风格文本。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

SUPPORTED_EXTENSIONS = {".txt", ".text", ".md", ".markdown", ".pdf", ".docx"}
TEXT_EXTENSIONS = {".txt", ".text"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}

_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"(?P<title>[^\"]*)\")?\)"
)


@dataclass(slots=True)
class ExtractedDocument:
    """统一抽取结果。"""

    content: str
    metadata: dict[str, Any]


class DocumentExtractionService:
    """按文件类型抽取文本、表格和图片说明。"""

    def extract_file(self, file_path: str) -> ExtractedDocument:
        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            raise ValueError(f"文件不存在: {file_path}")

        extension = path.suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise ValueError(f"不支持的文件格式: {extension}，仅支持: {supported}")

        raw_bytes = path.read_bytes()
        metadata = self._base_metadata(path, raw_bytes)

        if extension in TEXT_EXTENSIONS:
            content = self._read_text(raw_bytes, path)
            metadata["table_count"] = self._count_markdown_tables(content)
            metadata["image_count"] = 0
            metadata["extractor"] = "plain-text"
        elif extension in MARKDOWN_EXTENSIONS:
            original = self._read_text(raw_bytes, path)
            content, image_count = self._normalize_markdown_images(original)
            metadata["table_count"] = self._count_markdown_tables(content)
            metadata["image_count"] = image_count
            metadata["extractor"] = "markdown"
        elif extension == ".docx":
            content, stats = self._extract_docx(path)
            metadata.update(stats)
            metadata["extractor"] = "python-docx"
        else:
            content, stats = self._extract_pdf(path)
            metadata.update(stats)
            metadata["extractor"] = "pymupdf"

        metadata["has_images"] = metadata.get("image_count", 0) > 0
        metadata["has_tables"] = metadata.get("table_count", 0) > 0

        logger.info(
            f"文档抽取完成: {path.name}, extension={extension}, "
            f"images={metadata['image_count']}, tables={metadata['table_count']}, "
            f"content_length={len(content)}"
        )
        return ExtractedDocument(content=content, metadata=metadata)

    def _base_metadata(self, path: Path, raw_bytes: bytes) -> dict[str, Any]:
        normalized_path = path.as_posix()
        return {
            "source": normalized_path,
            "file_name": path.name,
            "extension": path.suffix.lower(),
            "file_hash": hashlib.sha256(raw_bytes).hexdigest(),
            "file_size": len(raw_bytes),
        }

    def _read_text(self, raw_bytes: bytes, path: Path) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        logger.warning(f"文本编码无法识别，使用替换模式读取: {path}")
        return raw_bytes.decode("utf-8", errors="replace")

    def _normalize_markdown_images(self, content: str) -> tuple[str, int]:
        count = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            alt = match.group("alt").strip()
            target = match.group("target").strip()
            title = (match.group("title") or "").strip()
            parts = ["[图片]"]
            if alt:
                parts.append(alt)
            if target:
                parts.append(f"path={target}")
            if title:
                parts.append(f"title={title}")
            return " ".join(parts)

        return _MARKDOWN_IMAGE_RE.sub(replace, content), count

    def _extract_docx(self, path: Path) -> tuple[str, dict[str, int]]:
        try:
            import docx
            from docx.table import Table
            from docx.text.paragraph import Paragraph
        except ImportError as exc:
            raise RuntimeError("解析 Word 文档需要安装 python-docx") from exc

        document = docx.Document(str(path))
        parts: list[str] = []
        image_count = 0
        table_count = 0

        for child in document.element.body.iterchildren():
            tag = self._local_name(child.tag)
            if tag == "p":
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if text:
                    parts.append(self._format_docx_paragraph(paragraph, text))

                image_labels = self._extract_docx_image_labels(paragraph)
                for label in image_labels:
                    image_count += 1
                    parts.append(f"[图片] Word 图片 {image_count}{label}")
            elif tag == "tbl":
                table = Table(child, document)
                markdown = self._table_to_markdown(self._docx_table_rows(table))
                if markdown:
                    table_count += 1
                    parts.append(f"## 表格 {table_count}\n\n{markdown}")

        return "\n\n".join(part for part in parts if part.strip()), {
            "image_count": image_count,
            "table_count": table_count,
        }

    def _format_docx_paragraph(self, paragraph: Any, text: str) -> str:
        style_name = getattr(getattr(paragraph, "style", None), "name", "") or ""
        if style_name.startswith("Heading"):
            level_match = re.search(r"(\d+)", style_name)
            level = int(level_match.group(1)) if level_match else 1
            level = max(1, min(level, 6))
            return f"{'#' * level} {text}"
        return text

    def _extract_docx_image_labels(self, paragraph: Any) -> list[str]:
        labels = []
        for run in paragraph.runs:
            if not any(
                self._local_name(element.tag) in {"drawing", "pict"}
                for element in run._element.iter()
            ):
                continue

            details = []
            for element in run._element.iter():
                if self._local_name(element.tag) == "docPr":
                    name = element.get("name")
                    alt = element.get("descr") or element.get("title")
                    if name:
                        details.append(f"name={name}")
                    if alt:
                        details.append(f"alt={alt}")
                    break

            labels.append(" " + " ".join(details) if details else "")
        return labels

    def _docx_table_rows(self, table: Any) -> list[list[str]]:
        rows = []
        for row in table.rows:
            rows.append([self._clean_cell_text(cell.text) for cell in row.cells])
        return rows

    def _extract_pdf(self, path: Path) -> tuple[str, dict[str, int]]:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("解析 PDF 文档需要安装 PyMuPDF") from exc

        pdf = fitz.open(str(path))
        try:
            parts: list[str] = []
            image_count = 0
            table_count = 0

            for page_index, page in enumerate(pdf, 1):
                page_parts = [f"## 第 {page_index} 页"]
                text = page.get_text("text").strip()
                if text:
                    page_parts.append(text)

                for rows in self._extract_pdf_tables(page):
                    markdown = self._table_to_markdown(rows)
                    if markdown:
                        table_count += 1
                        page_parts.append(f"### 表格 {table_count}\n\n{markdown}")

                for image_index, _image in enumerate(page.get_images(full=True), 1):
                    image_count += 1
                    page_parts.append(f"[图片] PDF 第 {page_index} 页图片 {image_index}")

                parts.append("\n\n".join(page_parts))

            return "\n\n".join(parts), {
                "image_count": image_count,
                "table_count": table_count,
            }
        finally:
            pdf.close()

    def _extract_pdf_tables(self, page: Any) -> list[list[list[str]]]:
        if not hasattr(page, "find_tables"):
            return []
        try:
            found = page.find_tables()
        except Exception as exc:
            logger.warning(f"PDF 表格识别失败，继续使用普通文本: {exc}")
            return []

        tables = []
        for table in getattr(found, "tables", []):
            try:
                rows = table.extract()
            except Exception as exc:
                logger.warning(f"PDF 表格内容抽取失败: {exc}")
                continue
            normalized = self._normalize_table_rows(rows)
            if normalized:
                tables.append(normalized)
        return tables

    def _normalize_table_rows(self, rows: list[list[Any]]) -> list[list[str]]:
        normalized = []
        for row in rows:
            cells = [self._clean_cell_text(cell) for cell in row]
            if any(cells):
                normalized.append(cells)
        return normalized

    def _table_to_markdown(self, rows: list[list[Any]]) -> str:
        normalized = self._normalize_table_rows(rows)
        if not normalized:
            return ""

        column_count = max(len(row) for row in normalized)
        rectangular = [row + [""] * (column_count - len(row)) for row in normalized]
        header = rectangular[0]
        body = rectangular[1:]

        lines = [
            "| " + " | ".join(self._escape_markdown_cell(cell) for cell in header) + " |",
            "| " + " | ".join("---" for _ in range(column_count)) + " |",
        ]
        lines.extend(
            "| " + " | ".join(self._escape_markdown_cell(cell) for cell in row) + " |"
            for row in body
        )
        return "\n".join(lines)

    def _count_markdown_tables(self, content: str) -> int:
        lines = [line.strip() for line in content.splitlines()]
        count = 0
        index = 0
        while index < len(lines) - 1:
            if lines[index].startswith("|") and self._is_markdown_table_separator(lines[index + 1]):
                count += 1
                index += 2
                while index < len(lines) and lines[index].startswith("|"):
                    index += 1
                continue
            index += 1
        return count

    def _is_markdown_table_separator(self, line: str) -> bool:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell)

    def _clean_cell_text(self, value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    def _escape_markdown_cell(self, value: Any) -> str:
        return self._clean_cell_text(value).replace("|", r"\|")

    def _local_name(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1]


document_extraction_service = DocumentExtractionService()
