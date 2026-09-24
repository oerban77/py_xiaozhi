"""Application termination and running process management.

Provides cross-platform process termination and listing via process_manager (psutil).
"""

import asyncio
import json
from typing import Any

from src.logging import get_logger

from .process_manager import kill_application_by_name
from .process_manager import list_running_applications as _list_apps

logger = get_logger()


async def kill_application(args: dict[str, Any]) -> bool:
    """Close an application.

    Args:
        args: parameter dict containing the application name
            - app_name: application name
            - force: whether to force close (optional, default False)

    Returns:
        Whether the close succeeded
    """
    try:
        app_name = args["app_name"]
        force = args.get("force", False)
        logger.info(f"[AppKiller] Attempting to close app: {app_name}, force: {force}")

        success = await asyncio.to_thread(kill_application_by_name, app_name, force)

        if success:
            logger.info(f"[AppKiller] App closed successfully: {app_name}")
        else:
            logger.warning(f"[AppKiller] Failed to close app: {app_name}")

        return success

    except Exception as e:
        logger.error(f"[AppKiller] Error closing app: {e}", exc_info=True)
        return False


async def list_running_applications(args: dict[str, Any]) -> str:
    """List all running applications.

    Args:
        args: dict containing the listing parameters
            - filter_name: application name filter (optional)

    Returns:
        JSON-formatted list of running applications
    """
    try:
        filter_name = args.get("filter_name", "")
        logger.info(f"[AppKiller] Listing running apps, filter: {filter_name}")

        apps = await asyncio.to_thread(_list_apps, filter_name)

        result = {
            "success": True,
            "total_count": len(apps),
            "applications": apps[:50],
            "message": f"Found {len(apps)} running application(s)",
        }

        logger.info(f"[AppKiller] Listing complete; {len(apps)} running app(s) found")
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        error_msg = f"Failed to list running applications: {e}"
        logger.error(f"[AppKiller] {error_msg}", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "total_count": 0,
                "applications": [],
                "message": error_msg,
            },
            ensure_ascii=False,
        )
