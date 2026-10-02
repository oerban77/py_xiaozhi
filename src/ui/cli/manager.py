# -*- coding: utf-8 -*-
"""CLI terminal interface."""

import asyncio
from typing import TYPE_CHECKING, Optional

from src.core.event_bus import EventBus, Events
from src.logging import get_logger

from .display import CLIDisplay

if TYPE_CHECKING:
    from src.core.task_manager import TaskManager

logger = get_logger()


class CliViewManager:
    """CLI interface (ViewPort: the same set_* methods as GUI/GPIO)."""

    def __init__(
        self,
        event_bus: EventBus,
        task_manager: Optional["TaskManager"] = None,
    ):
        self._event_bus = event_bus
        self._task_manager = task_manager
        self._display = CLIDisplay()
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # state
        self._auto_mode = False
        self._status = "Idle"
        self._connected = False
        self._chat_text = ""
        self._music_line = ""

        # Set command callback
        self._display.set_command_callback(self._handle_command)

        # Intercept log output as early as possible
        self._display.intercept_logging()

    async def start(self, mode: str = "cli"):
        """Start the CLI view.

        Args:
            mode: the runtime mode (ignored in CLI mode)
        """
        logger.info("CliViewManager: starting CLI interface...")
        self._running = True
        self._loop = asyncio.get_running_loop()

        # Start CLI display
        try:
            await self._display.start()
        except asyncio.CancelledError:
            logger.info("CliViewManager: display task cancelled")

    async def close(self):
        """Close the CLI view."""
        logger.info("CliViewManager: shutting down...")
        self._running = False
        await self._display.close()
        logger.info("CliViewManager: closed")

    def _handle_command(self, cmd: str):
        """Handle user commands - unified entry point."""
        cmd_lower = cmd.lower()

        command_parts = cmd.strip().split(maxsplit=1)
        if command_parts and command_parts[0].lower() == "/workspace":
            from src.utils.workspace import get_workspace_root, set_workspace

            if len(command_parts) == 1:
                message = f"Active workspace: {get_workspace_root()}"
            else:
                ok, result = set_workspace(command_parts[1])
                message = f"Workspace changed: {result}" if ok else result
            self._display.update_text(message)
            return

        if cmd_lower == "r":
            # start/stop conversation
            self._safe_emit(Events.UI_MANUAL_TOGGLE)
        elif cmd_lower == "x":
            # interrupt
            self._safe_emit(Events.UI_ABORT_REQUEST)
        elif cmd_lower == "q":
            # Quit
            self._safe_emit(Events.UI_QUIT_REQUEST)
        elif cmd_lower == "h":
            # Show help
            self._display.show_help()
        else:
            # Send text
            self._safe_emit(Events.UI_SEND_TEXT, {"text": cmd})

    def _safe_emit(self, event: str, data=None):
        """Safely emit an event.

        Prefer TaskManager.schedule_nowait (thread-safe + traceable);
        otherwise fall back to run_coroutine_threadsafe.
        """
        def _start_emit():
            if data is None:
                return self._event_bus.emit(event)
            return self._event_bus.emit(event, data)

        if self._task_manager is not None:
            try:
                self._task_manager.schedule_nowait(_start_emit)
                return
            except Exception as e:
                logger.error(
                    f"CliViewManager failed to dispatch event {event} via TaskManager: {e}",
                    exc_info=True,
                )

        if not self._loop or not self._loop.is_running():
            return
        coro = _start_emit()
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)

            def _done(f):
                try:
                    exc = f.exception()
                except Exception:
                    return
                if exc:
                    logger.error(
                        f"CliViewManager failed to emit event {event}: {exc}",
                        exc_info=exc,
                    )

            fut.add_done_callback(_done)
        except Exception as e:
            logger.error(f"CliViewManager failed to dispatch event {event} failed: {e}", exc_info=True)
            if asyncio.iscoroutine(coro):
                coro.close()

    # ========== Public API ============

    @property
    def is_running(self) -> bool:
        """Whether it is running."""
        return self._running

    def set_status(self, status: str, connected: bool = True):
        """settingsstate."""
        self._status = status
        self._connected = connected
        self._display.update_status(status, connected)

    def set_chat_text(self, text: str):
        self._chat_text = text
        self._display.update_text(text)

    def set_music_line(self, text: str):
        self._music_line = text
        self._display.update_music_line(text)

    def set_emotion(self, emotion: str):
        self._display.update_emotion(emotion)

    def set_auto_mode(self, auto_mode: bool):
        self._auto_mode = auto_mode
        self._display.update_auto_mode(auto_mode)

    def set_button_text(self, text: str):
        # The terminal has no main button
        logger.debug(f"CLI set_button_text: {text}")

    def is_auto_mode(self) -> bool:
        return bool(self._auto_mode)
