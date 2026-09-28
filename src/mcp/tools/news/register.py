"""News MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import get_news

logger = get_logger()


def register_news_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the news tool with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "get_news",
            (
                "Get the latest news headlines (Indonesian and international sources).\n"
                "This is the preferred tool whenever the user asks for news, current "
                "headlines, or 'berita terkini' / 'kabar terbaru', because it returns "
                "fresh headlines directly from RSS feeds.\n"
                "Leave topic empty for the top headlines right now.\n"
                "Parameters:\n"
                "- topic: optional category or keyword. Categories: 'indonesia', "
                "'nasional', 'teknologi'/'tech', 'olahraga'/'sepakbola', "
                "'ekonomi'/'finance', 'hiburan'/'entertainment', 'dunia'/'internasional'. "
                "Any other text is treated as a free-text keyword search.\n"
                "- max_results: number of headlines (default 5, max 20)"
            ),
            PropertyList(
                [
                    Property("topic", PropertyType.STRING, default_value=""),
                    Property("max_results", PropertyType.INTEGER, default_value=5,
                             min_value=1, max_value=20),
                ]
            ),
            get_news,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d news MCP tools", len(tools))
