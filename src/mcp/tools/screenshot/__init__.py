"""Screenshot tools for MCP.

The screenshot instance is created/injected by the caller.
"""

from .register import create_screenshot_camera, register_screenshot_tools

__all__ = [
    "create_screenshot_camera",
    "register_screenshot_tools",
]
