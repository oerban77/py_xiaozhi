"""macOS volume backend (applescript)."""

from __future__ import annotations

from typing import Any

from src.logging import get_logger

logger = get_logger()

DEFAULT_VOLUME = 70


class MacosVolumeBackend:
    def __init__(self) -> None:
        self._module_cache: dict[str, Any] = {}
        self._init()

    def _lazy_import(self, module_name: str) -> Any:
        if module_name not in self._module_cache:
            self._module_cache[module_name] = __import__(module_name)
        return self._module_cache[module_name]

    def _init(self) -> None:
        try:
            applescript = self._lazy_import("applescript")
            result = applescript.run("get volume settings")
            if not result or result.code != 0:
                raise Exception("Cannot access macOS volume control")
            logger.debug("macOS volume control initialized")
        except Exception as e:
            logger.error(f"macOS volume control init failed: {e}", exc_info=True)
            raise

    def get_volume(self) -> int:
        try:
            applescript = self._lazy_import("applescript")
            result = applescript.run("output volume of (get volume settings)")
            if result and result.out:
                return int(result.out.strip())
            return DEFAULT_VOLUME
        except Exception as e:
            logger.warning(f"Failed to get macOS volume: {e}", exc_info=True)
            return DEFAULT_VOLUME

    def set_volume(self, volume: int) -> None:
        try:
            applescript = self._lazy_import("applescript")
            applescript.run(f"set volume output volume {volume}")
        except Exception as e:
            logger.warning(f"Failed to set macOS volume: {e}", exc_info=True)

    def get_muted(self) -> bool:
        try:
            applescript = self._lazy_import("applescript")
            result = applescript.run("output muted of (get volume settings)")
            if result and result.out:
                return result.out.strip().lower() == "true"
            return False
        except Exception as e:
            logger.warning(f"Failed to get macOS mute state: {e}", exc_info=True)
            return False

    def set_muted(self, muted: bool) -> None:
        try:
            applescript = self._lazy_import("applescript")
            applescript.run(f"set volume output muted {'true' if muted else 'false'}")
        except Exception as e:
            logger.warning(f"Failed to set macOS mute state: {e}", exc_info=True)
