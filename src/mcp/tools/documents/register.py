"""Document MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import document_manage, image_read, search_files

logger = get_logger()


def register_documents_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the document tools with McpServer."""

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
            "document_manage",
            (
                "Read, create, edit, delete or export a document.\n"
                "Supports text formats (.txt .md .json .csv .log .ini .yaml .xml), "
                ".docx and .xlsx (written with built-in OOXML, no extra deps), "
                "and .pdf (text extraction via the optional pypdf package).\n"
                "Images (.png .jpg .jpeg .webp .bmp .gif .tif .tiff .ico and every "
                "other format Pillow can decode) return metadata plus OCR text; "
                "use the dedicated image_read tool for images.\n"
                "Parameters:\n"
                "- action: read | create | edit | delete | export (required)\n"
                "- path: document path, absolute or relative to cwd (required)\n"
                "- content: text content for create/edit\n"
                "- data: structured data (dict/list) to build json/csv/markdown/xlsx\n"
                "- format: override detection: text|markdown|json|csv|docx|xlsx\n"
                "- output: output path for export\n"
                "- query: keyword to filter lines when reading"
            ),
            PropertyList(
                [
                    Property("action", PropertyType.STRING),
                    Property("path", PropertyType.STRING),
                    Property("content", PropertyType.STRING, default_value=""),
                    Property("format", PropertyType.STRING, default_value=""),
                    Property("output", PropertyType.STRING, default_value=""),
                    Property("query", PropertyType.STRING, default_value=""),
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
