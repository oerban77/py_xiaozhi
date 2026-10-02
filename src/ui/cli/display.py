"""CLI terminal display interface.

Provides a terminal TUI with:
- status dashboard (top frame)
- log display area
- command input area
"""

import asyncio
import logging
import os
import shutil
import sys
from collections import deque
from typing import Callable, Optional

from src.constants.system import SystemConstants
from src.logging import get_logger

logger = get_logger()


class CLIDisplay:
    """CLI terminal display interface."""

    def __init__(self):
        self.running = True
        self._use_ansi = sys.stdout.isatty()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_drawn_rows = 0
        self._render_lock = None
        self._initialized = False  # whether initialized
        self._log_handler_installed = False  # whether the log handler is installed

        # Dashboard data
        self._dash_status = "Idle"
        self._dash_connected = False
        self._dash_text = ""
        self._dash_music = ""
        self._dash_emotion = "neutral"
        self._dash_auto_mode = False

        # Layout settings
        self._input_area_lines = 3  # input area row count
        self._dashboard_lines = 9  # minimum number of rows in the display area

        # ANSI styles
        self._ansi = {
            "reset": "\x1b[0m",
            "bold": "\x1b[1m",
            "dim": "\x1b[2m",
            "blue": "\x1b[34m",
            "cyan": "\x1b[36m",
            "green": "\x1b[32m",
            "yellow": "\x1b[33m",
            "magenta": "\x1b[35m",
            "red": "\x1b[31m",
        }

        # Callback function
        self._on_command: Optional[Callable[[str], None]] = None

        # Log buffer
        self._log_lines: deque[str] = deque(maxlen=6)

        # Command queue
        self._command_queue: asyncio.Queue = asyncio.Queue()

    def set_command_callback(self, callback: Callable[[str], None]):
        """Set the command callback."""
        self._on_command = callback

    def intercept_logging(self):
        """Intercept log output as early as possible (call before start).

        Mimics the old implementation: remove StreamHandler during __init__ and install a custom handler.
        """
        # Remove all StreamHandlers first
        self._remove_stream_handlers()
        # Reinstall our log handler
        self._install_log_handler()

    async def start(self):
        """Start the CLI display."""
        # Get the event loop first
        self._loop = asyncio.get_running_loop()
        self._render_lock = asyncio.Lock()

        # Ensure logging has been intercepted (if intercept_logging was not already called)
        if not self._log_handler_installed:
            self.intercept_logging()

        # Clear the screen and initialize the interface
        if self._use_ansi:
            # Full clear: clear screen + scrollback + move cursor to top-left
            sys.stdout.write("\x1b[3J\x1b[2J\x1b[H")
            sys.stdout.flush()

        # Mark as initialized
        self._initialized = True

        # Initialize screen display
        await self._init_screen()

        # Start the input loop
        try:
            await self._keyboard_input_loop()
        except asyncio.CancelledError:
            pass

    async def close(self):
        """Close the CLI display."""
        self.running = False

        # Restore standard logging
        self._restore_logging()

        # Clear the screen
        if self._use_ansi:
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.flush()

        print("The application is shutting down...\n")

    # ========== Status updates ==========

    def update_status(self, status: str, connected: bool = True):
        """Update status."""
        self._dash_status = status
        self._dash_connected = connected
        self._schedule_render()

    def update_text(self, text: str):
        if text and text.strip():
            self._dash_text = text.strip()
            self._schedule_render()

    def update_music_line(self, text: str):
        self._dash_music = (text or "").strip()
        self._schedule_render()

    def update_emotion(self, emotion: str):
        """Update the emotion."""
        self._dash_emotion = emotion
        self._schedule_render()

    def update_auto_mode(self, auto_mode: bool):
        """Update auto-mode state."""
        self._dash_auto_mode = auto_mode
        self._schedule_render()

    def add_log(self, message: str):
        """Add log."""
        self._log_lines.append(message)
        self._schedule_render()

    def _schedule_render(self):
        """Schedule render."""
        if not self._initialized:
            return
        if self._loop and self._use_ansi and self.running:
            try:
                if self._loop.is_running():
                    self._loop.call_soon_threadsafe(self._do_render)
            except Exception as e:
                logging.getLogger(__name__).error(f"Render scheduling failed: {e}")

    def _do_render(self):
        """Execute rendering (called in the event loop)."""
        if not self._initialized:
            return
        try:
            task = asyncio.create_task(self._safe_render(), name="cli:render")

            def _on_done(t: asyncio.Task):
                if t.cancelled():
                    return
                exc = t.exception()
                if exc:
                    logging.getLogger(__name__).error(
                        f"CLI render task exception: {exc}", exc_info=exc
                    )

            task.add_done_callback(_on_done)
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to create render task: {e}", exc_info=True)

    async def _safe_render(self):
        """Safe render (with lock)."""
        if self._render_lock is None:
            return
        async with self._render_lock:
            await self._render_dashboard()

    # ========== Screen rendering ============

    async def _init_screen(self):
        """Initialize the screen."""
        # Note: screen clearing is already handled in start()
        await self._render_dashboard(full=True)
        await self._render_input_area()

    def build_dashboard_lines(self, width: int = 78) -> list[str]:
        """Build a compact, modern dashboard layout for the CLI status panel."""

        def trunc(s: str, limit: int = 28) -> str:
            if s is None:
                return "—"
            s = str(s).strip()
            return s if len(s) <= limit else s[: limit - 1] + "…"

        def status_badge(status: str) -> str:
            status = (status or "Idle").strip()
            spinner = ["◐", "◓", "◑", "◒"]
            if "Speaking" in status:
                return f"{spinner[self._status_anim_index % len(spinner)]} {status}"
            if "Listening" in status:
                return f"{spinner[self._status_anim_index % len(spinner)]} {status}"
            if "Idle" in status:
                return f"● {status}"
            if "Disconnected" in status.lower():
                return f"○ {status}"
            return f"● {status}"

        self._status_anim_index = getattr(self, "_status_anim_index", 0) + 1
        mode_text = "Auto" if self._dash_auto_mode else "Manual"
        conn_text = "Connected" if self._dash_connected else "Disconnected"
        inner_width = max(18, min(max(18, width - 2), 78))
        title = f" {SystemConstants.APP_DISPLAY_NAME} "
        status_text = status_badge(self._dash_status)
        conversation_limit = max(1, inner_width - 15)
        conversation_text = trunc(self._dash_text, conversation_limit) if self._dash_text else "—"

        def content_row(text: str) -> str:
            content_width = inner_width - 2
            content = text[:content_width].ljust(content_width)
            return f"│ {content} │"

        lines = [
            "╭" + "─" * inner_width + "╮",
            "│" + title.center(inner_width) + "│",
            "├" + "─" * inner_width + "┤",
            content_row(f"Status: {status_text}"),
            content_row(f"Connection: {conn_text} | Mode: {mode_text}"),
            content_row(f"Emotion: {trunc(self._dash_emotion)}"),
            content_row(f"Conversation: {conversation_text}"),
            content_row(f"Music: {trunc(self._dash_music) if self._dash_music else '—'}"),
            "╰" + "─" * inner_width + "╯",
        ]

        return lines

    async def _render_dashboard(self, full: bool = False):
        """Render the dashboard."""

        if not self._use_ansi:
            mode_text = "Auto" if self._dash_auto_mode else "Manual"
            conn_text = "Connected" if self._dash_connected else "Disconnected"
            print(
                f"\rStatus: {self._dash_status} | {conn_text} | {mode_text} | Emotion: {self._dash_emotion}     ",
                end="",
                flush=True,
            )
            return

        cols, rows = self._term_size()
        usable_rows = max(5, rows - self._input_area_lines)
        display_width = max(20, min(cols, 82))
        body = self.build_dashboard_lines(width=display_width)
        body_rows = len(body)

        def style(s: str, *names: str) -> str:
            if not self._use_ansi:
                return s
            prefix = "".join(self._ansi.get(n, "") for n in names)
            return f"{prefix}{s}{self._ansi['reset']}"

        styled_lines = []
        for index, line in enumerate(body):
            plain_line = line[: max(1, min(len(line), cols))]
            if index in (0, 1, 2, len(body) - 1):
                styled_lines.append(style(plain_line, "cyan"))
            elif index == 3:
                styled_lines.append(style(plain_line, "green"))
            elif index == 4:
                styled_lines.append(style(plain_line, "magenta"))
            elif index in (5, 6, 7):
                styled_lines.append(style(plain_line, "yellow"))
            else:
                styled_lines.append(plain_line)

        # Save the cursor
        sys.stdout.write("\x1b7")

        total_rows = body_rows
        rows_to_clear = max(self._last_drawn_rows, total_rows)
        for i in range(rows_to_clear):
            self._goto(1 + i, 1)
            sys.stdout.write("\x1b[2K")

        for idx, line in enumerate(styled_lines):
            self._goto(idx + 1, 1)
            sys.stdout.write("\x1b[2K")
            sys.stdout.write(line[: max(2, min(len(line), cols))])

        self._goto(total_rows + 1, 1)
        sys.stdout.write("\x1b[2K")
        sys.stdout.write("\x1b8")
        sys.stdout.flush()

        self._last_drawn_rows = total_rows

    async def _render_input_area(self):
        """Render the input area."""
        if not self._use_ansi:
            return

        cols, rows = self._term_size()
        separator_row = max(1, rows - self._input_area_lines + 1)
        first_input_row = min(rows, separator_row + 1)
        second_input_row = min(rows, separator_row + 2)

        sys.stdout.write("\x1b7")

        # Separator line
        self._goto(separator_row, 1)
        sys.stdout.write("\x1b[2K")
        sys.stdout.write("═" * max(1, cols))

        # Input prompt
        self._goto(first_input_row, 1)
        sys.stdout.write("\x1b[2K")
        prompt = "\x1b[1m\x1b[36mInput:\x1b[0m " if self._use_ansi else "Input: "
        sys.stdout.write(prompt)

        # Reserved line
        self._goto(second_input_row, 1)
        sys.stdout.write("\x1b[2K")
        sys.stdout.flush()

        sys.stdout.write("\x1b8")
        self._goto(first_input_row, 1)
        sys.stdout.write(prompt)
        sys.stdout.flush()

    def _clear_input_area(self):
        """Clear the input area."""
        if not self._use_ansi:
            return
        cols, rows = self._term_size()
        separator_row = max(1, rows - self._input_area_lines + 1)
        for r in range(separator_row, min(rows + 1, separator_row + 3)):
            self._goto(r, 1)
            sys.stdout.write("\x1b[2K")
        sys.stdout.flush()

    # ========== Input handling ============

    async def _keyboard_input_loop(self):
        """Keyboard input loop."""
        try:
            while self.running:
                if self._use_ansi:
                    await self._render_input_area()
                    cmd = await asyncio.to_thread(self._read_line_raw)
                    self._clear_input_area()
                    await self._render_dashboard()
                else:
                    cmd = await asyncio.to_thread(input, "Input: ")

                await self._handle_command(cmd.strip())
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            await self.close()

    def _read_line_raw(self) -> str:
        """Read input in raw mode (supports CJK)."""
        try:
            import termios
            import tty
        except ImportError:
            # Windows does not support termios
            return input()

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            buffer: list[str] = []
            while True:
                ch = os.read(fd, 4)
                if not ch:
                    break
                try:
                    s = ch.decode("utf-8")
                except UnicodeDecodeError:
                    while True:
                        ch += os.read(fd, 1)
                        try:
                            s = ch.decode("utf-8")
                            break
                        except UnicodeDecodeError:
                            continue

                if s in ("\r", "\n"):
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    break
                elif s in ("\x7f", "\b"):
                    if buffer:
                        buffer.pop()
                    self._redraw_input_line("".join(buffer))
                elif s == "\x03":  # Ctrl+C
                    raise KeyboardInterrupt
                else:
                    buffer.append(s)
                    self._redraw_input_line("".join(buffer))

            return "".join(buffer)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _redraw_input_line(self, content: str):
        """Redraw the input line."""
        cols, rows = self._term_size()
        separator_row = max(1, rows - self._input_area_lines + 1)
        first_input_row = min(rows, separator_row + 1)
        prompt = "\x1b[1m\x1b[36mInput:\x1b[0m " if self._use_ansi else "Input: "
        self._goto(first_input_row, 1)
        sys.stdout.write("\x1b[2K")
        visible = content
        max_len = max(1, cols - len("Input: ") - 1)
        if len(visible) > max_len:
            visible = visible[-max_len:]
        sys.stdout.write(f"{prompt}{visible}")
        sys.stdout.flush()

    async def _handle_command(self, cmd: str):
        """Handle a command - everything is forwarded to CliViewManager."""
        if not cmd:
            return

        if self._on_command:
            # All commands are forwarded, none are intercepted
            self._on_command(cmd)

    def show_help(self):
        """Show help."""
        self._dash_text = "Commands: /workspace [path] | r=Start/Stop | x=Interrupt | q=Quit | h=Help"
        self._schedule_render()

    # ========== Log handling ============

    def _install_log_handler(self):
        """Install the log handler."""
        # Prevent duplicate installation
        if self._log_handler_installed:
            return
        self._log_handler_installed = True

        class DisplayLogHandler(logging.Handler):
            def __init__(self, display: "CLIDisplay"):
                super().__init__()
                self.display = display

            def emit(self, record: logging.LogRecord):
                try:
                    msg = self.format(record)
                    self.display._log_lines.append(msg)
                    self.display._schedule_render()
                except Exception as e:
                    logging.getLogger(__name__).error(f"Log recording failed: {e}")

        handler = DisplayLogHandler(self)
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logging.getLogger().addHandler(handler)

    def _remove_stream_handlers(self):
        """Remove all stdout log handlers."""
        root = logging.getLogger()

        # Remove all StreamHandlers from the root logger
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler):
                root.removeHandler(h)

        # Remove the StreamHandlers from all registered loggers
        for name in list(logging.Logger.manager.loggerDict.keys()):
            log = logging.getLogger(name)
            for h in list(log.handlers):
                if isinstance(h, logging.StreamHandler):
                    log.removeHandler(h)

        # Set the root logger level
        root.setLevel(logging.DEBUG)

    def _restore_logging(self):
        """Restore standard logging."""
        root = logging.getLogger()

        # Remove the DisplayLogHandler
        for h in list(root.handlers):
            if h.__class__.__name__ == "DisplayLogHandler":
                root.removeHandler(h)

        # Add a simple StreamHandler
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        root.addHandler(handler)

    # ========== Utility functions ============

    def _goto(self, row: int, col: int = 1):
        """Move the cursor."""
        sys.stdout.write(f"\x1b[{max(1, row)};{max(1, col)}H")

    def _term_size(self) -> tuple[int, int]:
        """Get the terminal size."""
        try:
            size = shutil.get_terminal_size(fallback=(80, 24))
            return size.columns, size.lines
        except Exception:
            return 80, 24
