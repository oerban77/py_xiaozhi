"""Document management MCP tools.

Ported from the reference Xiaozhi desktop app (mcp/mcp_documents.py):
- ``search_files``    — find files by name/extension under a directory
- ``document_manage`` — read / create / edit / delete / export documents

Supported text formats: ``.txt .md .json .csv .log .ini .yaml .yml .xml .html``
Supported Office formats (created/read with pure-Python OOXML, no extra deps):
``.docx`` and ``.xlsx``. PDF reading uses ``pypdf``/``PyPDF2`` when available
(optional dependency); without it only file metadata is returned.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import shutil
import zipfile
from typing import Any
from xml.sax.saxutils import escape

from src.logging import get_logger

logger = get_logger()

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".log",
    ".ini",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
}
BINARY_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
}

_TEXT_FORMATS = {
    "text",
    "csv",
    "json",
    "md",
    "log",
    "ini",
    "yaml",
    "yml",
    "xml",
    "html",
    "htm",
}

_MAX_SEARCH_RESULTS = 200
_MAX_READ_BYTES = 256 * 1024


def _resolve_path(path_value: str) -> str:
    """Resolve an absolute or cwd-relative path."""
    raw = str(path_value or "").strip()
    if not raw:
        raise ValueError("A document path is required")
    if os.path.isabs(raw):
        return raw
    candidates = [raw, os.path.abspath(raw)]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return os.path.abspath(raw)


def _guess_format(path: str, requested: str | None = None) -> str:
    if requested:
        return str(requested).lower()
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in BINARY_EXTENSIONS:
        return ext.lstrip(".").lower()
    return "binary"


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _write_text(path: str, content: str) -> None:
    _ensure_parent(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content or "")


def _serialize_structured_data(data: object) -> str:
    if isinstance(data, (dict, list)):
        return json.dumps(data, ensure_ascii=False, indent=2)
    return str(data)


def _serialize_to_markdown(data: object) -> str:
    if isinstance(data, dict):
        return "\n".join(f"- **{k}**: {v}" for k, v in data.items())
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, dict):
                lines.append("- " + ", ".join(f"{k}={v}" for k, v in item.items()))
            else:
                lines.append(f"- {item}")
        return "\n".join(lines)
    return str(data)


def _serialize_to_csv(data: object) -> str:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        fieldnames = list(data[0].keys())
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for row in data:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
        return buffer.getvalue()
    if isinstance(data, dict):
        return ",".join(f"{k}={v}" for k, v in data.items())
    return str(data)


# ── DOCX (minimal OOXML, no external dependency) ──────────────


def _write_docx(path: str, content: str) -> None:
    _ensure_parent(path)
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t xml:space="preserve">{escaped}</w:t></w:r></w:p>'
        '<w:sectPr/></w:body></w:document>'
    ).format(escaped=escape(str(content or "")))
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc_xml)


def _read_docx(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            data = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", data, flags=re.S)
        text = "".join(re.sub(r"<[^>]+>", "", part) for part in parts)
        if text.strip():
            return text.strip()
    except Exception as exc:
        logger.warning("DOCX read failed: %s", exc)
    return f"Word document. Size: {os.path.getsize(path)} bytes"


# ── XLSX (minimal OOXML, no external dependency) ──────────────


def _rows_from_content(content: object) -> list[list[str]]:
    rows: list[list[str]] = []
    if isinstance(content, (list, tuple)):
        for row in content:
            rows.append([str(cell) for cell in row])
    else:
        for line in str(content or "").splitlines():
            if not line.strip():
                continue
            rows.append([c.strip() for c in re.split(r";|,|\t", line)])
    return rows or [[""]]


def _write_xlsx(path: str, content: object) -> None:
    _ensure_parent(path)
    rows = _rows_from_content(content)

    sheet_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for row_index, row in enumerate(rows, start=1):
        row_xml = [f'<row r="{row_index}">']
        for col_index, cell_value in enumerate(row, start=1):
            cell_ref = f"{chr(64 + col_index)}{row_index}"
            row_xml.append(
                f'<c r="{cell_ref}" t="inlineStr"><is><t>'
                f"{escape(str(cell_value))}</t></is></c>"
            )
        row_xml.append("</row>")
        sheet_xml.extend(row_xml)
    sheet_xml.extend(["</sheetData>", "</worksheet>"])

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )
    shared_strings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1">'
        "<si><t>placeholder</t></si></sst>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", "".join(sheet_xml))
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("xl/sharedStrings.xml", shared_strings_xml)


def _read_xlsx(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml_data = zf.read("xl/worksheets/sheet1.xml").decode(
                "utf-8", errors="ignore"
            )
        rows = []
        for match in re.finditer(r"<row[^>]*>(.*?)</row>", xml_data, flags=re.S):
            cells = re.findall(r"<t[^>]*>(.*?)</t>", match.group(1), flags=re.S)
            if cells:
                rows.append(" | ".join(re.sub(r"<[^>]+>", "", c) for c in cells))
        if rows:
            return "\n".join(rows)
    except Exception as exc:
        logger.warning("XLSX read failed: %s", exc)
    return f"Spreadsheet document. Size: {os.path.getsize(path)} bytes"


def _export_xlsx_to_csv(path: str, output_path: str) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml_data = zf.read("xl/worksheets/sheet1.xml").decode(
                "utf-8", errors="ignore"
            )
        rows = []
        for match in re.finditer(r"<row[^>]*>(.*?)</row>", xml_data, flags=re.S):
            cells = re.findall(r"<t[^>]*>(.*?)</t>", match.group(1), flags=re.S)
            rows.append([re.sub(r"<[^>]+>", "", c) for c in cells])
        _ensure_parent(output_path)
        with open(output_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerows(rows)
        return f"Exported to CSV: {output_path}"
    except Exception as exc:
        logger.error("Export XLSX->CSV failed: %s", exc)
        return f"Error: {exc}"


def _export_text_to_xlsx(path: str, output_path: str) -> str:
    try:
        content = _read_text(path) if os.path.exists(path) else ""
        _write_xlsx(output_path, content)
        return f"Exported to XLSX: {output_path}"
    except Exception as exc:
        logger.error("Export text->XLSX failed: %s", exc)
        return f"Error: {exc}"


# ── PDF (optional dependency) ────────────────────────────────


def _extract_pdf_text(path: str) -> str:
    import importlib.util

    for module_name in ("pypdf", "PyPDF2"):
        try:
            if importlib.util.find_spec(module_name) is None:
                continue
            reader = __import__(module_name, fromlist=["PdfReader"]).PdfReader(path)
            text_parts = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    text_parts.append(text.strip())
            if text_parts:
                return "\n\n".join(text_parts)
        except Exception as exc:
            logger.warning("PDF read (%s) failed: %s", module_name, exc)

    size = os.path.getsize(path)
    if size > 0:
        return (
            "No text could be extracted from this PDF "
            f"(file size: {size} bytes). "
            "Install pypdf for better text extraction."
        )
    return "The PDF file is empty."


def _read_pdf(path: str, query: str | None = None) -> str:
    text = _extract_pdf_text(path)
    if not query:
        return text
    query_text = str(query).strip().lower()
    if not query_text:
        return text
    matches = [
        line.strip()
        for line in text.splitlines()
        if query_text in line.lower()
    ]
    if matches:
        return "\n".join(matches[:10])
    return f"No results for query: {query}"


def _write_placeholder_binary(path: str) -> None:
    _ensure_parent(path)
    with open(path, "wb") as fh:
        fh.write(b"placeholder")


# ── search_files ─────────────────────────────────────────────


def _search_files(
    directory: str, pattern: str = "", extensions: str = "", recursive: bool = True
) -> str:
    if not os.path.exists(directory):
        return f"Directory not found: {directory}"

    ext_filter = (
        {e.strip().lower().lstrip(".") for e in extensions.split(",") if e.strip()}
        if extensions
        else set()
    )
    pattern_lower = pattern.lower().strip() if pattern else ""

    found: list[str] = []
    walk_fn = os.walk if recursive else lambda d: [(d, [], os.listdir(d))]
    for root, _dirs, files in walk_fn(directory):
        for fname in files:
            fname_lower = fname.lower()
            if pattern_lower and pattern_lower not in fname_lower:
                continue
            if ext_filter:
                ext = os.path.splitext(fname_lower)[1].lstrip(".")
                if ext not in ext_filter:
                    continue
            found.append(os.path.join(root, fname))
            if len(found) >= _MAX_SEARCH_RESULTS:
                break
        if len(found) >= _MAX_SEARCH_RESULTS:
            break

    if not found:
        desc = f" with pattern '{pattern}'" if pattern else ""
        desc += f" extensions [{extensions}]" if extensions else ""
        return f"No files found{desc} in {directory}"

    lines = [f"Found {len(found)} file(s):"] + found[:_MAX_SEARCH_RESULTS]
    if len(found) >= _MAX_SEARCH_RESULTS:
        lines.append(f"... and more (limit {_MAX_SEARCH_RESULTS})")
    return "\n".join(lines)


# ── document_manage ──────────────────────────────────────────


def _document_manage_sync(args: dict[str, Any]) -> str:
    action = str(args.get("action") or "").strip().lower()
    if action not in {"read", "create", "edit", "delete", "export"}:
        return "action must be one of: read, create, edit, delete, export"

    path_value = (
        args.get("path") or args.get("file") or args.get("document_path") or ""
    )
    if not path_value:
        return "A document path is required"

    path = _resolve_path(str(path_value))
    fmt = _guess_format(path, args.get("format"))

    if action == "create":
        content = args.get("content") or ""
        data = args.get("data")
        if data is not None and not content:
            content = _serialize_structured_data(data)
        if fmt == "text":
            _write_text(path, str(content))
        elif fmt == "json":
            _write_text(path, _serialize_structured_data(data if data is not None else content))
        elif fmt == "markdown":
            _write_text(path, _serialize_to_markdown(data if data is not None else content))
        elif fmt == "csv":
            _write_text(path, _serialize_to_csv(data if data is not None else content))
        elif fmt == "docx":
            _write_docx(path, str(content))
        elif fmt == "xlsx":
            if isinstance(data, list) and data and isinstance(data[0], dict):
                rows = [list(data[0].keys())]
                for row in data:
                    rows.append([str(row.get(k, "")) for k in rows[0]])
                _write_xlsx(path, rows)
            else:
                _write_xlsx(path, str(content))
        else:
            _write_placeholder_binary(path)
        return f"Document created: {path}"

    if action == "edit":
        if not os.path.exists(path):
            return f"File not found: {path_value}"
        content = args.get("content") or ""
        if fmt in {"text", "csv"}:
            _write_text(path, str(content))
        elif fmt == "docx":
            _write_docx(path, str(content))
        elif fmt == "xlsx":
            _write_xlsx(path, str(content))
        else:
            _write_placeholder_binary(path)
        return f"Document updated: {path}"

    if action == "delete":
        if not os.path.exists(path):
            return f"File not found: {path_value}"
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return f"Document deleted: {path}"

    if action == "export":
        if not os.path.exists(path):
            return f"File not found: {path_value}"
        output = args.get("output") or args.get("target")
        if not output:
            return "An output path is required for export"
        output_path = _resolve_path(str(output))
        if output_path.lower().endswith(".xlsx"):
            if path.lower().endswith(".xlsx"):
                return _export_xlsx_to_csv(path, output_path)
            return _export_text_to_xlsx(path, output_path)
        if output_path.lower().endswith(".csv"):
            if path.lower().endswith(".xlsx"):
                return _export_xlsx_to_csv(path, output_path)
            _write_text(output_path, _read_text(path))
            return f"Exported to CSV: {output_path}"
        return f"Export not supported for format: {fmt}"

    # action == "read"
    if not os.path.exists(path):
        return f"File not found: {path_value}"
    if os.path.isdir(path):
        return f"Path is a folder: {path}"

    if fmt in _TEXT_FORMATS:
        query = args.get("query")
        text = _read_text(path)
        if len(text) > _MAX_READ_BYTES:
            text = text[:_MAX_READ_BYTES] + f"\n... (truncated at {_MAX_READ_BYTES} bytes)"
        if query:
            query_text = str(query).strip().lower()
            matches = [
                line.strip() for line in text.splitlines() if query_text in line.lower()
            ]
            if matches:
                return "\n".join(matches[:10])
            return f"No results for query: {query}"
        return text

    if fmt == "docx":
        return _read_docx(path)
    if fmt == "xlsx":
        return _read_xlsx(path)
    if fmt == "pdf":
        return _read_pdf(path, args.get("query"))
    return f"Document available. Detected format: {fmt}"


async def search_files(args: dict[str, Any]) -> str:
    """Find files under a directory by name pattern and/or extension."""
    return await asyncio.to_thread(
        _search_files,
        str(args.get("directory", "")),
        str(args.get("pattern", "")),
        str(args.get("extensions", "")),
        bool(args.get("recursive", True)),
    )


async def document_manage(args: dict[str, Any]) -> str:
    """Read / create / edit / delete / export a document."""
    return await asyncio.to_thread(_document_manage_sync, args)
