"""Windows volume backend (pycaw / comtypes)."""

from __future__ import annotations

from typing import Any

from src.logging import get_logger

logger = get_logger()


class WindowsVolumeBackend:
    def __init__(self) -> None:
        self._module_cache: dict[str, Any] = {}
        self.volume_control = None
        self._init()

    def _lazy_import(self, module_name: str, attr: str | None = None) -> Any:
        if module_name in self._module_cache:
            module = self._module_cache[module_name]
        else:
            module = __import__(
                module_name, fromlist=["*"] if "." in module_name else []
            )
            self._module_cache[module_name] = module
        if attr:
            return getattr(module, attr)
        return module

    def _init(self) -> None:
        try:
            AudioUtilities = self._lazy_import("pycaw.pycaw", "AudioUtilities")

            devices = AudioUtilities.GetSpeakers()
            if devices is None:
                raise RuntimeError("No default audio output device found")

            # pycaw >= 20260921 wraps the raw IMMDevice in an AudioDevice helper:
            # AudioUtilities.GetSpeakers() no longer returns the COM object, so
            # devices.Activate(...) raises AttributeError. The endpoint volume is
            # exposed through the EndpointVolume property instead, which activates
            # and QueryInterface()s the IAudioEndpointVolume interface internally.
            if hasattr(devices, "EndpointVolume"):
                self.volume_control = devices.EndpointVolume
            else:
                # Legacy pycaw (< 20260921): GetSpeakers() returns the raw IMMDevice.
                POINTER = self._lazy_import("ctypes", "POINTER")
                cast = self._lazy_import("ctypes", "cast")
                CLSCTX_ALL = self._lazy_import("comtypes", "CLSCTX_ALL")
                IAudioEndpointVolume = self._lazy_import(
                    "pycaw.pycaw", "IAudioEndpointVolume"
                )
                interface = devices.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                )
                self.volume_control = cast(interface, POINTER(IAudioEndpointVolume))

            logger.debug("Windows volume control initialized")
        except Exception as e:
            logger.error(f"Windows volume control init failed: {e}", exc_info=True)
            raise

    def get_volume(self) -> int:
        try:
            volume_scalar = self.volume_control.GetMasterVolumeLevelScalar()
            return int(volume_scalar * 100)
        except Exception as e:
            logger.warning(f"Failed to get Windows volume: {e}", exc_info=True)
            return 70

    def set_volume(self, volume: int) -> None:
        try:
            self.volume_control.SetMasterVolumeLevelScalar(volume / 100.0, None)
        except Exception as e:
            logger.warning(f"Failed to set Windows volume: {e}", exc_info=True)

    def get_muted(self) -> bool:
        try:
            return bool(self.volume_control.GetMute())
        except Exception as e:
            logger.warning(f"Failed to get Windows mute state: {e}", exc_info=True)
            return False

    def set_muted(self, muted: bool) -> None:
        try:
            self.volume_control.SetMute(bool(muted), None)
        except Exception as e:
            logger.warning(f"Failed to set Windows mute state: {e}", exc_info=True)
