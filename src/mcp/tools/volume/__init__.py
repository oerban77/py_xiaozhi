"""Volume control tools.

- VolumeController / platform backends: see volume_controller / windows|macos|linux
- register_volume_tools: register with McpServer (closure holds the controller, no module-level singleton)
"""

from .register import create_volume_controller, register_volume_tools
from .volume_controller import VolumeController

__all__ = [
    "VolumeController",
    "create_volume_controller",
    "register_volume_tools",
]
