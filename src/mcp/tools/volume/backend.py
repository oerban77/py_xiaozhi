"""Volume backend protocol and factory."""

from __future__ import annotations

from typing import Protocol


class VolumeBackend(Protocol):
    """Platform volume implementation."""

    def get_volume(self) -> int:
        """Current volume 0-100."""
        ...

    def set_volume(self, volume: int) -> None:
        """Set the volume 0-100 (the caller already clamps it)."""
        ...
