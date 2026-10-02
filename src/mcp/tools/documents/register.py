"""Document MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import (
    document_manage,
    image_read,
    search_files,
    set_pending_document_provider,
)

logger = get_logger()


def register_documents_tools(
    add_tool: Callable[[McpTool], None],
    pending_document_provider: Callable[[], tuple[str, str] | None] | None = None,
) -> None:
    """Register the document tools with McpServer.

    pending_document_provider: when supplied, ``manage_document`` reads a
    chat-attached document when the LLM calls it without a path.
    """
    set_pending_document_provider(pending_document_provider)

    tools: list[McpTool] = [
        McpTool(
            "search_files",
            (
                "Find files on the filesystem by name pattern and/or extension.\n"
                "Searches a directory recursively by default.\n"
                "Parameters:\n"
                "- directory: root directory to search (required)\n"
                "- pattern: filename substring or glob, e.g. 'invoice', '*.pdf'\n"
                "- extensions: comma-separated filter, e.g. 'pdf,docx,png'\n"
                "- recursive: search subfolders (default true)"
            ),
            PropertyList(
                [
                    Property("directory", PropertyType.STRING),
                    Property("pattern", PropertyType.STRING, default_value=""),
                    Property("extensions", PropertyType.STRING, default_value=""),
                    Property("recursive", PropertyType.BOOLEAN, default_value=True),
                ]
            ),
            search_files,
        ),
        McpTool(
            "manage_document",
            (
                "[ATTACHED DOCUMENT READER - use this for attached files] "
                "When a document/file is attached, uploaded, or sent in the chat, "
                "you MUST call this tool with action=read and NO path to read it. "
                "This is the ONLY tool that can read a document attached in the "
                "chat. Never use take_screenshot, read_file, or image_read for an "
                "attached document. If the user asks to read, analyze, summarize, "
                "explain, translate, or answer questions about an attached "
                "document, call manage_document(action=read) FIRST, then answer "
                "from the returned content. For a request to read a numbered entry "
                "(for example 'tokoh ke 2'), pass entry_number; return the extracted "
                "source text faithfully and do not replace it with a summary or "
                "conclusion. If the source text is unavailable, say so instead of "
                "guessing.\n"
                "Read, create, edit, delete or export a document.\n"
                "Supports text formats (.txt .md .json .csv .log .ini .yaml .xml), "
                ".docx and .xlsx (written with built-in OOXML, no extra deps), "
                ".pptx (slide text) and .pdf (text extraction via the optional "
                "pypdf package). PDFs are read in chunks of at most 20 pages "
                "and 24,000 characters; all other document and image text is "
                "returned in chunks of at most 24,000 characters. Use the "
                "continuation parameters to read the next chunk.\n"
                "Parameters:\n"
                "- action: read | create | edit | delete | export (required)\n"
                "- path: document path, absolute or relative to cwd. When the "
                "user has attached a document this is optional: call it with "
                "action=read and no path to read the attached file.\n"
                "- content: text content for create/edit\n"
                "- data: structured data (dict/list) to build json/csv/markdown/xlsx\n"
                "- format: override detection: text|markdown|json|csv|docx|xlsx\n"
                "- output: output path for export\n"
                "- query: keyword to filter lines when reading\n"
                "- page_start: first PDF page, 1-based (default 1)\n"
                "- page_end: last PDF page, inclusive (default: up to 20 pages)\n"
                "- entry_number: numbered PDF entry to return verbatim (default 0)\n"
                "- entry_continue: continue an entry from page_start (default false)\n"
                "- entry_char_offset: character offset within a long entry (default 0)\n"
                "- chunk_index: 1-based 24,000-character chunk for non-PDF "
                "documents (default 1); follow the continuation hint"
            ),
            PropertyList(
                [
                    Property("action", PropertyType.STRING),
                    Property("path", PropertyType.STRING, default_value=""),
                    Property("content", PropertyType.STRING, default_value=""),
                    Property("format", PropertyType.STRING, default_value=""),
                    Property("output", PropertyType.STRING, default_value=""),
                    Property("query", PropertyType.STRING, default_value=""),
                    Property("page_start", PropertyType.INTEGER, default_value=1, min_value=1),
                    Property("page_end", PropertyType.INTEGER, default_value=0, min_value=0),
                    Property("entry_number", PropertyType.INTEGER, default_value=0, min_value=0),
                    Property("entry_continue", PropertyType.BOOLEAN, default_value=False),
                    Property("entry_char_offset", PropertyType.INTEGER, default_value=0, min_value=0),
                    Property("chunk_index", PropertyType.INTEGER, default_value=1, min_value=1),
                ]
            ),
            document_manage,
        ),
        McpTool(
            "image_read",
            (
                "Read an image file from disk and return its text content.\n"
                "Supports every format Pillow can decode: PNG, JPG/JPEG, WEBP, "
                "BMP, GIF, TIFF, ICO, JP2, PPM, EPS, PSD, AVIF, HEIC and more.\n"
                "Returns the image dimensions plus the text found in it (OCR).\n"
                "OCR uses the first available engine: tesseract, "
                "rapidocr-onnxruntime or easyocr. When none is installed the "
                "image is described through the camera vision service instead "
                "(CAMERA.Local_VL_url / CAMERA.explain_url), if configured.\n"
                "Parameters:\n"
                "- path: image path, absolute or relative to cwd (required)\n"
                "- question: what to ask about the image (vision service only)"
            ),
            PropertyList(
                [
                    Property("path", PropertyType.STRING),
                    Property("question", PropertyType.STRING, default_value=""),
                ]
            ),
            image_read,
        ),
    ]

    for tool in tools:
        add_tool(tool)
