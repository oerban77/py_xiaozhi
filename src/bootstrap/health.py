"""Startup health gate and audio degradation strategy.

Independent of session business logic: decides whether to exit / continue degraded
based only on the set of failed plugins and environment variables.
"""

from __future__ import annotations

import os
from typing import Optional, Protocol

# Exit directly on startup failure to avoid a zombie wait_shutdown
CRITICAL_PLUGINS = ("ui",)
# Audio is critical by default; when XIAOZHI_DEGRADED_AUDIO=1 a failure only degrades instead of exiting
AUDIO_CRITICAL = True

DEGRADED_AUDIO_NOTICE = (
    "Degraded mode: audio is unavailable (no microphone/speakers). Settings still work; "
    "please restart after fixing the device."
)


class _PluginHealth(Protocol):
    def is_failed(self, name: str) -> bool: ...


def audio_is_fatal() -> bool:
    """Whether an audio failure should cause the process to exit.

    - XIAOZHI_DISABLE_AUDIO=1: intentionally disabled, not treated as a failure
    - XIAOZHI_DEGRADED_AUDIO=1: continue degraded on failure (UI/settings still usable)
    - Default: an audio failure exits with code 1
    """
    if os.getenv("XIAOZHI_DISABLE_AUDIO") == "1":
        return False
    if os.getenv("XIAOZHI_DEGRADED_AUDIO") == "1":
        return False
    return AUDIO_CRITICAL


def check_critical_plugins(plugins: _PluginHealth) -> Optional[str]:
    """Check whether the critical plugins are available.

    Returns:
        An error description; None when everything is healthy
    """
    failed: list[str] = []
    for name in CRITICAL_PLUGINS:
        if plugins.is_failed(name):
            failed.append(name)

    if audio_is_fatal() and plugins.is_failed("audio"):
        failed.append("audio")

    if not failed:
        return None

    hints = []
    if "audio" in failed:
        hints.append(
            "For audio debugging you can set XIAOZHI_DISABLE_AUDIO=1; "
            "or set XIAOZHI_DEGRADED_AUDIO=1 to continue without a microphone (UI/settings still usable)."
        )
    return (
        f"Critical plugin startup failed: {', '.join(failed)}."
        "The application will exit to avoid spinning idle (zombie)."
        + (" " + " ".join(hints) if hints else "")
    )
