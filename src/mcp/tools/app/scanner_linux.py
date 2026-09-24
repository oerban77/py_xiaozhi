"""Linux application scanner.

Dedicated to scanning and managing applications on Linux.
"""

import platform
import subprocess
from pathlib import Path
from typing import Dict, List

from src.logging import get_logger

logger = get_logger()


def scan_installed_applications() -> List[Dict[str, str]]:
    """Scan installed applications on Linux.

    Returns:
        List[Dict[str, str]]: Application list
    """
    if platform.system() != "Linux":
        return []

    apps = []

    # Scan .desktop files
    desktop_dirs = [
        "/usr/share/applications",
        "/usr/local/share/applications",
        Path.home() / ".local/share/applications",
    ]

    for desktop_dir in desktop_dirs:
        desktop_path = Path(desktop_dir)
        if desktop_path.exists():
            for desktop_file in desktop_path.glob("*.desktop"):
                try:
                    app_info = _parse_desktop_file(desktop_file)
                    if app_info and _should_include_app(app_info["display_name"]):
                        apps.append(app_info)
                except Exception as e:
                    logger.debug(
                        f"[LinuxScanner] Failed to parse .desktop file {desktop_file}: {e}"
                    )

    # Add common Linux system applications
    system_apps = [
        {
            "name": "gedit",
            "display_name": "Text Editor",
            "path": "gedit",
            "type": "system",
        },
        {
            "name": "firefox",
            "display_name": "FirefoxBrowser",
            "path": "firefox",
            "type": "system",
        },
        {
            "name": "gnome-calculator",
            "display_name": "Calculator",
            "path": "gnome-calculator",
            "type": "system",
        },
        {
            "name": "nautilus",
            "display_name": "File Manager",
            "path": "nautilus",
            "type": "system",
        },
        {
            "name": "gnome-terminal",
            "display_name": "Terminal",
            "path": "gnome-terminal",
            "type": "system",
        },
        {
            "name": "gnome-control-center",
            "display_name": "Settings",
            "path": "gnome-control-center",
            "type": "system",
        },
    ]
    apps.extend(system_apps)

    logger.info(f"[LinuxScanner] Scan complete; {len(apps)} app(s) found")
    return apps


def scan_running_applications() -> List[Dict[str, str]]:
    """Scan running applications on Linux.

    Returns:
        List[Dict[str, str]]: list of running applications
    """
    if platform.system() != "Linux":
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

        logger.info(f"[LinuxScanner] Found {len(apps)} running app(s) found")
        return apps

    except Exception as e:
        logger.error(f"[LinuxScanner] Failed to scan running apps: {e}", exc_info=True)
        return []


def _parse_desktop_file(desktop_file: Path) -> Dict[str, str]:
    """Parse .desktop files.

    Args:
        desktop_file: .desktop file path

    Returns:
        Dict[str, str]: application information
    """
    try:
        with open(desktop_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse .desktop files
        name = ""
        display_name = ""
        exec_cmd = ""

        for line in content.split("\n"):
            if line.startswith("Name="):
                display_name = line.split("=", 1)[1]
            elif line.startswith("Name[zh_CN]="):
                display_name = line.split("=", 1)[1]  # Prefer localized names
            elif line.startswith("Exec="):
                exec_cmd = line.split("=", 1)[1].split()[0]  # Take the first command

        if display_name and exec_cmd:
            name = clean_app_name(display_name)
            return {
                "name": name,
                "display_name": display_name,
                "path": exec_cmd,
                "type": "desktop",
            }

        return None

    except Exception:
        return None


def _should_include_app(display_name: str) -> bool:
    """Decide whether this application should be included.

    Args:
        display_name: application display name

    Returns:
        bool: whether to include it
    """
    if not display_name:
        return False

    # Excluded application patterns
    exclude_patterns = [
        # System components
        "gnome-",
        "kde-",
        "xfce-",
        "unity-",
        # Development tools
        "gdb",
        "valgrind",
        "strace",
        "ltrace",
        # System tools
        "dconf",
        "gsettings",
        "xdg-",
        "desktop-file-",
        # otherSystem components
        "help",
        "about",
        "preferences",
        "settings",
    ]

    display_lower = display_name.lower()

    # Check exclusion patterns
    for pattern in exclude_patterns:
        if pattern in display_lower:
            return False

    return True


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
        # Kernel and core processes
        "kthreadd",
        "ksoftirqd",
        "migration",
        "rcu_",
        "watchdog",
        "systemd",
        "init",
        "kernel",
        "kworker",
        "kcompactd",
        # System services
        "dbus",
        "networkd",
        "resolved",
        "logind",
        "udevd",
        "cron",
        "rsyslog",
        "ssh",
        "avahi",
        "cups",
        # Desktop environment service
        "gnome-",
        "kde-",
        "xfce-",
        "unity-",
        "compiz",
        "pulseaudio",
        "pipewire",
        "wireplumber",
        # X11/Wayland
        "Xorg",
        "wayland",
        "weston",
        "mutter",
        "kwin",
    }

    # Check whether it is a system process
    comm_lower = comm.lower()
    command_lower = command.lower()

    # Exclude empty names or system processes
    if not comm or any(proc in comm_lower for proc in system_processes):
        return False

    # Exclude processes under system paths
    if any(
        path in command_lower
        for path in [
            "/usr/libexec/",
            "/usr/lib/",
            "/lib/",
            "/sbin/",
            "/usr/sbin/",
            "/bin/systemd",
            "/usr/bin/dbus",
        ]
    ):
        return False

    # Exclude obvious system services
    if any(
        keyword in command_lower
        for keyword in ["daemon", "service", "helper", "agent", "monitor"]
    ):
        return False

    # Only include user applications
    user_app_indicators = [
        "/usr/bin/",
        "/usr/local/bin/",
        "/opt/",
        "/home/",
        "/snap/",
        "/flatpak/",
    ]

    return any(indicator in command_lower for indicator in user_app_indicators)


def _extract_app_name(comm: str, command: str) -> str:
    """Extract the application name from process information.

    Args:
        comm: process name
        command: full command

    Returns:
        str: application name
    """
    # Attempt to extract the application name from the command path
    if "/" in command:
        try:
            # Get executable file name
            exec_path = command.split()[0]
            app_name = Path(exec_path).name

            # Remove common suffixes
            if app_name.endswith(".py"):
                app_name = app_name[:-3]
            elif app_name.endswith(".sh"):
                app_name = app_name[:-3]

            return app_name
        except (IndexError, AttributeError):
            pass

    # Fall back to the process name
    return comm if comm else "Unknown"


from .utils import clean_app_name
