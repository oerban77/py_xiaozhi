"""Manage the current user's Windows startup entry."""

import subprocess
import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "py-xiaozhi"
_VALUE_NAME_TUI = "py-xiaozhi-tui"


def is_windows_startup_supported() -> bool:
    return sys.platform == "win32"


def _gui_command() -> str:
    """Build the GUI launch command (may be frozen or script)."""
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        launcher = Path(sys.argv[0]).expanduser().resolve()
        if launcher.suffix.lower() in {".exe", ".com"}:
            command = [str(launcher)]
        else:
            command = [sys.executable, str(launcher)]
    command.append("--start-minimized")
    return subprocess.list2cmdline(command)


def _tui_command() -> str:
    """Build the TUI launch command that starts a terminal window minimized."""
    return subprocess.list2cmdline(["cmd", "/c", "start", "", "/min", "xiaozhi-tui"])


def set_windows_startup_enabled(enabled: bool) -> None:
    """Create or remove the GUI startup entry. Raises on registry errors."""
    if not is_windows_startup_supported():
        raise OSError("Windows startup is only supported on Windows")

    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _gui_command())
        else:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except OSError as exc:
                if getattr(exc, "winerror", None) != 2:
                    raise


def set_tui_startup_enabled(enabled: bool) -> None:
    """Create or remove the TUI startup entry. Raises on registry errors."""
    if not is_windows_startup_supported():
        raise OSError("Windows startup is only supported on Windows")

    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, _VALUE_NAME_TUI, 0, winreg.REG_SZ, _tui_command())
        else:
            try:
                winreg.DeleteValue(key, _VALUE_NAME_TUI)
            except OSError as exc:
                if getattr(exc, "winerror", None) != 2:
                    raise