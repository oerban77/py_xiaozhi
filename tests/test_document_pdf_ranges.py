import json

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.mcp.tools.documents.register import register_documents_tools
from src.mcp.tools.documents.service import _PDF_MAX_PAGES_PER_READ, _read_pdf


def _write_text_pdf(path, page_count):
    writer = PdfWriter()
    for page_number in range(1, page_count + 1):
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        fonts = DictionaryObject({NameObject("/F1"): writer._add_object(font)})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})

        stream = DecodedStreamObject()
        stream.set_data(
            f"BT /F1 12 Tf 20 200 Td (PAGE {page_number} unique) Tj ET".encode()
        )
        page[NameObject("/Contents")] = writer._add_object(stream)

    writer.write(path)


def test_large_pdf_returns_chunks_with_continuation(tmp_path):
    pdf_path = tmp_path / "long-document.pdf"
    page_count = _PDF_MAX_PAGES_PER_READ + 1
    _write_text_pdf(pdf_path, page_count=page_count)

    first_chunk = _read_pdf(str(pdf_path))
    next_page = _PDF_MAX_PAGES_PER_READ + 1
    next_chunk = _read_pdf(str(pdf_path), page_start=next_page, page_end=page_count)

    assert "PAGE 1 unique" in first_chunk
    assert f"PAGE {_PDF_MAX_PAGES_PER_READ} unique" in first_chunk
    assert f"PAGE {next_page} unique" not in first_chunk
    assert f"Continue with page_start={next_page}, page_end={page_count}." in first_chunk
    assert f"PAGE {next_page} unique" in next_chunk
    assert f"Read pages {next_page}-{page_count} of {page_count}." in next_chunk


def test_pdf_page_range_validation(tmp_path):
    pdf_path = tmp_path / "short-document.pdf"
    _write_text_pdf(pdf_path, page_count=3)

    assert "page_start 4 exceeds" in _read_pdf(str(pdf_path), page_start=4)
    assert "page_end must be greater than or equal" in _read_pdf(
        str(pdf_path), page_start=3, page_end=2
    )


@pytest.mark.asyncio
async def test_manage_document_tool_reads_requested_pdf_range(tmp_path):
    pdf_path = tmp_path / "long-document.pdf"
    _write_text_pdf(pdf_path, page_count=25)
    tools = []
    register_documents_tools(tools.append)
    document_tool = next(tool for tool in tools if tool.name == "manage_document")

    response = json.loads(
        await document_tool.call(
            {
                "action": "read",
                "path": str(pdf_path),
                "page_start": 21,
                "page_end": 25,
            }
        )
    )
    text = response["content"][0]["text"]

    assert response["isError"] is False
    assert "PAGE 21 unique" in text
    assert "PAGE 24 unique" in text
    assert "PAGE 25 unique" not in text
    assert "Continue with page_start=25, page_end=25." in text

    continuation = json.loads(
        await document_tool.call(
            {
                "action": "read",
                "path": str(pdf_path),
                "page_start": 25,
                "page_end": 25,
            }
        )
    )
    continuation_text = continuation["content"][0]["text"]
    assert continuation["isError"] is False
    assert "PAGE 25 unique" in continuation_text
