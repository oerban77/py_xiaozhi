"""Weather MCP tool registration (currently a mock, a real API is pending)."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import get_forecast_payload, get_weather_payload

logger = get_logger()


def register_weather_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the weather tools with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "get_weather",
            (
                "Get the current weather for a given city. "
                "Parameter: city - the city name (e.g., Beijing, Shanghai, Guangzhou)"
            ),
            PropertyList(
                [Property("city", PropertyType.STRING, default_value="Beijing")]
            ),
            get_weather_payload,
        ),
        McpTool(
            "get_forecast",
            (
                "Get the weather forecast for a given city. "
                "Parameters: city - the city name, days - number of forecast days (1-7)"
            ),
            PropertyList(
                [
                    Property("city", PropertyType.STRING, default_value="Beijing"),
                    Property(
                        "days",
                        PropertyType.INTEGER,
                        default_value=3,
                        min_value=1,
                        max_value=7,
                    ),
                ]
            ),
            get_forecast_payload,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d weather MCP tools (register_weather_tools, mock)", len(tools))
