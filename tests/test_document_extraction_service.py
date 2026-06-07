import base64
import importlib
import sys
import types
from pathlib import Path

import pytest

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_markdown_extractor_converts_images_to_searchable_text(tmp_path):
    module = importlib.import_module("app.services.document_extraction_service")
    markdown_path = tmp_path / "runbook.md"
    markdown_path.write_text(
        "# CPU Runbook\n\n![CPU flame graph](images/cpu.png)\n\n| Metric | Status |\n| --- | --- |\n| CPU | High |\n",
        encoding="utf-8",
    )

    extracted = module.document_extraction_service.extract_file(str(markdown_path))

    assert "# CPU Runbook" in extracted.content
    assert "[图片] CPU flame graph path=images/cpu.png" in extracted.content
    assert "| Metric | Status |" in extracted.content
    assert extracted.metadata["file_name"] == "runbook.md"
    assert extracted.metadata["extension"] == ".md"
    assert extracted.metadata["image_count"] == 1
    assert extracted.metadata["table_count"] == 1
    assert extracted.metadata["has_images"] is True
    assert extracted.metadata["has_tables"] is True
    assert len(extracted.metadata["file_hash"]) == 64


def test_docx_extractor_converts_tables_and_images_to_markdown(tmp_path):
    module = importlib.import_module("app.services.document_extraction_service")
    docx = pytest.importorskip("docx")

    image_path = tmp_path / "chart.png"
    image_path.write_bytes(TINY_PNG)
    docx_path = tmp_path / "word-runbook.docx"

    document = docx.Document()
    document.add_heading("Word Runbook", level=1)
    document.add_paragraph("Review service saturation before restart.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Status"
    table.cell(1, 0).text = "Memory"
    table.cell(1, 1).text = "High"
    document.add_paragraph().add_run().add_picture(str(image_path))
    document.save(docx_path)

    extracted = module.document_extraction_service.extract_file(str(docx_path))

    assert "# Word Runbook" in extracted.content
    assert "| Metric | Status |" in extracted.content
    assert "| Memory | High |" in extracted.content
    assert "[图片] Word 图片 1" in extracted.content
    assert extracted.metadata["extension"] == ".docx"
    assert extracted.metadata["image_count"] == 1
    assert extracted.metadata["table_count"] == 1


def test_pdf_extractor_converts_tables_and_images_to_markdown(tmp_path):
    module = importlib.import_module("app.services.document_extraction_service")
    fitz = pytest.importorskip("fitz")

    pdf_path = tmp_path / "pdf-runbook.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    x0, y0 = 72, 72
    col_widths = [100, 100]
    row_height = 28
    for index in range(4):
        y = y0 + index * row_height
        page.draw_line((x0, y), (x0 + sum(col_widths), y), color=(0, 0, 0), width=1)
    x = x0
    page.draw_line((x, y0), (x, y0 + 3 * row_height), color=(0, 0, 0), width=1)
    for width in col_widths:
        x += width
        page.draw_line((x, y0), (x, y0 + 3 * row_height), color=(0, 0, 0), width=1)
    for text, x, y in [
        ("Metric", x0 + 8, y0 + 18),
        ("Status", x0 + 108, y0 + 18),
        ("CPU", x0 + 8, y0 + 46),
        ("High", x0 + 108, y0 + 46),
        ("Memory", x0 + 8, y0 + 74),
        ("Normal", x0 + 108, y0 + 74),
    ]:
        page.insert_text((x, y), text, fontsize=10)
    page.insert_text((72, 180), "PDF runbook text", fontsize=12)
    page.insert_image(fitz.Rect(72, 210, 82, 220), stream=TINY_PNG)
    pdf.save(pdf_path)
    pdf.close()

    extracted = module.document_extraction_service.extract_file(str(pdf_path))

    assert "PDF runbook text" in extracted.content
    assert "| Metric | Status |" in extracted.content
    assert "| CPU | High |" in extracted.content
    assert "[图片] PDF 第 1 页图片 1" in extracted.content
    assert extracted.metadata["extension"] == ".pdf"
    assert extracted.metadata["image_count"] == 1
    assert extracted.metadata["table_count"] == 1


def test_splitter_adds_quality_metadata_for_extracted_documents():
    module = importlib.import_module("app.services.document_splitter_service")

    documents = module.DocumentSplitterService().split_document(
        "# CPU\n\n![chart](cpu.png)\n\n| Metric | Status |\n| --- | --- |\n| CPU | High |",
        "C:/uploads/cpu.md",
        base_metadata={
            "file_hash": "a" * 64,
            "image_count": 1,
            "table_count": 1,
            "has_images": True,
            "has_tables": True,
        },
    )

    assert documents
    first = documents[0]
    assert first.metadata["_source"] == "C:/uploads/cpu.md"
    assert first.metadata["file_hash"] == "a" * 64
    assert first.metadata["chunk_index"] == 0
    assert first.metadata["chunk_id"] == "aaaaaaaaaaaa:0"
    assert first.metadata["heading_path"] == "CPU"
    assert first.metadata["content_type"] == "mixed"


def test_splitter_uses_markdown_headers_for_extracted_binary_documents():
    module = importlib.import_module("app.services.document_splitter_service")

    documents = module.DocumentSplitterService().split_document(
        "## 第 1 页\n\nPDF runbook text\n\n| Metric | Status |\n| --- | --- |\n| CPU | High |",
        "C:/uploads/runbook.pdf",
        base_metadata={"file_hash": "c" * 64, "extension": ".pdf"},
    )

    assert documents
    assert documents[0].metadata["heading_path"] == "第 1 页"
    assert documents[0].metadata["content_type"] == "table"


def test_vector_index_service_uses_extractor_before_splitting(monkeypatch, tmp_path):
    fake_store_module = types.ModuleType("app.services.vector_store_manager")
    fake_store = types.SimpleNamespace(
        delete_by_source=lambda source: None,
        add_documents=lambda documents: ["doc-1"],
        reinitialize=lambda: None,
    )
    fake_store_module.vector_store_manager = fake_store
    monkeypatch.setitem(sys.modules, "app.services.vector_store_manager", fake_store_module)

    original_module = sys.modules.pop("app.services.vector_index_service", None)
    try:
        module = importlib.import_module("app.services.vector_index_service")
        service = module.VectorIndexService()
        pdf_path = tmp_path / "runbook.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        splitter_calls = []
        added_documents = []

        class FakeExtractor:
            def extract_file(self, file_path):
                assert Path(file_path) == pdf_path.resolve()
                return types.SimpleNamespace(
                    content="# Extracted PDF\n\n[图片] PDF 第 1 页图片 1",
                    metadata={"file_hash": "b" * 64, "extension": ".pdf", "image_count": 1},
                )

        monkeypatch.setattr(module, "document_extraction_service", FakeExtractor(), raising=False)

        def fake_split_document(content, file_path, base_metadata=None):
            splitter_calls.append((content, file_path, base_metadata))
            return [types.SimpleNamespace(page_content=content, metadata=base_metadata or {})]

        monkeypatch.setattr(module.document_splitter_service, "split_document", fake_split_document)
        monkeypatch.setattr(
            module.vector_store_manager,
            "add_documents",
            lambda documents: added_documents.extend(documents) or ["doc-1"],
        )

        service.index_single_file(str(pdf_path))

        assert splitter_calls == [
            (
                "# Extracted PDF\n\n[图片] PDF 第 1 页图片 1",
                pdf_path.resolve().as_posix(),
                {"file_hash": "b" * 64, "extension": ".pdf", "image_count": 1},
            )
        ]
        assert added_documents
    finally:
        sys.modules.pop("app.services.vector_index_service", None)
        if original_module is not None:
            sys.modules["app.services.vector_index_service"] = original_module
