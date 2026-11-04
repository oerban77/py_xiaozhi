"""Smart home MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import (
    device_control,
    device_details,
    device_status,
    discover_devices,
    lights_all,
    room_control,
)

logger = get_logger()


def register_smarthome_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the smart home (generic MQTT) tools with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "device_status",
            (
                "Show the status of every smart home device (lights, switches, "
                "sensors, ...). Use this first to learn the device numbers "
                "used by device_control. Devices marked RO are read-only "
                "(discovered state topic, no known command topic) and "
                "cannot be controlled until a command_topic is configured."
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
                "- action: ON, OFF or TOGGLE (required)\n"
                "Read-only (RO) devices return an error instead of publishing "
                "to a guessed topic."
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
                "Switch every controllable light on or off at once.\n"
                "Parameters:\n"
                "- action: ON or OFF (required)"
            ),
            PropertyList([Property("action", PropertyType.STRING)]),
            lights_all,
        ),
        McpTool(
            "room_control",
            (
                "Switch every controllable device in one room on or off.\n"
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
                "Rescan the MQTT broker for smart home devices. Discovery "
                "covers Tasmota, Home Assistant discovery, Zigbee2MQTT, "
                "Shelly, and generic MQTT state topics. Use this when a "
                "device was added or the list looks outdated. Discovery "
                "never publishes to an unknown topic."
            ),
            PropertyList([]),
            discover_devices,
        ),
        McpTool(
            "device_details",
            (
                "Show the full protocol, state topic and command topic "
                "for one device, to check why it is controllable or "
                "read-only before controlling it.\n"
                "Parameters:\n"
                "- deviceIndex: device number from device_status (starts at 1) (required)"
            ),
            PropertyList(
                [
                    Property(
                        "deviceIndex",
                        PropertyType.INTEGER,
                        min_value=1,
                        max_value=128,
                    ),
                ]
            ),
            device_details,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d smart home MCP tools", len(tools))
