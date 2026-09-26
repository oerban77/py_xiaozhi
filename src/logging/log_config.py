"""Logging configuration module.

Provides the logging system's configuration dataclass and loading functions
(no singleton get_instance).
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class Environment(Enum):
    """Runtime environment enum."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


@dataclass
class LoggingConfig:
    """Logging configuration dataclass."""

    level: str = "INFO"
    format_type: str = "colored"  # colored, json, simple

    log_dir: Optional[Path] = None
    log_file: str = "app.log"
    error_log_file: str = "error.log"

    max_bytes: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 30
    rotation_when: str = "midnight"  # midnight, H, D, W0-W6
    rotation_interval: int = 1

    enable_console: bool = True
    enable_file: bool = True
    enable_error_file: bool = True
    enable_json_file: bool = False
    enable_async: bool = False
    enable_sensitive_filter: bool = True

    sensitive_patterns: list[str] = field(
        default_factory=lambda: [
            "password",
            "passwd",
            "secret",
            "token",
            "api_key",
            "apikey",
            "access_token",
            "refresh_token",
            "authorization",
            "credential",
            "private_key",
        ]
    )

    third_party_levels: dict[str, str] = field(
        default_factory=lambda: {
            "urllib3": "WARNING",
            "websockets": "WARNING",
            "asyncio": "WARNING",
            "paho": "WARNING",
            "PIL": "WARNING",
            "matplotlib": "WARNING",
            # qasync and the internal event bus emit DEBUG on every event loop iteration
            # with large repr() payloads. They are written synchronously from the thread
            # that needs the GIL, so this flood starves the real-time audio callback and
            # causes TTS underruns. Keep them quiet regardless of the root log level.
            "qasync": "WARNING",
            "src.core.event_bus": "WARNING",
        }
    )


def _get_environment() -> Environment:
    env_str = os.environ.get("APP_ENV", "development").lower()
    env_map = {
        "dev": Environment.DEVELOPMENT,
        "development": Environment.DEVELOPMENT,
        "test": Environment.TESTING,
        "testing": Environment.TESTING,
        "prod": Environment.PRODUCTION,
        "production": Environment.PRODUCTION,
    }
    return env_map.get(env_str, Environment.DEVELOPMENT)


def _get_default_log_dir() -> Path:
    try:
        from src.utils.resource_finder import get_log_dir

        return get_log_dir()
    except ImportError:
        return Path.cwd() / "logs"


def load_logging_config(app_config: Any | None = None) -> LoggingConfig:
    """Load logging configuration (a pure function, no singleton).

    Args:
        app_config: optional ConfigManager instance; when omitted, the LOGGING section is
            read if initialize_config has already run.
    """
    config = LoggingConfig()

    try:
        if app_config is None:
            from src.utils.config_manager import get_config

            try:
                app_config = get_config()
            except RuntimeError:
                app_config = None

        if app_config is not None:
            logging_cfg = app_config.get_config("LOGGING", {}) or {}
            if logging_cfg:
                config.level = logging_cfg.get("LEVEL", config.level)
                config.format_type = logging_cfg.get("FORMAT_TYPE", config.format_type)
                config.enable_console = logging_cfg.get(
                    "ENABLE_CONSOLE", config.enable_console
                )
                config.enable_file = logging_cfg.get("ENABLE_FILE", config.enable_file)
                config.enable_error_file = logging_cfg.get(
                    "ENABLE_ERROR_FILE", config.enable_error_file
                )
                config.enable_json_file = logging_cfg.get(
                    "ENABLE_JSON_FILE", config.enable_json_file
                )
                config.enable_async = logging_cfg.get(
                    "ENABLE_ASYNC", config.enable_async
                )
                config.enable_sensitive_filter = logging_cfg.get(
                    "ENABLE_SENSITIVE_FILTER", config.enable_sensitive_filter
                )
                config.max_bytes = logging_cfg.get("MAX_BYTES", config.max_bytes)
                config.backup_count = logging_cfg.get(
                    "BACKUP_COUNT", config.backup_count
                )
                config.rotation_when = logging_cfg.get(
                    "ROTATION_WHEN", config.rotation_when
                )
                third_party = logging_cfg.get("THIRD_PARTY_LEVELS")
                if third_party:
                    config.third_party_levels.update(third_party)
    except Exception:
        # Use defaults when the configuration is not ready
        pass

    env_level = os.environ.get("LOG_LEVEL")
    if env_level:
        config.level = env_level.upper()
    config.format_type = os.environ.get("LOG_FORMAT", config.format_type)

    env = _get_environment()
    if not env_level:
        if env == Environment.DEVELOPMENT:
            # Only fall back to DEBUG when the user has not configured a level at all.
            # A level set in config.json is an explicit choice and must win over the
            # environment default; forcing DEBUG here flooded the log with qasync /
            # event-bus records written synchronously, starving the audio thread.
            if not config.level:
                config.level = "DEBUG"
        elif env == Environment.PRODUCTION:
            config.level = "INFO"

    if env == Environment.PRODUCTION:
        config.enable_json_file = True
        config.enable_async = True

    if config.log_dir is None:
        config.log_dir = _get_default_log_dir()

    return config
