"""Indonesia national holiday MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import indonesia_holiday_query

logger = get_logger()


def register_indonesia_holiday_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the Indonesia holiday tool with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "self.indonesia_holiday_query",
            (
                "Cek hari libur nasional Indonesia (SKB 3 Menteri).\n"
                "Tiga aksi:\n"
                "- action='list'  : daftar libur tahun ini (default)\n"
                "- action='year'  : daftar libur tahun tertentu (param: year, contoh 2026)\n"
                "- action='check' : apakah tanggal YYYY-MM-DD adalah libur? (param: date)\n"
                "\n"
                "Contoh penggunaan:\n"
                "- 'list' tanpa parameter -> libur tahun berjalan\n"
                "- year=2026 -> libur tahun 2026\n"
                "- check date='2026-08-17' -> cek 17 Agustus 2026"
            ),
            PropertyList(
                [
                    Property("action", PropertyType.STRING, default_value="list"),
                    Property("year", PropertyType.INTEGER, default_value=0,
                             min_value=2000, max_value=2100),
                    Property("date", PropertyType.STRING, default_value=""),
                ]
            ),
            indonesia_holiday_query,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d Indonesia holiday MCP tools", len(tools))
