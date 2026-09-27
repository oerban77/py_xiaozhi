"""Reminder MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import (
    VALID_MODES,
    add_reminder,
    add_reminder_in,
    delete_reminder,
    edit_reminder,
    list_reminders,
    toggle_reminder,
)

logger = get_logger()

_MODE_HELP = ", ".join(VALID_MODES)
_MAX_DURATION = 86400 * 366
_MAX_ID = 100000

# Optional string fields carry an empty-string default so the MCP framework
# treats them as optional arguments (PropertyList.get_required() only lists
# properties without a default value).
_EMPTY = ""


def register_reminder_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the reminder tools with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "add_reminder_in",
            (
                "Add a one-shot reminder from a relative duration, e.g. "
                "'remind me in 20 seconds', 'in 5 minutes', 'in 2 hours'.\n"
                "Use this when the user gives a relative duration.\n"
                "Parameters:\n"
                "- title: reminder title (required)\n"
                "- message: text spoken when the alarm rings (default: title)\n"
                "- seconds / minutes / hours: duration, may be combined"
            ),
            PropertyList(
                [
                    Property("title", PropertyType.STRING),
                    Property("message", PropertyType.STRING, default_value=_EMPTY),
                    Property("seconds", PropertyType.INTEGER, default_value=0,
                             min_value=0, max_value=_MAX_DURATION),
                    Property("minutes", PropertyType.INTEGER, default_value=0,
                             min_value=0, max_value=_MAX_DURATION),
                    Property("hours", PropertyType.INTEGER, default_value=0,
                             min_value=0, max_value=_MAX_DURATION),
                ]
            ),
            add_reminder_in,
        ),
        McpTool(
            "add_reminder",
            (
                "Add a reminder with a repeat mode:\n"
                "- once: at a specific datetime ('2026-05-15 07:00')\n"
                "- daily: every day at a time ('07:00')\n"
                "- hourly: every N hours (interval_hours, minute)\n"
                "- workday: Mon-Fri at a time ('08:00')\n"
                "- weekly: on a weekday + time (weekday: 'monday', time: '09:00')\n"
                "- monthly: on day N + time (day: 1, time: '08:00')\n"
                "- yearly: on day/month + time (day: 1, month: 1, time: '08:00')\n"
                f"Valid modes: {_MODE_HELP}\n"
                "Parameters:\n"
                "- title (required), message, mode (required)\n"
                "- datetime / time / interval_hours / minute / weekday / day / month"
            ),
            PropertyList(
                [
                    Property("title", PropertyType.STRING),
                    Property("message", PropertyType.STRING, default_value=_EMPTY),
                    Property("mode", PropertyType.STRING, default_value="once"),
                    Property("datetime", PropertyType.STRING, default_value=_EMPTY),
                    Property("time", PropertyType.STRING, default_value=_EMPTY),
                    Property("interval_hours", PropertyType.INTEGER,
                             default_value=1, min_value=1, max_value=720),
                    Property("minute", PropertyType.INTEGER,
                             default_value=0, min_value=0, max_value=59),
                    Property("weekday", PropertyType.STRING, default_value=_EMPTY),
                    Property("day", PropertyType.INTEGER,
                             default_value=1, min_value=1, max_value=31),
                    Property("month", PropertyType.INTEGER,
                             default_value=1, min_value=1, max_value=12),
                ]
            ),
            add_reminder,
        ),
        McpTool(
            "list_reminders",
            "List all stored reminders with their schedule and next trigger time.",
            PropertyList([]),
            list_reminders,
        ),
        McpTool(
            "delete_reminder",
            "Delete a reminder by its id (see list_reminders).",
            PropertyList(
                [Property("id", PropertyType.INTEGER, min_value=1, max_value=_MAX_ID)]
            ),
            delete_reminder,
        ),
        McpTool(
            "edit_reminder",
            (
                "Edit an existing reminder. Only the given fields are changed.\n"
                "Same fields as add_reminder, plus enabled (true/false)."
            ),
            PropertyList(
                [
                    Property("id", PropertyType.INTEGER, min_value=1, max_value=_MAX_ID),
                    Property("title", PropertyType.STRING, default_value=_EMPTY),
                    Property("message", PropertyType.STRING, default_value=_EMPTY),
                    Property("mode", PropertyType.STRING, default_value="once"),
                    Property("datetime", PropertyType.STRING, default_value=_EMPTY),
                    Property("time", PropertyType.STRING, default_value=_EMPTY),
                    Property("interval_hours", PropertyType.INTEGER,
                             default_value=1, min_value=1, max_value=720),
                    Property("minute", PropertyType.INTEGER,
                             default_value=0, min_value=0, max_value=59),
                    Property("weekday", PropertyType.STRING, default_value=_EMPTY),
                    Property("day", PropertyType.INTEGER,
                             default_value=1, min_value=1, max_value=31),
                    Property("month", PropertyType.INTEGER,
                             default_value=1, min_value=1, max_value=12),
                    Property("enabled", PropertyType.BOOLEAN, default_value=True),
                ]
            ),
            edit_reminder,
        ),
        McpTool(
            "toggle_reminder",
            "Enable or disable a reminder by its id.",
            PropertyList(
                [
                    Property("id", PropertyType.INTEGER, min_value=1, max_value=_MAX_ID),
                    Property("enabled", PropertyType.BOOLEAN, default_value=True),
                ]
            ),
            toggle_reminder,
        ),
    ]

    for tool in tools:
        add_tool(tool)
