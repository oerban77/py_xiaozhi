"""General application management utilities.

Provides unified application matching, lookup, caching and name cleaning.
"""

import platform
import re
import time
from typing import Any

from src.logging import get_logger

logger = get_logger()

# Global application cache
_cached_applications: list[dict[str, Any]] | None = None
_cache_timestamp: float = 0
_cache_duration = 300  # cache for 5 minutes


def clean_app_name(name: str) -> str:
    """Cleans the application name, removing version numbers and special characters.

    Args:
        name: original name

    Returns:
        str: cleaned name
    """
    if not name:
        return ""

    # Remove common version number patterns
    name = re.sub(r"\s+v?\d+[\.\d]*", "", name)
    name = re.sub(r"\s*\(\d+\)", "", name)
    name = re.sub(r"\s*\[.*?\]", "", name)

    # Remove extra spaces
    name = " ".join(name.split())

    return name.strip()


class AppMatcher:
    """
    Unified application matcher.
    """

    # Special application name mappings - sorted by length to avoid short names matching first
    SPECIAL_MAPPINGS = {
        "qqmusic": ["qqmusic", "qqmusic", "qq music"],
        "qqmusic": ["qqmusic", "qqmusic", "qq music"],
        "qq music": ["qqmusic", "qqmusic", "qq music"],
        "tencent meeting": ["tencent meeting", "腾讯会议", "voovmeeting"],
        "腾讯会议": ["tencent meeting", "腾讯会议", "voovmeeting"],
        "google chrome": ["chrome", "googlechrome", "google chrome"],
        "microsoft edge": ["msedge", "edge", "microsoft edge"],
        "microsoft office": [
            "microsoft office",
            "office",
            "word",
            "excel",
            "powerpoint",
        ],
        "microsoft word": ["microsoft word", "word"],
        "microsoft excel": ["microsoft excel", "excel"],
        "microsoft powerpoint": ["microsoft powerpoint", "powerpoint"],
        "visual studio code": ["code", "vscode", "visual studio code"],
        "wps office": ["wps", "wps office"],
        "qq": ["qq", "qqnt", "tencentqq"],
        "wechat": ["wechat", "weixin", "微信"],
        "dingtalk": ["dingtalk", "钉钉", "ding"],
        "钉钉": ["dingtalk", "钉钉", "ding"],
        "chrome": ["chrome", "googlechrome", "google chrome"],
        "firefox": ["firefox", "mozilla"],
        "edge": ["msedge", "edge", "microsoft edge"],
        "safari": ["safari"],
        "notepad": ["notepad", "notepad++"],
        "calculator": ["calc", "calculator", "calculatorapp"],
        "calc": ["calc", "calculator", "calculatorapp"],
        "feishu": ["feishu", "飞书", "lark"],
        "vscode": ["code", "vscode", "visual studio code"],
        "pycharm": ["pycharm", "pycharm64"],
        "cursor": ["cursor"],
        "typora": ["typora"],
        "wps": ["wps", "wps office"],
        "office": ["microsoft office", "office", "word", "excel", "powerpoint"],
        "word": ["microsoft word", "word"],
        "excel": ["microsoft excel", "excel"],
        "powerpoint": ["microsoft powerpoint", "powerpoint"],
        "finder": ["finder"],
        "terminal": ["terminal", "iterm"],
        "iterm": ["iterm", "iterm2"],
    }

    # Process grouping map (used when terminating groups)
    PROCESS_GROUPS = {
        "chrome": "chrome",
        "googlechrome": "chrome",
        "firefox": "firefox",
        "edge": "edge",
        "msedge": "edge",
        "safari": "safari",
        "qq": "qq",
        "qqnt": "qq",
        "tencentqq": "qq",
        "qqmusic": "qqmusic",
        "QQMUSIC": "QQMUSIC",
        "QQmusic": "QQmusic",
        "wechat": "wechat",
        "weixin": "wechat",
        "dingtalk": "dingtalk",
        "钉钉": "dingtalk",
        "feishu": "feishu",
        "飞书": "feishu",
        "lark": "feishu",
        "vscode": "vscode",
        "code": "vscode",
        "cursor": "cursor",
        "pycharm": "pycharm",
        "pycharm64": "pycharm",
        "typora": "typora",
        "calculatorapp": "calculator",
        "calc": "calculator",
        "calculator": "calculator",
        "tencent meeting": "tencent_meeting",
        "腾讯会议": "tencent_meeting",
        "voovmeeting": "tencent_meeting",
        "wps": "wps",
        "word": "word",
        "excel": "excel",
        "powerpoint": "powerpoint",
        "finder": "finder",
        "terminal": "terminal",
        "iterm": "iterm",
        "iterm2": "iterm",
    }

    @classmethod
    def normalize_name(cls, name: str) -> str:
        """
        Standardize application names.
        """
        if not name:
            return ""

        # Remove the .exe suffix
        name = name.lower().replace(".exe", "")

        # Remove version numbers and special characters
        name = re.sub(r"\s+v?\d+[\.\d]*", "", name)
        name = re.sub(r"\s*\(\d+\)", "", name)
        name = re.sub(r"\s*\[.*?\]", "", name)
        name = " ".join(name.split())

        return name.strip()

    @classmethod
    def get_process_group(cls, process_name: str) -> str:
        """
        Get the process group it belongs to.
        """
        normalized = cls.normalize_name(process_name)

        # Check direct mapping
        if normalized in cls.PROCESS_GROUPS:
            return cls.PROCESS_GROUPS[normalized]

        # Check containment relationship
        for key, group in cls.PROCESS_GROUPS.items():
            if key in normalized or normalized in key:
                return group

        return normalized

    @classmethod
    def match_application(cls, target_name: str, app_info: dict[str, Any]) -> int:
        """Match applications and return a match score.

        Args:
            target_name: target application name
            app_info: application information

        Returns:
            int: match score (0-100); 0 means no match
        """
        if not target_name or not app_info:
            return 0

        target_lower = target_name.lower()
        app_name = app_info.get("name", "").lower()
        display_name = app_info.get("display_name", "").lower()
        window_title = app_info.get("window_title", "").lower()
        exe_path = app_info.get("command", "").lower()

        # 1. Exact match (100 points)
        if target_lower == app_name or target_lower == display_name:
            return 100

        # 2. Special mapping match (95-98 points) - prefer matching the more specific keyword
        best_special_score = 0

        for key in cls.SPECIAL_MAPPINGS:
            if key in target_lower or target_lower == key:
                # Check whether a matching alias exists
                for alias in cls.SPECIAL_MAPPINGS[key]:
                    if alias.lower() in app_name or alias.lower() in display_name:
                        # Compute the match score: more specific matches score higher
                        if target_lower == key:
                            score = 98  # Exact match special mapping keys
                        elif len(key) > len(target_lower) * 0.8:
                            score = 97  # Close-length matches
                        else:
                            score = 95  # General special mapping match

                        if score > best_special_score:
                            best_special_score = score

        if best_special_score > 0:
            return best_special_score

        # 3. Normalized name match (90 points)
        normalized_target = cls.normalize_name(target_name)
        normalized_app = cls.normalize_name(app_info.get("name", ""))
        normalized_display = cls.normalize_name(app_info.get("display_name", ""))

        if (
            normalized_target == normalized_app
            or normalized_target == normalized_display
        ):
            return 90

        # 4. Containment match (70-80 points)
        if target_lower in app_name:
            return 80
        if target_lower in display_name:
            return 75
        if app_name and app_name in target_lower:
            # Avoid short names accidentally matching longer names
            if len(app_name) < len(target_lower) * 0.5:
                return 50  # Lower score
            return 70

        # 5. Window title match (60 points)
        if window_title and target_lower in window_title:
            return 60

        # 6. Path match (50 points)
        if exe_path and target_lower in exe_path:
            return 50

        # 7. Fuzzy match (30 points)
        if cls._fuzzy_match(target_lower, app_name) or cls._fuzzy_match(
            target_lower, display_name
        ):
            return 30

        return 0

    @classmethod
    def _fuzzy_match(cls, target: str, candidate: str) -> bool:
        """
        Fuzzy match.
        """
        if not target or not candidate:
            return False

        # Remove all non-alphanumeric characters for comparison
        target_clean = re.sub(r"[^a-zA-Z0-9一-鿿]", "", target)
        candidate_clean = re.sub(r"[^a-zA-Z0-9一-鿿]", "", candidate)

        return target_clean in candidate_clean or candidate_clean in target_clean


async def get_cached_applications(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Get the cached application list.

    Args:
        force_refresh: whether to force a cache refresh

    Returns:
        Application list
    """
    global _cached_applications, _cache_timestamp

    current_time = time.time()

    # Check if the cache is valid
    if (
        not force_refresh
        and _cached_applications is not None
        and (current_time - _cache_timestamp) < _cache_duration
    ):
        logger.debug(
            f"[AppUtils] Using cached app list, cached {int(current_time - _cache_timestamp)}s ago"
        )
        return _cached_applications

    # Rescan applications
    try:
        import json

        from .scanner import scan_installed_applications

        logger.info("[AppUtils] Refreshing app cache")
        result_json = await scan_installed_applications(
            {"force_refresh": force_refresh}
        )
        result = json.loads(result_json)

        if result.get("success", False):
            _cached_applications = result.get("applications", [])
            _cache_timestamp = current_time
            logger.info(
                f"[AppUtils] App cache refreshed; found {len(_cached_applications)} apps"
            )
            return _cached_applications
        else:
            logger.warning(
                f"[AppUtils] App scan failed: {result.get('message', 'unknown error')}"
            )
            return _cached_applications or []

    except Exception as e:
        logger.error(f"[AppUtils] Failed to refresh app cache: {e}", exc_info=True)
        return _cached_applications or []


async def find_best_matching_app(
    app_name: str, app_type: str = "any"
) -> dict[str, Any] | None:
    """Find the best matching application.

    Args:
        app_name: application name
        app_type: application type filter ("installed", "running", "any")

    Returns:
        Best matching application info
    """
    try:
        if app_type == "running":
            # Get running applications
            import asyncio

            from .process_manager import list_running_applications as list_running_sync

            applications = await asyncio.to_thread(list_running_sync)
        else:
            # Get installed applications
            applications = await get_cached_applications()

        if not applications:
            return None

        # Compute match scores for all applications
        matches = []
        for app in applications:
            score = AppMatcher.match_application(app_name, app)
            if score > 0:
                matches.append((score, app))

        if not matches:
            return None

        # Sort by score and return the best match
        matches.sort(key=lambda x: x[0], reverse=True)
        best_score, best_app = matches[0]

        logger.info(
            f"[AppUtils] Best match found: {best_app.get('display_name', best_app.get('name', ''))} (score: {best_score})"
        )
        return best_app

    except Exception as e:
        logger.error(f"[AppUtils] Failed to find matching app: {e}", exc_info=True)
        return None


def clear_app_cache():
    """Clear the application cache."""
    global _cached_applications, _cache_timestamp

    _cached_applications = None
    _cache_timestamp = 0
    logger.info("[AppUtils] App cache cleared")


def get_system_scanner():
    """Get the corresponding scanner module for the current system."""
    system = platform.system()

    if system == "Darwin":
        from . import scanner_mac

        return scanner_mac
    elif system == "Windows":
        from . import scanner_windows

        return scanner_windows
    elif system == "Linux":
        from . import scanner_linux

        return scanner_linux
    else:
        logger.warning(f"[AppUtils] Unsupported system: {system}")
        return None


def get_cache_info() -> dict[str, Any]:
    """
    Get cache information.
    """

    current_time = time.time()
    cache_age = current_time - _cache_timestamp if _cache_timestamp > 0 else -1

    return {
        "cached": _cached_applications is not None,
        "count": len(_cached_applications) if _cached_applications else 0,
        "age_seconds": int(cache_age) if cache_age >= 0 else None,
        "valid": cache_age >= 0 and cache_age < _cache_duration,
        "cache_duration": _cache_duration,
    }
