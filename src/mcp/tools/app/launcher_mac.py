"""macOS application launcher.

Provides application launching on macOS.
All subprocess calls use list form; shell=True is not used.
"""

import os
import subprocess

from src.logging import get_logger

logger = get_logger()


def launch_application(app_name: str) -> bool:
    """Launch an application on macOS.

    Args:
        app_name: application name

    Returns:
        bool: whether the launch succeeded
    """
    try:
        logger.info(f"[MacLauncher] Launching app: {app_name}")

        # Method 1: use the open -a command (safe list form)
        try:
            subprocess.Popen(
                ["open", "-a", app_name],
                start_new_session=True,
            )
            logger.info(f"[MacLauncher] Launched via open -a: {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[MacLauncher] open -a launch failed: {app_name}")

        # Method 2: use the application name directly
        try:
            subprocess.Popen(
                [app_name],
                start_new_session=True,
            )
            logger.info(f"[MacLauncher] Launched directly: {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[MacLauncher] Direct launch failed: {app_name}")

        # Method 3: try the Applications directory
        app_path = f"/Applications/{app_name}.app"
        if os.path.exists(app_path):
            subprocess.Popen(
                ["open", app_path],
                start_new_session=True,
            )
            logger.info(f"[MacLauncher] Launched via Applications folder: {app_name}")
            return True

        # Method 4: try open -a again as a last resort (no osascript)
        # The previous osascript + f-string approach had an AppleScript injection
        # vulnerability and has been removed
        try:
            subprocess.Popen(
                ["open", "-a", app_name, "--background"],
                start_new_session=True,
            )
            logger.info(f"[MacLauncher] Launched via open -a (background mode): {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[MacLauncher] open -a (background mode) launch failed: {app_name}")

        logger.warning(f"[MacLauncher] All macOS launch methods failed: {app_name}")
        return False

    except Exception as e:
        logger.error(f"[MacLauncher] macOS launch failed: {e}", exc_info=True)
        return False
