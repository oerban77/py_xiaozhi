"""Volume MCP tools: register_volume_tools injects a VolumeController, with no module-level singleton."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .volume_controller import VolumeController

logger = get_logger()


def create_volume_controller() -> VolumeController | None:
    """Create the volume controller; returns None when dependencies are missing or initialization fails."""
    if not VolumeController.check_dependencies():
        return None
    try:
        return VolumeController()
    except Exception as e:
        logger.error(f"Volume controller init failed: {e}", exc_info=True)
        return None


def register_volume_tools(
    add_tool: Callable[[McpTool], None],
    controller: VolumeController | None = None,
) -> None:
    """Register the volume tools with McpServer (the closure holds the controller; no global singleton is written).

    When controller is None, create_volume_controller() is tried; if that still fails, tools that
    return an "unavailable" result are registered so the tool list does not end up missing entries.
    """
    if controller is None:
        controller = create_volume_controller()

    async def set_volume(args: dict[str, Any]) -> bool:
        try:
            volume = args["volume"]
            logger.info(f"[VolumeTools] Setting volume to {volume}")
            if not (0 <= volume <= 100):
                logger.warning(f"[VolumeTools] Volume out of range: {volume}")
                return False
            if controller is None:
                logger.warning("[VolumeTools] Volume control dependencies incomplete; cannot set volume")
                return False
            await asyncio.to_thread(controller.set_volume, volume)
            logger.info(f"[VolumeTools] Volume set: {volume}")
            return True
        except KeyError:
            logger.error("[VolumeTools] Missing volume parameter")
            return False
        except Exception as e:
            logger.error(f"[VolumeTools] Failed to set volume: {e}", exc_info=True)
            return False

    async def get_volume(args: dict[str, Any]) -> int:
        try:
            logger.info("[VolumeTools] Getting current volume")
            if controller is None:
                logger.warning("[VolumeTools] Volume control dependencies incomplete; returning default volume")
                return VolumeController.DEFAULT_VOLUME
            current = await asyncio.to_thread(controller.get_volume)
            logger.info(f"[VolumeTools] Current volume: {current}")
            return current
        except Exception as e:
            logger.error(f"[VolumeTools] Failed to get volume: {e}", exc_info=True)
            return VolumeController.DEFAULT_VOLUME

    async def get_volume_status(args: dict[str, Any]) -> str:
        try:
            if controller is not None:
                current = await asyncio.to_thread(controller.get_volume)
                status = {
                    "volume": current,
                    "muted": current == 0,
                    "available": True,
                }
            else:
                status = {
                    "volume": 50,
                    "muted": False,
                    "available": False,
                    "reason": "Dependencies not available",
                }
        except Exception as e:
            logger.warning(f"[VolumeTools] Failed to get volume state: {e}", exc_info=True)
            status = {
                "volume": 50,
                "muted": False,
                "available": False,
                "error": str(e),
            }
        return json.dumps(status, ensure_ascii=False)

    tools: list[McpTool] = [
        McpTool(
            "self.audio_speaker.set_volume",
            (
                "Set the system speaker volume to an absolute value (0-100).\n"
                "Use when user mentions: volume, sound, louder, quieter, mute, unmute, adjust volume.\n"
                "Examples: 'set volume to 50', 'turn volume up', 'make it louder', 'mute', "
                "'Set the volume to 50', 'Turn up volume', 'Turn down volume slightly', 'Mute'.\n"
                "Parameter:\n"
                "- volume: Integer (0-100) representing the target volume level. Set to 0 for mute."
            ),
            PropertyList(
                [Property("volume", PropertyType.INTEGER, min_value=0, max_value=100)]
            ),
            set_volume,
        ),
        McpTool(
            "self.audio_speaker.get_volume",
            (
                "Get the current system speaker volume level.\n"
                "Use when user asks about: current volume, volume level, how loud, what's the volume.\n"
                "Examples: 'what is the current volume?', 'how loud is it?', 'check volume level', "
                "'What is the current volume?', 'check the volume', 'what is the volume level'.\n"
                "Returns: Integer (0-100) representing the current volume level."
            ),
            PropertyList(),
            get_volume,
        ),
        McpTool(
            "self.audio_speaker.get_volume_status",
            (
                "Get detailed speaker volume status including whether audio output is muted and "
                "whether the volume controller dependencies are available. Returns a JSON payload "
                "with fields: volume (0-100), muted (bool), available (bool), reason/error(optional)."
            ),
            PropertyList(),
            get_volume_status,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info(
        "Registered %d volume MCP tools (VolumeController injected, available=%s)",
        len(tools),
        controller is not None,
    )
