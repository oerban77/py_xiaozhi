import inspect
import logging
import sys
from pathlib import Path

from .filters import (
    DuplicateFilter,
    SensitiveDataFilter,
)
from .formatters import ColoredFormatter, JsonFormatter, SimpleFormatter
from .log_config import LoggingConfig, load_logging_config
from .log_handlers import (
    AsyncHandler,
    TimeSizeRotatingFileHandler,
)

# Module version
__version__ = "1.0.0"

# Whether the logging system is initialized
_initialized = False

# Exported public interface
__all__ = [
    # Main function
    "setup_logging",
    "get_logger",
    "shutdown_logging",
    # configuration
    "LoggingConfig",
    "load_logging_config",
    # filter
    "SensitiveDataFilter",
    "DuplicateFilter",
    # formatter
    "ColoredFormatter",
    "JsonFormatter",
    "SimpleFormatter",
    # handler
    "TimeSizeRotatingFileHandler",
    "AsyncHandler",
]


def setup_logging(
    level: str | None = None,
    log_dir: str | Path | None = None,
    enable_console: bool = True,
    enable_file: bool = True,
    enable_json: bool = False,
    enable_async: bool = False,
    enable_sensitive_filter: bool = True,
    config: LoggingConfig | None = None,
) -> Path | None:
    """Initialize the logging system.

    Args:
        level: Log level; defaults to environment-based settings.
        log_dir: log directory; defaults to the project root logs directory.
        enable_console: whether to enable console output.
        enable_file: whether to enable file output.
        enable_json: whether to enable JSON file output.
        enable_async: whether to enable async logging.
        enable_sensitive_filter: whether to enable sensitive-data filtering.
        config: custom config object.

    Returns:
        Log file path (if file output is enabled).
    """
    global _initialized

    # Get or create the config (no LoggingConfigManager singleton)
    if config is None:
        config = load_logging_config()

    # Application parameter overrides
    if level:
        config.level = level.upper()
    if log_dir:
        config.log_dir = Path(log_dir)
    config.enable_console = enable_console
    config.enable_file = enable_file
    config.enable_json_file = enable_json
    config.enable_async = enable_async
    config.enable_sensitive_filter = enable_sensitive_filter

    # Ensure the log directory exists
    if config.log_dir:
        config.log_dir.mkdir(parents=True, exist_ok=True)

    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.level, logging.INFO))

    # get_logger() may have installed temporary per-module console handlers before
    # setup_logging() ran. Remove those now that the configured handlers are ready.
    for logger_obj in logging.Logger.manager.loggerDict.values():
        if not isinstance(logger_obj, logging.Logger):
            continue
        for handler in list(logger_obj.handlers):
            if getattr(handler, "_xiaozhi_bootstrap_handler", False):
                logger_obj.removeHandler(handler)
                handler.close()
        if hasattr(logger_obj, "_xiaozhi_bootstrap_level"):
            logger_obj.setLevel(logger_obj._xiaozhi_bootstrap_level)
            del logger_obj._xiaozhi_bootstrap_level

    # Clear existing handlers
    if root_logger.handlers:
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)

    def _make_filters() -> list[logging.Filter]:
        """Each handler needs its own filter instance to avoid shared state losing logs."""
        result: list[logging.Filter] = [DuplicateFilter(suppress_seconds=3.0)]
        if config.enable_sensitive_filter:
            result.append(SensitiveDataFilter(patterns=config.sensitive_patterns))
        return result

    # Create the handler list
    handlers: list[logging.Handler] = []

    # Console handler
    if config.enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, config.level, logging.INFO))
        console_handler.setFormatter(
            ColoredFormatter(
                use_colors=True,
                show_trace_id=True,
                show_thread=True,
            )
        )
        for f in _make_filters():
            console_handler.addFilter(f)
        handlers.append(console_handler)

    # File handler
    log_file = None
    if config.enable_file and config.log_dir:
        log_file = config.log_dir / config.log_file

        file_handler = TimeSizeRotatingFileHandler(
            log_file,
            when=config.rotation_when,
            interval=config.rotation_interval,
            max_bytes=config.max_bytes,
            backup_count=config.backup_count,
            compress=True,
        )
        file_handler.setLevel(getattr(logging, config.level, logging.INFO))
        file_handler.setFormatter(
            SimpleFormatter(
                fmt="%(asctime)s | %(levelname)-8s | [%(trace_id)s] | %(name)s | %(message)s | %(threadName)s",
                include_trace_id=True,
            )
        )
        for f in _make_filters():
            file_handler.addFilter(f)
        handlers.append(file_handler)

    # Error log file handler
    if config.enable_error_file and config.log_dir:
        error_file = config.log_dir / config.error_log_file

        error_handler = TimeSizeRotatingFileHandler(
            error_file,
            when=config.rotation_when,
            interval=config.rotation_interval,
            max_bytes=config.max_bytes,
            backup_count=config.backup_count,
            compress=True,
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(
            SimpleFormatter(
                fmt="%(asctime)s | %(levelname)-8s | [%(trace_id)s] | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
                include_trace_id=True,
            )
        )
        for f in _make_filters():
            error_handler.addFilter(f)
        handlers.append(error_handler)

    # JSON file handler
    if config.enable_json_file and config.log_dir:
        json_file = config.log_dir / "app.json.log"

        json_handler = TimeSizeRotatingFileHandler(
            json_file,
            when=config.rotation_when,
            interval=config.rotation_interval,
            max_bytes=config.max_bytes,
            backup_count=config.backup_count,
            compress=True,
        )
        json_handler.setLevel(getattr(logging, config.level, logging.INFO))
        json_handler.setFormatter(JsonFormatter())
        for f in _make_filters():
            json_handler.addFilter(f)
        handlers.append(json_handler)

    # Wrap with async handler
    if config.enable_async and handlers:
        async_handler = AsyncHandler(handlers)
        root_logger.addHandler(async_handler)
    else:
        for handler in handlers:
            root_logger.addHandler(handler)

    # Set the log level for third-party libraries
    for module_name, level_str in config.third_party_levels.items():
        logging.getLogger(module_name).setLevel(
            getattr(logging, level_str, logging.WARNING)
        )

    _initialized = True

    # Record that the logging system is initialized
    logger = logging.getLogger(__name__)
    logger.info("Logging system initialized")
    if log_file:
        logger.debug(f"Log file: {log_file}")

    return log_file


def get_logger(name: str | None = None) -> logging.Logger:
    """Get the configured logger.

    Args:
        name: Logger name. If omitted, the caller's module name is used automatically.

    Returns:
        Configured logger

    Example:
        logger = get_logger()  # Module name injected automatically
        logger = get_logger("custom.name")  # Custom name
    """
    if name is None:
        # Automatically obtain the caller's module name
        frame = inspect.currentframe()
        if frame and frame.f_back:
            name = frame.f_back.f_globals.get("__name__", "__main__")
        else:
            name = "__main__"

    logger = logging.getLogger(name)

    # If the logging system is not initialized, add a basic console handler
    if not _initialized and not logger.handlers and not logging.root.handlers:
        handler = logging.StreamHandler()
        handler._xiaozhi_bootstrap_handler = True
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)
        logger._xiaozhi_bootstrap_level = logger.level
        logger.setLevel(logging.DEBUG)

    return logger


def shutdown_logging() -> None:
    """Shut down the logging system and clean up resources.

    Call this when the application exits to ensure all logs are written.
    """
    global _initialized

    logging.shutdown()
    _initialized = False
