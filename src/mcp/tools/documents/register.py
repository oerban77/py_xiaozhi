"""Document MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import document_manage, search_files

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
    ]

    for tool in tools:
        add_tool(tool)
