"""macOS application scanner.

Dedicated to scanning and managing applications on macOS.
"""

import platform
import subprocess
from pathlib import Path
from typing import Dict, List

from src.logging import get_logger

logger = get_logger()


def scan_installed_applications() -> List[Dict[str, str]]:
    """Scan installed applications on macOS.

    Returns:
        List[Dict[str, str]]: Application list
    """
    if platform.system() != "Darwin":
        return []

    apps = []

    # Scan the /Applications directory
    applications_dir = Path("/Applications")
    if applications_dir.exists():
        for app_path in applications_dir.glob("*.app"):
            app_name = app_path.stem
            clean_name = clean_app_name(app_name)
            apps.append(
                {
                    "name": clean_name,
                    "display_name": app_name,
                    "path": str(app_path),
                    "type": "application",
                }
            )

    # Scan the user applications directory
    user_apps_dir = Path.home() / "Applications"
    if user_apps_dir.exists():
        for app_path in user_apps_dir.glob("*.app"):
            app_name = app_path.stem
            clean_name = clean_app_name(app_name)
            apps.append(
                {
                    "name": clean_name,
                    "display_name": app_name,
                    "path": str(app_path),
                    "type": "user_application",
                }
            )

    # Add common system applications
    system_apps = [
        {
            "name": "Calculator",
            "display_name": "Calculator",
            "path": "Calculator",
            "type": "system",
        },
        {
            "name": "TextEdit",
            "display_name": "TextEdit",
            "path": "TextEdit",
            "type": "system",
        },
        {
            "name": "Preview",
            "display_name": "Preview",
            "path": "Preview",
            "type": "system",
        },
        {
            "name": "Safari",
            "display_name": "SafariBrowser",
            "path": "Safari",
            "type": "system",
        },
        {"name": "Finder", "display_name": "Finder", "path": "Finder", "type": "system"},
        {
            "name": "Terminal",
            "display_name": "Terminal",
            "path": "Terminal",
            "type": "system",
        },
        {
            "name": "System Preferences",
            "display_name": "System Preferences",
            "path": "System Preferences",
            "type": "system",
        },
    ]
    apps.extend(system_apps)

    logger.info(f"[MacScanner] Scan complete; {len(apps)} app(s) found")
    return apps


def scan_running_applications() -> List[Dict[str, str]]:
    """Scan running applications on macOS.

    Returns:
        List[Dict[str, str]]: list of running applications
    """
    if platform.system() != "Darwin":
        return []

    apps = []

    try:
        # Get process information with the ps command
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,comm,command"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")[1:]  # Skip the header row

            for line in lines:
                parts = line.strip().split(None, 3)
                if len(parts) >= 4:
                    pid, ppid, comm, command = parts

                    # Filter out processes we are not interested in
                    if _should_include_process(comm, command):
                        display_name = _extract_app_name(comm, command)
                        clean_name = clean_app_name(display_name)

                        apps.append(
                            {
                                "pid": int(pid),
                                "ppid": int(ppid),
                                "name": clean_name,
                                "display_name": display_name,
                                "command": command,
                                "type": "application",
                            }
                        )

        logger.info(f"[MacScanner] Found {len(apps)} running app(s) found")
        return apps

    except Exception as e:
        logger.error(f"[MacScanner] Failed to scan running apps: {e}", exc_info=True)
        return []


def _should_include_process(comm: str, command: str) -> bool:
    """Decide whether this process should be included.

    Args:
        comm: process name
        command: full command

    Returns:
        bool: whether to include it
    """
    # Exclude system processes and services
    system_processes = {
        # Core system processes
        "kernel_task",
        "launchd",
        "kextd",
        "UserEventAgent",
        "cfprefsd",
        "loginwindow",
        "WindowServer",
        "SystemUIServer",
        "Dock",
        "Finder",
        "ControlCenter",
        "NotificationCenter",
        "WallpaperAgent",
        "Spotlight",
        "WiFiAgent",
        "CoreLocationAgent",
        "bluetoothd",
        "wirelessproxd",
        # System services
        "com.apple.",
        "suhelperd",
        "softwareupdated",
        "cloudphotod",
        "identityservicesd",
        "imagent",
        "sharingd",
        "remindd",
        "contactsd",
        "accountsd",
        "CallHistorySyncHelper",
        "CallHistoryPluginHelper",
        # Drivers and extensions
        "AppleSpell",
        "coreaudiod",
        "audio",
        "webrtc",
        "chrome_crashpad_handler",
        "crashpad_handler",
        "fsnotifier",
        "mdworker",
        "mds",
        "spotlight",
        # Other system components
        "automountd",
        "autofsd",
        "aslmanager",
        "syslogd",
        "ntpd",
        "mDNSResponder",
        "distnoted",
        "notifyd",
        "powerd",
        "thermalmonitord",
        "watchdogd",
    }

    # Check whether it is a system process
    comm_lower = comm.lower()
    command_lower = command.lower()

    # Exclude empty names or system paths
    if not comm or comm_lower in system_processes:
        return False

    # Exclude processes under system paths
    if any(
        path in command_lower
        for path in [
            "/system/library/",
            "/library/apple/",
            "/usr/libexec/",
            "/system/applications/utilities/",
            "/private/var/",
            "com.apple.",
            ".xpc/",
            ".framework/",
            ".appex/",
            "helper (gpu)",
            "helper (renderer)",
            "helper (plugin)",
            "crashpad_handler",
            "fsnotifier",
        ]
    ):
        return False

    # Exclude obvious system services
    if any(
        keyword in command_lower
        for keyword in [
            "xpcservice",
            "daemon",
            "agent",
            "service",
            "monitor",
            "updater",
            "sync",
            "backup",
            "cache",
            "log",
        ]
    ):
        return False

    # Only include user applications
    user_app_indicators = ["/applications/", "/users/", "~/", ".app/contents/macos/"]

    return any(indicator in command_lower for indicator in user_app_indicators)


def _extract_app_name(comm: str, command: str) -> str:
    """Extract the application name from process information.

    Args:
        comm: process name
        command: full command

    Returns:
        str: application name
    """
    # Try to extract the .app name from the command path
    if ".app/Contents/MacOS/" in command:
        try:
            app_path = command.split(".app/Contents/MacOS/")[0] + ".app"
            app_name = Path(app_path).name.replace(".app", "")
            return app_name
        except (IndexError, AttributeError):
            pass

    # Try to extract from the /Applications/ path
    if "/Applications/" in command:
        try:
            parts = command.split("/Applications/")[1].split("/")[0]
            if parts.endswith(".app"):
                return parts.replace(".app", "")
        except (IndexError, AttributeError):
            pass

    # Fall back to the process name
    return comm if comm else "Unknown"


from .utils import clean_app_name
