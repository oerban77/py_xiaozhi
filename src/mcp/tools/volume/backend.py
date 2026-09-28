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

    def get_muted(self) -> bool:
        """Whether the output is currently muted (software mute of the default sink)."""
        ...

    def set_muted(self, muted: bool) -> None:
        """Mute or unmute the default output sink."""
        ...
