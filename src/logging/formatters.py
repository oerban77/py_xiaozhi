"""Log formatter module.

Provide multiple log formatters:
- color console formatter
- JSON structured formatter
- simple text formatter
"""

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Optional, Union


def _empty_context() -> dict[str, Any]:
    return {}


class ColoredFormatter(logging.Formatter):
    """
    Color log formatter for console output.
    """

    # ANSI color codes
    COLORS = {
        "RESET": "\033[0m",
        "BOLD": "\033[1m",
        "DIM": "\033[2m",
        # foreground color
        "BLACK": "\033[30m",
        "RED": "\033[31m",
        "GREEN": "\033[32m",
        "YELLOW": "\033[33m",
        "BLUE": "\033[34m",
        "MAGENTA": "\033[35m",
        "CYAN": "\033[36m",
        "WHITE": "\033[37m",
        # highlight foreground color
        "BRIGHT_RED": "\033[91m",
        "BRIGHT_GREEN": "\033[92m",
        "BRIGHT_YELLOW": "\033[93m",
        "BRIGHT_BLUE": "\033[94m",
        "BRIGHT_MAGENTA": "\033[95m",
        "BRIGHT_CYAN": "\033[96m",
        "BRIGHT_WHITE": "\033[97m",
        # background color
        "BG_RED": "\033[41m",
        "BG_YELLOW": "\033[43m",
    }

    # Colors corresponding to log levels
    LEVEL_COLORS = {
        logging.DEBUG: "CYAN",
        logging.INFO: "GREEN",
        logging.WARNING: "YELLOW",
        logging.ERROR: "RED",
        logging.CRITICAL: "BRIGHT_WHITE",
    }

    # Background color for the corresponding log level (used for CRITICAL)
    LEVEL_BG_COLORS = {
        logging.CRITICAL: "BG_RED",
    }

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        style: str = "%",
        use_colors: bool = True,
        show_trace_id: bool = True,
        show_thread: bool = True,
    ) -> None:
        super().__init__(fmt, datefmt, style)
        self.use_colors = use_colors and self._supports_color()
        self.show_trace_id = show_trace_id
        self.show_thread = show_thread

    def _supports_color(self) -> bool:
        """
        Check whether the terminal supports color.
        """
        # Windows terminal check
        if sys.platform == "win32":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                return True
            except Exception:
                return False

        # Unix system check
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    def _colorize(self, text: str, color: str) -> str:
        """
        Add color to text.
        """
        if not self.use_colors:
            return text
        color_code = self.COLORS.get(color, "")
        reset = self.COLORS["RESET"]
        return f"{color_code}{text}{reset}"

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record.
        """
        # timestamp
        timestamp = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
        timestamp_colored = self._colorize(timestamp, "DIM")

        # Log level
        level_color = self.LEVEL_COLORS.get(record.levelno, "WHITE")
        bg_color = self.LEVEL_BG_COLORS.get(record.levelno)

        level_name = record.levelname.ljust(8)
        if bg_color and self.use_colors:
            level_colored = (
                f"{self.COLORS[bg_color]}{self.COLORS['BOLD']}"
                f"{level_name}{self.COLORS['RESET']}"
            )
        else:
            level_colored = self._colorize(level_name, level_color)

        # Logger name (shortened display)
        name = self._shorten_name(record.name)
        name_colored = self._colorize(f"[{name}]", "BLUE")

        # message
        message = record.getMessage()
        if record.levelno >= logging.ERROR:
            message = self._colorize(message, level_color)

        # Build the base log line
        parts = [timestamp_colored, level_colored, name_colored, message]

        # Add Trace ID
        if self.show_trace_id:
            trace_id = getattr(record, "trace_id", None) or _empty_context().get(
                "trace_id"
            )
            if trace_id and trace_id != "-":
                trace_colored = self._colorize(f"[{trace_id[:8]}]", "MAGENTA")
                parts.insert(3, trace_colored)

        # Add thread name
        if self.show_thread:
            thread_name = record.threadName
            if thread_name != "MainThread":
                thread_colored = self._colorize(f"({thread_name})", "DIM")
                parts.append(thread_colored)

        log_line = " ".join(parts)

        # Exception info
        if record.exc_info:
            exc_text = self._format_exception(record.exc_info)
            log_line = f"{log_line}\n{exc_text}"

        return log_line

    def _shorten_name(self, name: str, max_length: int = 25) -> str:
        """
        Shorten the logger name.
        """
        if len(name) <= max_length:
            return name

        parts = name.split(".")
        if len(parts) == 1:
            return name[:max_length]

        # Keep the last part and abbreviate the preceding part
        result = []
        for i, part in enumerate(parts[:-1]):
            if i == 0:
                result.append(part[:3])
            else:
                result.append(part[0])
        result.append(parts[-1])

        shortened = ".".join(result)
        if len(shortened) > max_length:
            return shortened[:max_length]
        return shortened

    def _format_exception(self, exc_info: tuple) -> str:
        """
        Format exception information.
        """
        lines = traceback.format_exception(*exc_info)
        if self.use_colors:
            return self._colorize("".join(lines), "RED")
        return "".join(lines)


class JsonFormatter(logging.Formatter):
    """
    JSON formatter for the log aggregation system.
    """

    def __init__(
        self,
        include_extra: bool = True,
        include_stack_trace: bool = True,
        timestamp_format: str = "iso",  # iso, unix, unix_ms
    ) -> None:
        super().__init__()
        self.include_extra = include_extra
        self.include_stack_trace = include_stack_trace
        self.timestamp_format = timestamp_format

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log records as JSON.
        """
        log_data: dict[str, Any] = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add context information
        context = _empty_context()
        if context:
            log_data["context"] = context

        # Read context straight from the record (if present)
        for field in ("trace_id", "request_id", "user_id", "session_id"):
            value = getattr(record, field, None)
            if value and value != "-":
                if "context" not in log_data:
                    log_data["context"] = {}
                log_data["context"][field] = value

        # Add thread info
        log_data["thread"] = {
            "id": record.thread,
            "name": record.threadName,
        }

        # Add process info
        log_data["process"] = {
            "id": record.process,
            "name": record.processName,
        }

        # Add exception info
        if record.exc_info and self.include_stack_trace:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "stack_trace": self._format_stack_trace(record.exc_info),
            }

        # Add additional fields
        if self.include_extra:
            extra = self._extract_extra(record)
            if extra:
                log_data["extra"] = extra

        return json.dumps(log_data, ensure_ascii=False, default=str)

    def _format_timestamp(self, created: float) -> Union[str, float]:
        """
        Format timestamps.
        """
        if self.timestamp_format == "unix":
            return created
        elif self.timestamp_format == "unix_ms":
            return int(created * 1000)
        else:  # iso
            dt = datetime.fromtimestamp(created, tz=timezone.utc)
            return dt.isoformat()

    def _format_stack_trace(self, exc_info: tuple) -> list[str]:
        """
        Format stack traces.
        """
        lines = traceback.format_exception(*exc_info)
        return [line.strip() for line in lines if line.strip()]

    def _extract_extra(self, record: logging.LogRecord) -> dict[str, Any]:
        """
        Extract extra fields.
        """
        # standard LogRecord fields
        standard_fields = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "exc_info",
            "exc_text",
            "thread",
            "threadName",
            "message",
            # Custom context fields
            "trace_id",
            "request_id",
            "user_id",
            "session_id",
            "extra_context",
        }

        extra = {}
        for key, value in record.__dict__.items():
            if key not in standard_fields and not key.startswith("_"):
                extra[key] = value

        return extra


class SimpleFormatter(logging.Formatter):
    """
    Simple text formatter for file output.
    """

    DEFAULT_FORMAT = (
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s | %(threadName)s"
    )
    DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        include_trace_id: bool = True,
    ) -> None:
        super().__init__(
            fmt or self.DEFAULT_FORMAT,
            datefmt or self.DEFAULT_DATE_FORMAT,
        )
        self.include_trace_id = include_trace_id

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record.
        """
        # Add trace_id
        if self.include_trace_id:
            trace_id = getattr(record, "trace_id", None) or _empty_context().get(
                "trace_id", "-"
            )
            record.trace_id = trace_id

        return super().format(record)
