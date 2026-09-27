"""Smart home MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import (
    device_control,
    device_status,
    discover_devices,
    lights_all,
    room_control,
)

logger = get_logger()


def register_smarthome_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the smart home (MQTT/Tasmota) tools with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "device_status",
            (
                "Show the status of every smart home device (lights, switches, ...). "
                "Use this first to learn the device numbers used by device_control."
            ),
            PropertyList([]),
            device_status,
        ),
        McpTool(
            "device_control",
            (
                "Control a single smart home device by its number in the device_status list.\n"
                "Parameters:\n"
                "- deviceIndex: device number from device_status (starts at 1) (required)\n"
                "- action: ON, OFF or TOGGLE (required)"
            ),
            PropertyList(
                [
                    Property(
                        "deviceIndex",
                        PropertyType.INTEGER,
                        min_value=1,
                        max_value=128,
                    ),
                    Property("action", PropertyType.STRING, default_value="TOGGLE"),
                ]
            ),
            device_control,
        ),
        McpTool(
            "lights_all",
            (
                "Switch every light on or off at once.\n"
                "Parameters:\n"
                "- action: ON or OFF (required)"
            ),
            PropertyList([Property("action", PropertyType.STRING)]),
            lights_all,
        ),
        McpTool(
            "room_control",
            (
                "Switch every device in one room on or off.\n"
                "Parameters:\n"
                "- room: the room name, e.g. 'living room', 'bedroom' (required)\n"
                "- action: ON or OFF (required)"
            ),
            PropertyList(
                [
                    Property("room", PropertyType.STRING),
                    Property("action", PropertyType.STRING, default_value="ON"),
                ]
            ),
            room_control,
        ),
        McpTool(
            "discover_devices",
            (
                "Rescan the MQTT broker for smart home devices (Tasmota discovery). "
                "Use this when a device was added or the list looks outdated."
            ),
            PropertyList([]),
            discover_devices,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d smart home MCP tools", len(tools))
