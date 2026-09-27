"""Hardware MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import hardware_command, list_commands, run_command

logger = get_logger()

_MAX_TIMEOUT = 600


def register_hardware_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the hardware/system command tools with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "run_command",
            (
                "Run any shell/system command on this device: ping, dir, ls, git, "
                "python, npm, docker, systemctl, ipconfig, netstat, tracert, "
                "nslookup, whoami, hostname, and every other OS command.\n"
                "Parameters:\n"
                "- command: the FULL command to run, e.g. 'ping -n 4 8.8.8.8', "
                "'git -C D:\\\\project log --oneline -n 20', 'python --version'\n"
                "- args: extra arguments (optional, usually inside command already)\n"
                "- timeout: max seconds to wait (default 60)"
            ),
            PropertyList(
                [
                    Property("command", PropertyType.STRING),
                    Property("args", PropertyType.STRING, default_value=""),
                    Property(
                        "timeout",
                        PropertyType.INTEGER,
                        default_value=60,
                        min_value=1,
                        max_value=_MAX_TIMEOUT,
                    ),
                ]
            ),
            run_command,
        ),
        McpTool(
            "hardware_command",
            "Alias for run_command — run any shell/system command on this device.",
            PropertyList(
                [
                    Property("command", PropertyType.STRING),
                    Property("args", PropertyType.STRING, default_value=""),
                    Property(
                        "timeout",
                        PropertyType.INTEGER,
                        default_value=60,
                        min_value=1,
                        max_value=_MAX_TIMEOUT,
                    ),
                ]
            ),
            hardware_command,
        ),
        McpTool(
            "list_commands",
            (
                "Show the command shortcuts registered in the HARDWARE config. "
                "Any other OS command can be run directly with run_command."
            ),
            PropertyList([]),
            list_commands,
        ),
    ]

    for tool in tools:
        add_tool(tool)
