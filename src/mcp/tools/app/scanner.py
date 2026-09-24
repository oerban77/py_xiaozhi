"""Unified application scanner entry point.

Select the corresponding scanner implementation according to the current system
"""

import asyncio
import json
from typing import Any, Dict

from src.logging import get_logger

from .utils import get_system_scanner

logger = get_logger()


async def scan_installed_applications(args: Dict[str, Any]) -> str:
    """Scan all installed applications on the system.

    Args:
        args: dictionary containing scan parameters
            - force_refresh: whether to force a fresh scan (optional, default False)

    Returns:
        str: JSON-formatted list of applications
    """
    try:
        force_refresh = args.get("force_refresh", False)
        logger.info(f"[AppScanner] Scanning installed apps, force refresh: {force_refresh}")

        # Get the scanner for the current system
        scanner = get_system_scanner()
        if not scanner:
            error_msg = "Unsupported operating system"
            logger.error(f"[AppScanner] {error_msg}")
            return json.dumps(
                {
                    "success": False,
                    "total_count": 0,
                    "applications": [],
                    "message": error_msg,
                },
                ensure_ascii=False,
            )

        # Run the scan in a thread pool to avoid blocking the event loop
        apps = await asyncio.to_thread(scanner.scan_installed_applications)

        result = {
            "success": True,
            "total_count": len(apps),
            "applications": apps,
            "message": f"Successfully scanned {len(apps)} installed application(s)",
        }

        logger.info(f"[AppScanner] Scan complete; {len(apps)} app(s) found")
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        error_msg = f"Failed to scan applications: {str(e)}"
        logger.error(f"[AppScanner] {error_msg}", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "total_count": 0,
                "applications": [],
                "message": error_msg,
            },
            ensure_ascii=False,
        )


async def list_running_applications(args: Dict[str, Any]) -> str:
    """List applications currently running in the system.

    Args:
        args: dictionary containing filter parameters
            - filter_name: application name filter condition (optional)

    Returns:
        str: JSON-formatted list of running applications
    """
    try:
        filter_name = args.get("filter_name", "")
        logger.info(f"[AppScanner] Listing running apps, filter: {filter_name}")

        # Get the scanner for the current system
        scanner = get_system_scanner()
        if not scanner:
            error_msg = "Unsupported operating system"
            logger.error(f"[AppScanner] {error_msg}")
            return json.dumps(
                {
                    "success": False,
                    "total_count": 0,
                    "applications": [],
                    "message": error_msg,
                },
                ensure_ascii=False,
            )

        # Run the scan in a thread pool to avoid blocking the event loop
        apps = await asyncio.to_thread(scanner.scan_running_applications)

        # Application filter conditions
        if filter_name:
            filter_lower = filter_name.lower()
            filtered_apps = []
            for app in apps:
                if (
                    filter_lower in app.get("name", "").lower()
                    or filter_lower in app.get("display_name", "").lower()
                    or filter_lower in app.get("command", "").lower()
                ):
                    filtered_apps.append(app)
            apps = filtered_apps

        result = {
            "success": True,
            "total_count": len(apps),
            "applications": apps,
            "message": f"Found {len(apps)} running application(s)",
        }

        logger.info(f"[AppScanner] Listing complete; {len(apps)} running app(s) found")
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        error_msg = f"Failed to list running applications: {str(e)}"
        logger.error(f"[AppScanner] {error_msg}", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "total_count": 0,
                "applications": [],
                "message": error_msg,
            },
            ensure_ascii=False,
        )
