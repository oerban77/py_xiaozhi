"""Screenshot MCP tool registration and factory."""

import asyncio
import json
from typing import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .screenshot_camera import ScreenshotCamera

logger = get_logger()


def create_screenshot_camera() -> ScreenshotCamera:
    """Create desktop screenshot implementation."""
    return ScreenshotCamera()


def register_screenshot_tools(
    add_tool: Callable[[McpTool], None],
    photo_camera=None,
) -> None:
    """Register take_screenshot.

    photo_camera: optional, used to reuse the explain URL/token during analysis (the same camera as take_photo).
    """
    camera = create_screenshot_camera()
    if photo_camera is not None and hasattr(photo_camera, "get_explain_url"):
        # Share the vision configuration with the photo camera (if already configured)
        try:
            url = getattr(photo_camera, "explain_url", None) or getattr(
                photo_camera, "_explain_url", None
            )
            token = getattr(photo_camera, "explain_token", None) or getattr(
                photo_camera, "_explain_token", None
            )
            if url and hasattr(camera, "set_explain_url"):
                camera.set_explain_url(url)
            if token and hasattr(camera, "set_explain_token"):
                camera.set_explain_token(token)
        except Exception as e:
            logger.debug(f"Failed to sync vision config to screenshot camera: {e}", exc_info=True)

    # Keep a reference so analyze can fall back to the photo camera's explain settings
    camera._photo_camera_ref = photo_camera  # type: ignore[attr-defined]

    async def take_screenshot(arguments: dict) -> str:
        logger.info(
            f"Using screenshot camera implementation: {camera.__class__.__name__}"
        )

        question = arguments.get("question", "")
        display_id = arguments.get("display", None)

        if display_id:
            if isinstance(display_id, str):
                if display_id.lower() in ["main", "Main display", "Main monitor", "Laptop", "Built-in display"]:
                    display_id = "main"
                elif display_id.lower() in [
                    "secondary",
                    "Secondary display",
                    "Secondary monitor",
                    "External",
                    "External display",
                    "Second screen",
                ]:
                    display_id = "secondary"
                else:
                    try:
                        display_id = int(display_id)
                    except ValueError:
                        logger.warning(
                            f"Invalid display parameter: {display_id}, using default"
                        )
                        display_id = None

        logger.info(
            f"Taking screenshot with question: {question}, display: {display_id}"
        )

        success = await asyncio.to_thread(camera.capture, display_id)
        if not success:
            logger.error("Failed to capture screenshot")
            return json.dumps(
                {"success": False, "message": "Failed to capture screenshot"}
            )

        logger.info("Screenshot captured, starting analysis...")
        return await asyncio.to_thread(camera.analyze, question)

    add_tool(
        McpTool(
            "take_screenshot",
            (
                "[Desktop screenshot / screen analysis] Call this tool ONLY when the user "
                "wants to capture or analyze the DESKTOP SCREEN itself: screenshot, "
                "take a screenshot, look at the desktop, analyze the screen, what is "
                "on the desktop, screen capture, view the current interface, analyze "
                "the current page, screen OCR. "
                "This tool captures the physical desktop screen; it does NOT read "
                "document files attached in the chat — use document_manage for those. "
                "Features: 1) capture the whole desktop screen; 2) screen content recognition and analysis; "
                "3) screen OCR text extraction; 4) interface element analysis; 5) application recognition; "
                "6) error message screenshot analysis; 7) desktop state check; 8) multi-monitor screenshots. "
                "Parameter description: { question: 'the question you want to ask about the desktop/screen', "
                "display: 'monitor selection (optional)' }; "
                "display options: 'main'/'Main display'/'Laptop' (main monitor), 'secondary'/'Secondary display'/"
                "'External display' (secondary monitor), or leave it empty (all monitors); "
                "Applicable scenarios: desktop screenshots, screen analysis, interface issue diagnosis, "
                "application state inspection, error screenshot analysis, etc. "
                "Note: this tool captures the desktop, so make sure the user consents to the screenshot."
            ),
            PropertyList(
                [
                    Property("question", PropertyType.STRING),
                    Property("display", PropertyType.STRING, default_value=""),
                ]
            ),
            take_screenshot,
        )
    )
    logger.info("Registered take_screenshot (no global singleton)")
