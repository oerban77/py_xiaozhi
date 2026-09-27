"""Prayer times MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import (
    prayer_list_cities,
    prayer_list_provinces,
    prayer_times_monthly,
    prayer_times_today,
)

logger = get_logger()


def register_prayer_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the prayer-times tools with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "prayer_times_today",
            (
                "Get today's prayer times for a city/regency in Indonesia. "
                "Returns Imsak, Fajr, Sunrise, Dhuha, Dhuhr, Asr, Maghrib and Isha.\n"
                "The city name does not need the 'Kab.' or 'Kota' prefix, "
                "e.g. 'Kudus', 'Bandung', 'Bogor'.\n"
                "If a city name is ambiguous, use prayer_list_cities to see the exact names.\n"
                "Parameters:\n"
                "- province: e.g. 'Jawa Tengah', 'Jawa Barat', 'DKI Jakarta' (required)\n"
                "- city: city/regency without prefix, e.g. 'Kudus', 'Bandung' (required)"
            ),
            PropertyList(
                [
                    Property("province", PropertyType.STRING),
                    Property("city", PropertyType.STRING),
                ]
            ),
            prayer_times_today,
        ),
        McpTool(
            "prayer_times_monthly",
            (
                "Get the full-month prayer times for a city/regency in Indonesia.\n"
                "Parameters:\n"
                "- province: e.g. 'Jawa Tengah' (required)\n"
                "- city: e.g. 'Kudus' (required)\n"
                "- month: 1-12 (default: current month)\n"
                "- year: e.g. 2026 (default: current year)"
            ),
            PropertyList(
                [
                    Property("province", PropertyType.STRING),
                    Property("city", PropertyType.STRING),
                    Property("month", PropertyType.INTEGER, default_value=0,
                             min_value=0, max_value=12),
                    Property("year", PropertyType.INTEGER, default_value=0,
                             min_value=0, max_value=2100),
                ]
            ),
            prayer_times_monthly,
        ),
        McpTool(
            "prayer_list_provinces",
            "List all provinces available in the prayer-times API (Indonesia).",
            PropertyList([]),
            prayer_list_provinces,
        ),
        McpTool(
            "prayer_list_cities",
            (
                "List all regencies/cities of a province. "
                "Use this to confirm the exact city name before calling "
                "prayer_times_today.\n"
                "Parameters:\n"
                "- province: e.g. 'Jawa Tengah' (required)"
            ),
            PropertyList([Property("province", PropertyType.STRING)]),
            prayer_list_cities,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d prayer MCP tools", len(tools))
