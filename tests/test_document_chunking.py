import json
import zipfile

import pytest

from src.mcp.tools.documents.register import register_documents_tools
from src.mcp.tools.documents.service import _write_docx, _write_xlsx


def _write_document(path, extension, content):
    if extension == "txt":
        path.write_text(content, encoding="utf-8")
    elif extension == "docx":
        _write_docx(str(path), content)
    elif extension == "xlsx":
        _write_xlsx(str(path), content)
    elif extension == "odt":
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "content.xml",
                f"<document><text:p>{content}</text:p></document>",
            )
    elif extension == "pptx":
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "ppt/slides/slide1.xml",
                f"<slide><text><a:t>{content}</a:t></text></slide>",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["txt", "docx", "xlsx", "odt", "pptx"])
async def test_manage_document_reads_non_pdf_chunks(tmp_path, extension):
    content = "A" * 24_000 + "B" * 40
    document_path = tmp_path / f"long-document.{extension}"
    _write_document(document_path, extension, content)

    tools = []
    register_documents_tools(tools.append)
    document_tool = next(tool for tool in tools if tool.name == "manage_document")
    common_args = {"action": "read", "path": str(document_path)}

    first = json.loads(await document_tool.call(common_args))
    second = json.loads(
        await document_tool.call({**common_args, "chunk_index": 2})
    )
    first_text = first["content"][0]["text"]
    second_text = second["content"][0]["text"]

    assert first["isError"] is False
    assert second["isError"] is False
    assert "Continue with chunk_index=2" in first_text
    assert "B" * 40 not in first_text
    assert "B" * 40 in second_text
    assert "end of document" in second_text
