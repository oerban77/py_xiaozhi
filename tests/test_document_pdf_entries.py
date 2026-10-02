import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.mcp.tools.documents import service
from src.mcp.tools.documents.register import register_documents_tools

_ENTRIES = [
    "01. TOKOH PERTAMA\nTeks sumber pertama, bukan kesimpulan.",
    "02. TOKOH KEDUA\nTeks sumber kedua secara persis.",
    "03. TOKOH KETIGA\nTeks sumber ketiga, bukan ringkasan.",
]


def _write_numbered_pdf(path: Path) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for entry in _ENTRIES:
        page = writer.add_blank_page(width=300, height=300)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        escaped = entry.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 20 200 Td ({escaped}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)


def test_numbered_query_returns_only_matching_pdf_entry(tmp_path):
    path = tmp_path / "numbered.pdf"
    _write_numbered_pdf(path)

    result = service._read_pdf(str(path), query="baca tokoh ke 2")

    assert "02. TOKOH KEDUA" in result
    assert "Teks sumber kedua secara persis." in result
    assert "01. TOKOH PERTAMA" not in result
    assert "03. TOKOH KETIGA" not in result
    assert "Verbatim source text for entry 2" in result


def test_unmatched_pdf_query_does_not_return_unrelated_chunk(tmp_path):
    path = tmp_path / "numbered.pdf"
    _write_numbered_pdf(path)

    result = service._read_pdf(str(path), query="kesimpulan yang tidak ada")

    assert "No exact text match" in result
    assert "TOKOH PERTAMA" not in result
    assert "TOKOH KEDUA" not in result


def test_numbered_followup_reuses_recent_pdf_not_pasted_instruction(
    monkeypatch, tmp_path
):
    pdf_path = tmp_path / "numbered.pdf"
    _write_numbered_pdf(pdf_path)
    request_path = tmp_path / "pasted-request.txt"
    request_path.write_text("Baca tokoh ke 2", encoding="utf-8")
    marker = "Ini pesan/perintah pengguna. Baca isinya dan jalankan permintaan yang tertulis."
    monkeypatch.setattr(
        service, "_PENDING_DOCUMENT_PROVIDER", lambda: (str(request_path), marker)
    )
    monkeypatch.setattr(service, "_LAST_READ_PDF_PATH", None)
    monkeypatch.setattr(service, "_LAST_READ_PDF_AT", 0.0)

    service._document_manage_sync({"action": "read", "path": str(pdf_path)})
    result = service._document_manage_sync({"action": "read"})

    assert "02. TOKOH KEDUA" in result
    assert "Teks sumber kedua secara persis." in result
    assert "01. TOKOH PERTAMA" not in result
    assert "03. TOKOH KETIGA" not in result


@pytest.mark.asyncio
async def test_manage_document_mcp_returns_requested_entry_verbatim(tmp_path):
    pdf_path = tmp_path / "numbered.pdf"
    _write_numbered_pdf(pdf_path)
    tools = []
    register_documents_tools(tools.append)
    document_tool = next(tool for tool in tools if tool.name == "manage_document")

    response = json.loads(
        await document_tool.call(
            {
                "action": "read",
                "path": str(pdf_path),
                "entry_number": 2,
            }
        )
    )
    text = response["content"][0]["text"]

    assert response["isError"] is False
    assert "02. TOKOH KEDUA" in text
    assert "Teks sumber kedua secara persis." in text
    assert "TOKOH PERTAMA" not in text
    assert "TOKOH KETIGA" not in text
