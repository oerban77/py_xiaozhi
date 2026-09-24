"""Unified application launcher.

Select the appropriate launcher implementation according to the system.
"""

import asyncio
import platform
from typing import Any

from src.logging import get_logger

from .utils import find_best_matching_app

logger = get_logger()


async def launch_application(args: dict[str, Any]) -> bool:
    try:
        app_name = args["app_name"]
        logger.info(f"[AppLauncher] Attempting to launch app: {app_name}")

        matched_app = await _find_matching_application(app_name)
        if matched_app:
            logger.info(
                f"[AppLauncher] Matched app found: "
                f"{matched_app.get('display_name', matched_app.get('name', ''))}"
            )
            success = await _launch_matched_app(matched_app, app_name)
        else:
            logger.info(f"[AppLauncher] No exact match found; using original name: {app_name}")
            success = await _launch_by_name(app_name)

        if success:
            logger.info(f"[AppLauncher] App launched successfully: {app_name}")
        else:
            logger.warning(f"[AppLauncher] Failed to launch app: {app_name}")

        return success

    except KeyError:
        logger.error("[AppLauncher] Missing app_name parameter")
        return False
    except Exception as e:
        logger.error(f"[AppLauncher] Failed to launch app: {e}", exc_info=True)
        return False


async def _find_matching_application(app_name: str) -> dict[str, Any] | None:
    try:
        return await find_best_matching_app(app_name, "installed")
    except Exception as e:
        logger.warning(f"[AppLauncher] Error finding matching app: {e}", exc_info=True)
        return None


async def _launch_matched_app(
    matched_app: dict[str, Any], original_name: str
) -> bool:
    try:
        app_type = matched_app.get("type", "unknown")
        app_path = matched_app.get("path", matched_app.get("name", original_name))
        system = platform.system()

        if system == "Windows":
            if app_type == "uwp":
                from .launcher_windows import launch_uwp_app_by_path

                return await asyncio.to_thread(launch_uwp_app_by_path, app_path)
            elif app_type == "shortcut" and app_path.endswith(".lnk"):
                from .launcher_windows import launch_shortcut

                return await asyncio.to_thread(launch_shortcut, app_path)

        return await _launch_by_name(app_path)

    except Exception as e:
        logger.error(f"[AppLauncher] Failed to launch matched app: {e}", exc_info=True)
        return False


async def _launch_by_name(app_name: str) -> bool:
    try:
        system = platform.system()

        if system == "Windows":
            from .launcher_windows import launch_application

            return await asyncio.to_thread(launch_application, app_name)
        elif system == "Darwin":
            from .launcher_mac import launch_application

            return await asyncio.to_thread(launch_application, app_name)
        elif system == "Linux":
            from .launcher_linux import launch_application

            return await asyncio.to_thread(launch_application, app_name)
        else:
            logger.error(f"[AppLauncher] Unsupported OS: {system}")
            return False

    except Exception as e:
        logger.error(f"[AppLauncher] Failed to launch app: {e}", exc_info=True)
        return False
