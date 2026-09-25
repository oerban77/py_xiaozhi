"""Web search MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import read_article, web_search

logger = get_logger()


def register_websearch_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the web search tools with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "web_search",
            (
                "Search the web for the latest news and articles. "
                "Returns a list of results with title, snippet, source, date and link.\n"
                "Use when the user asks about current events, recent news, or anything that "
                "requires up-to-date information from the internet.\n"
                "Parameters:\n"
                "- query: the search keywords (required), e.g. 'weather in London', 'match result'\n"
                "- count: number of results (default 5, max 10)\n"
                "- language: result language, e.g. 'en-US' (English), 'id-ID' (Indonesian), 'zh-CN' (Chinese)"
            ),
            PropertyList(
                [
                    Property("query", PropertyType.STRING),
                    Property("count", PropertyType.INTEGER, default_value=5,
                             min_value=1, max_value=10),
                    Property("language", PropertyType.STRING, default_value="en-US"),
                ]
            ),
            web_search,
        ),
        McpTool(
            "read_article",
            (
                "Read the body of an article from a URL. Returns the cleaned text "
                "(only the core paragraphs).\n"
                "Use this after web_search when the user wants the full content of a "
                "specific result, or whenever a web page's text is needed.\n"
                "Parameters:\n"
                "- url: the article URL (required)\n"
                "- max_chars: maximum characters returned (default 6000)"
            ),
            PropertyList(
                [
                    Property("url", PropertyType.STRING),
                    Property("max_chars", PropertyType.INTEGER, default_value=6000),
                ]
            ),
            read_article,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d web search MCP tools", len(tools))
