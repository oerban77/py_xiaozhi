"""Linux application launcher.

Provides application launching on Linux.
All subprocess calls use list form; shell=True is not used.
"""

import os
import subprocess

from src.logging import get_logger

logger = get_logger()


def launch_application(app_name: str) -> bool:
    """Launch an application on Linux.

    Args:
        app_name: application name

    Returns:
        bool: whether the launch succeeded
    """
    try:
        logger.info(f"[LinuxLauncher] Launching app: {app_name}")

        # Method 1: use the application name directly
        try:
            subprocess.Popen(
                [app_name],
                start_new_session=True,
            )
            logger.info(f"[LinuxLauncher] Launched directly: {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[LinuxLauncher] Direct launch failed: {app_name}")

        # Method 2: look up the application path with which
        try:
            result = subprocess.run(
                ["which", app_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                app_path = result.stdout.strip()
                subprocess.Popen(
                    [app_path],
                    start_new_session=True,
                )
                logger.info(f"[LinuxLauncher] Launched via which: {app_name}")
                return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[LinuxLauncher] which launch failed: {app_name}")

        # Method 3: use xdg-open (for desktop environments)
        try:
            subprocess.Popen(
                ["xdg-open", app_name],
                start_new_session=True,
            )
            logger.info(f"[LinuxLauncher] Launched via xdg-open: {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[LinuxLauncher] xdg-open launch failed: {app_name}")

        # Method 4: try common application paths
        common_paths = [
            f"/usr/bin/{app_name}",
            f"/usr/local/bin/{app_name}",
            f"/opt/{app_name}/{app_name}",
            f"/snap/bin/{app_name}",
        ]

        for path in common_paths:
            if os.path.exists(path):
                subprocess.Popen(
                    [path],
                    start_new_session=True,
                )
                logger.info(
                    f"[LinuxLauncher] Launched via common path: {app_name} ({path})"
                )
                return True

        # Method 5: try launching via a .desktop file
        desktop_dirs = [
            "/usr/share/applications",
            "/usr/local/share/applications",
            os.path.expanduser("~/.local/share/applications"),
        ]

        for desktop_dir in desktop_dirs:
            desktop_file = os.path.join(desktop_dir, f"{app_name}.desktop")
            if os.path.exists(desktop_file):
                subprocess.Popen(
                    ["gtk-launch", f"{app_name}.desktop"],
                    start_new_session=True,
                )
                logger.info(f"[LinuxLauncher] Launched via .desktop file: {app_name}")
                return True

        logger.warning(f"[LinuxLauncher] All Linux launch methods failed: {app_name}")
        return False

    except Exception as e:
        logger.error(f"[LinuxLauncher] Linux launch failed: {e}", exc_info=True)
        return False
