"""Cross-platform volume controller facade.

Platform implementations live in the windows / macos / linux modules; this class handles detection,
wiring, and dependency checks.
"""

from __future__ import annotations

import platform
import shutil

from src.logging import get_logger

from .backend import VolumeBackend


class VolumeController:
    """Cross-platform volume controller."""

    DEFAULT_VOLUME = 70

    def __init__(self):
        self.logger = get_logger()
        self.system = platform.system()
        self.is_arm = platform.machine().startswith(("arm", "aarch"))
        self._backend: VolumeBackend = self._create_backend()

    def _create_backend(self) -> VolumeBackend:
        if self.system == "Windows":
            from .windows import WindowsVolumeBackend

            return WindowsVolumeBackend()
        if self.system == "Darwin":
            from .macos import MacosVolumeBackend

            return MacosVolumeBackend()
        if self.system == "Linux":
            from .linux import LinuxVolumeBackend

            return LinuxVolumeBackend()

        self.logger.warning(f"Unsupported OS: {self.system}")
        raise NotImplementedError(f"Unsupported operating system: {self.system}")

    def get_volume(self) -> int:
        """Get the current volume (0-100)."""
        try:
            return self._backend.get_volume()
        except Exception as e:
            self.logger.warning(f"Failed to get volume: {e}", exc_info=True)
            return self.DEFAULT_VOLUME

    def set_volume(self, volume: int) -> None:
        """Set the volume (0-100)."""
        volume = max(0, min(100, volume))
        try:
            self._backend.set_volume(volume)
        except Exception as e:
            self.logger.warning(f"Failed to set volume: {e}", exc_info=True)

    @staticmethod
    def check_dependencies() -> bool:
        """Check and report missing dependencies."""
        system = platform.system()
        missing: list[str] = []

        VolumeController._check_python_modules(system, missing)
        if system == "Linux":
            VolumeController._check_linux_tools(missing)
        return VolumeController._report_missing_dependencies(system, missing)

    @staticmethod
    def _check_python_modules(system: str, missing: list[str]) -> None:
        if system == "Windows":
            for module in ["pycaw", "comtypes"]:
                try:
                    __import__(module)
                except ImportError:
                    missing.append(module)
        elif system == "Darwin":
            try:
                __import__("applescript")
            except ImportError:
                missing.append("applescript")

    @staticmethod
    def _check_linux_tools(missing: list[str]) -> None:
        tools = ["pactl", "wpctl", "amixer"]
        if not any(shutil.which(tool) for tool in tools):
            missing.append("pulseaudio-utils, wireplumber, or alsa-utils")

    @staticmethod
    def _report_missing_dependencies(system: str, missing: list[str]) -> bool:
        if not missing:
            return True
        logger = get_logger()
        logger.warning(f"Volume control requires these dependencies, which were not found: {', '.join(missing)}")
        if system in ["Windows", "Darwin"]:
            logger.warning(f"Install them with: pip install {' '.join(missing)}")
        elif system == "Linux":
            logger.warning(
                f"Install them with: sudo apt-get install {' '.join(missing)}"
            )
        return False
