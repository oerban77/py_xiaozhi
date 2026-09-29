"""Document management MCP tools.

Ported from the reference Xiaozhi desktop app (mcp/mcp_documents.py):
- ``search_files``    — find files by name/extension under a directory
- ``document_manage`` — read / create / edit / delete / export documents

Supported text formats: ``.txt .md .json .csv .log .ini .yaml .yml .xml .html``
Supported Office formats (created/read with pure-Python OOXML, no extra deps):
``.docx`` and ``.xlsx``. PDF reading uses ``pypdf``/``PyPDF2`` when available
(optional dependency); without it only file metadata is returned.
Image formats (``.png .jpg .jpeg .webp .bmp .gif .tif .tiff .ico`` and every
other format Pillow can decode) are read with ``image_read`` / ``document_manage``:
metadata plus OCR text. OCR uses the first available engine
(``pytesseract``, ``rapidocr-onnxruntime``, ``easyocr``); when none is installed
the image is described through the camera vision service instead, if configured.
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
from typing import Any, Callable
from xml.sax.saxutils import escape

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()

# Injected by McpServer.add_common_tools: yields (path, question) for a document
# attached in the desktop chat, so document_manage can read it without a path.
_PENDING_DOCUMENT_PROVIDER: Callable[[], tuple[str, str] | None] | None = None


def set_pending_document_provider(
    provider: Callable[[], tuple[str, str] | None] | None,
) -> None:
    """Inject the chat-attachment provider (set by McpServer.add_common_tools)."""
    global _PENDING_DOCUMENT_PROVIDER
    _PENDING_DOCUMENT_PROVIDER = provider


def _consume_pending_document() -> tuple[str, str] | None:
    """Return (and clear) the chat-attached document, if any is queued."""
    if _PENDING_DOCUMENT_PROVIDER is None:
        return None
    try:
        return _PENDING_DOCUMENT_PROVIDER()
    except Exception as exc:  # noqa: BLE001 — never break the tool call
        logger.warning("pending document provider failed: %s", exc)
        return None

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

# Every image format Pillow can plausibly decode.
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".webp",
    ".bmp",
    ".dib",
    ".gif",
    ".tif",
    ".tiff",
    ".ico",
    ".jp2",
    ".j2k",
    ".jpx",
    ".jpm",
    ".ppm",
    ".pgm",
    ".pbm",
    ".pnm",
    ".eps",
    ".psd",
    ".tga",
    ".pcx",
    ".dds",
    ".hdr",
    ".exr",
    ".avif",
    ".heic",
    ".heif",
    ".svg",
    ".ras",
    ".sgi",
    ".xbm",
    ".xpm",
    ".cur",
    ".fits",
    ".fpx",
    ".mpo",
    ".pict",
    ".pxr",
    ".xwd",
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
_DEFAULT_EXPLAIN_URL = "https://api.xiaozhi.me/vision/explain"


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
    if ext in IMAGE_EXTENSIONS:
        return "image"
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


def _read_odt(path: str) -> str:
    """Read an ODF text document (OpenDocument .odt/.ods/.odp content.xml)."""
    try:
        with zipfile.ZipFile(path) as zf:
            data = zf.read("content.xml").decode("utf-8", errors="ignore")
        parts = re.findall(r"<text:p[^>]*>(.*?)</text:p>", data, flags=re.S)
        lines = []
        for part in parts:
            text = re.sub(r"<[^>]+>", "", part)
            if text.strip():
                lines.append(text.strip())
        if lines:
            return "\n".join(lines)
    except Exception as exc:
        logger.warning("ODF read failed: %s", exc)
    return f"OpenDocument file. Size: {os.path.getsize(path)} bytes"


def _read_pptx(path: str) -> str:
    """Read a PowerPoint .pptx: slide text from every slide in order."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [
                n
                for n in zf.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
            ]
            names.sort(key=lambda n: int(re.search(r"\d+", n).group()))
            slides = []
            for idx, name in enumerate(names, 1):
                data = zf.read(name).decode("utf-8", errors="ignore")
                parts = re.findall(r"<a:t[^>]*>(.*?)</a:t>", data, flags=re.S)
                lines = [
                    re.sub(r"<[^>]+>", "", part).strip()
                    for part in parts
                ]
                lines = [ln for ln in lines if ln]
                if lines:
                    slides.append(f"[Slide {idx}]\n" + "\n".join(lines))
            if slides:
                return "\n\n".join(slides)
    except Exception as exc:
        logger.warning("PPTX read failed: %s", exc)
    return f"PowerPoint document. Size: {os.path.getsize(path)} bytes"


def _read_legacy_office(path: str) -> str:
    """Best-effort text extraction from a legacy OLE2 Office file (.doc/.xls/.ppt).

    These are compound binary formats; without python-docx/pandas we can only
    pull out the embedded ASCII/UTF-16 runs, which covers most plain text.
    """
    try:
        import olefile

        if not olefile.isOleFile(path):
            raise RuntimeError("not an OLE2 compound file")
        with olefile.OleFileIO(path) as ole:
            streams = []
            for stream in ole.listdir():
                name = "/".join(stream)
                if not name.lower().endswith((".doc", ".ppt", ".xls", "worddocument")):
                    continue
                streams.append((name, ole.openstream(stream).read()))
            if not streams:
                streams.append(("document", b""))
        texts = []
        for _name, data in streams:
            if not data:
                continue
            # Printable ASCII runs
            ascii_text = re.findall(rb"[\x20-\x7e]{4,}", data)
            # UTF-16LE runs (common in OLE2)
            utf16_text = re.findall(
                rb"(?:[\x20-\x7e]\x00){4,}", data
            )
            chunks = [m.decode("ascii", "ignore") for m in ascii_text]
            chunks += [m.decode("utf-16-le", "ignore") for m in utf16_text]
            joined = " ".join(chunks).strip()
            if joined:
                texts.append(joined)
        if texts:
            return "\n".join(texts)
    except ImportError:
        logger.warning(
            "Legacy Office read needs the optional 'olefile' package: %s", path
        )
    except Exception as exc:
        logger.warning("Legacy Office read failed: %s", exc)
    return f"Office document. Size: {os.path.getsize(path)} bytes"


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
    # No line matched: the LLM still needs the document content to answer the
    # user's question, so fall back to the full extracted text.
    return text


def _write_placeholder_binary(path: str) -> None:
    _ensure_parent(path)
    with open(path, "wb") as fh:
        fh.write(b"placeholder")


# ── Images (metadata + OCR / vision description) ─────────────


def _image_metadata(path: str) -> dict[str, Any]:
    """Size, dimensions, format and colour mode via Pillow (no OCR)."""
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - Pillow is a hard dep elsewhere
        logger.warning("Pillow unavailable: %s", exc)
        return {"path": path, "size_bytes": os.path.getsize(path)}

    try:
        with Image.open(path) as img:
            return {
                "path": path,
                "size_bytes": os.path.getsize(path),
                "format": img.format or os.path.splitext(path)[1].lstrip("."),
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
            }
    except Exception as exc:
        logger.warning("Image metadata failed for %s: %s", path, exc)
        return {
            "path": path,
            "size_bytes": os.path.getsize(path),
            "error": f"Could not decode image: {exc}",
        }


def _ocr_tesseract(image_path: str) -> str | None:
    """OCR via the external tesseract binary (pytesseract wrapper)."""
    try:
        import pytesseract
    except ImportError:
        return None
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            return pytesseract.image_to_string(img).strip() or None
    except Exception as exc:
        logger.warning("tesseract OCR failed: %s", exc)
        return None


def _ocr_rapidocr(image_path: str) -> str | None:
    """OCR via rapidocr-onnxruntime (self-contained, no external binary)."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    try:
        engine = RapidOCR()
        result, _elapsed = engine(image_path)
        if not result:
            return None
        lines = [item[1] for item in result if item and len(item) > 1]
        return "\n".join(lines).strip() or None
    except Exception as exc:
        logger.warning("rapidocr OCR failed: %s", exc)
        return None


def _ocr_easyocr(image_path: str) -> str | None:
    """OCR via easyocr (downloads model weights on first use)."""
    try:
        import easyocr
    except ImportError:
        return None
    try:
        reader = easyocr.Reader(["en"], gpu=False)
        result = reader.readtext(image_path, detail=0)
        return "\n".join(result).strip() or None
    except Exception as exc:
        logger.warning("easyocr OCR failed: %s", exc)
        return None


def _has_ocr_engine() -> bool:
    """True when at least one OCR engine can be imported."""
    for module in ("pytesseract", "rapidocr_onnxruntime", "easyocr"):
        try:
            __import__(module)
            return True
        except ImportError:
            continue
    return False


def _ocr_image(image_path: str) -> str | None:
    """Run the first available OCR engine; None when none is installed."""
    for engine in (_ocr_tesseract, _ocr_rapidocr, _ocr_easyocr):
        text = engine(image_path)
        if text:
            return text
    return None


def _vision_endpoint_configured() -> bool:
    try:
        cfg = get_config()
    except Exception:
        return False

    explain_url = (cfg.get_config("CAMERA.explain_url", "") or "").strip()
    return bool(explain_url or _DEFAULT_EXPLAIN_URL)


def _vision_describe(image_path: str, question: str) -> str | None:
    """Describe an image through the camera vision service, when configured.

    Reuses the same CAMERA.explain_url / Local_VL_url settings as take_photo so
    no separate account or configuration is needed.
    """
    try:
        cfg = get_config()
    except Exception:
        return None

    question = (question or "").strip()
    explain_url = (
        cfg.get_config("CAMERA.explain_url", "") or _DEFAULT_EXPLAIN_URL
    ).strip()
    if not explain_url:
        return None
    try:
        import requests

        with open(image_path, "rb") as fh:
            image_bytes = fh.read()

        headers = {
            "Device-Id": cfg.get_config("SYSTEM_OPTIONS.DEVICE_ID") or "",
            "Client-Id": cfg.get_config("SYSTEM_OPTIONS.CLIENT_ID") or "",
        }
        token = (cfg.get_config("CAMERA.explain_token", "") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        mime = _guess_mime(image_path)
        files = {
            "question": (None, question or "Describe this image briefly and clearly."),
            "file": (os.path.basename(image_path), image_bytes, mime),
        }
        response = requests.post(
            explain_url, headers=headers, files=files, timeout=20
        )
        if response.status_code != 200:
            logger.warning("Vision service returned HTTP %s", response.status_code)
            return None
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or None
        if isinstance(payload, dict):
            if not payload.get("success", True):
                return None
            text = (
                payload.get("text")
                or payload.get("result")
                or payload.get("response")
                or payload.get("content")
                or ""
            )
            return str(text).strip() or None
        return str(payload).strip() or None
    except Exception as exc:
        logger.warning("Explain vision describe failed: %s", exc)
        return None


def _guess_mime(path: str) -> str:
    """Best-effort MIME type for an image path."""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".jpe": "image/jpeg",
        ".jfif": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".ico": "image/x-icon",
        ".svg": "image/svg+xml",
    }.get(ext, "image/jpeg")


def _read_image(path: str, question: str | None = None) -> str:
    """Read an image file: metadata block + OCR text or a vision description."""
    question = (question or "").strip()
    vision_configured = _vision_endpoint_configured()
    vision_attempted = False

    if question and vision_configured:
        vision_attempted = True
        description = _vision_describe(path, question)
        if description:
            return f"Image: {path}\nDescription:\n{description}"

    meta = _image_metadata(path)
    width = meta.get("width")
    height = meta.get("height")
    dims = (
        f"{width}x{height} {meta.get('format', '')} ({meta.get('mode', '')})"
        if width
        else f"{meta.get('size_bytes', 0)} bytes"
    )
    header = f"Image: {path}\nDimensions: {dims}\n"

    ocr_text = _ocr_image(path)
    if ocr_text:
        result = f"{header}Text (OCR):\n{ocr_text}"
        if question and not vision_configured:
            result += (
                "\n\nVisual description unavailable: configure a vision URL "
                "and API key for hosted services, or a private/local vision URL."
            )
        elif question and vision_attempted:
            result += "\n\nVision service did not respond; only OCR text is available."
        return result

    if not vision_attempted and vision_configured:
        description = _vision_describe(
            path, question or "Describe this image briefly and clearly."
        )
        if description:
            return f"{header}Description:\n{description}"

    if _has_ocr_engine():
        return (
            f"{header}No text was detected in this image. The OCR engine "
            "found no characters; it may be a photo or drawing rather than a "
            "document. Set CAMERA.Local_VL_url + CAMERA.VLapi_key or "
            "CAMERA.explain_url to have images described instead."
        )

    return (
        f"{header}No OCR engine is installed and no vision service is "
        "configured, so the image content could not be read. Install "
        "rapidocr-onnxruntime (pip install rapidocr-onnxruntime) or set "
        "CAMERA.Local_VL_url + CAMERA.VLapi_key / CAMERA.explain_url."
    )


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
    pending_question = ""
    if not path_value:
        # No path given: fall back to a document attached in the desktop chat.
        pending = _consume_pending_document()
        if pending is None:
            return "A document path is required"
        path_value = pending[0]
        pending_question = str(pending[1] or "").strip()
        args = dict(args)
        args["path"] = path_value
        if not args.get("query") and pending_question:
            args["query"] = pending_question

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

    # Surface the user's chat question and the file name to the LLM alongside
    # the content, so it knows what to answer even though the sent prompt only
    # carried the short question (the detect channel rejects long texts).
    question_hint = ""
    if pending_question:
        file_name = os.path.basename(str(path_value))
        question_hint = f"[Attached file: {file_name} | User question: {pending_question}]\n\n"

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
                return question_hint + "\n".join(matches[:10])
            # No line matched: the LLM still needs the document content to answer
            # the user's question, so fall back to the full text.
            return question_hint + text
        return question_hint + text

    if fmt == "docx":
        return question_hint + _read_docx(path)
    if fmt == "xlsx":
        return question_hint + _read_xlsx(path)
    if fmt == "pdf":
        return question_hint + _read_pdf(path, args.get("query"))
    if fmt in {"odt", "ods", "odp"}:
        return question_hint + _read_odt(path)
    if fmt in {"doc", "xls", "ppt"}:
        return question_hint + _read_legacy_office(path)
    if fmt == "pptx":
        return question_hint + _read_pptx(path)
    if fmt == "image":
        return question_hint + _read_image(path, args.get("query"))
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


def _image_read_sync(args: dict[str, Any]) -> str:
    """Read any image file: metadata plus OCR text (or a vision description)."""
    path_value = (
        args.get("path")
        or args.get("file")
        or args.get("image_path")
        or args.get("document_path")
        or ""
    )
    if not path_value:
        return "An image path is required"

    path = _resolve_path(str(path_value))
    if not os.path.exists(path):
        return f"File not found: {path_value}"
    if os.path.isdir(path):
        return f"Path is a folder: {path}"

    return _read_image(path, args.get("question") or args.get("query"))


async def image_read(args: dict[str, Any]) -> str:
    """Read an image file from disk (all formats Pillow can decode)."""
    return await asyncio.to_thread(_image_read_sync, args)
