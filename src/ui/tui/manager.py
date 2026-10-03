"""TUI ViewManager: a ViewPort implementation that drives the Textual App."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.core.event_bus import EventBus, Events
from src.logging import get_logger

if TYPE_CHECKING:
    from src.core.task_manager import TaskManager
    from src.ui.tui.app import XiaozhiTuiApp

logger = get_logger()


class TuiViewManager:
    """TUI interface (ViewPort: the same set_* methods as GUI/CLI/GPIO)."""

    def __init__(
        self,
        event_bus: EventBus,
        task_manager: TaskManager | None = None,
    ):
        self._event_bus = event_bus
        self._task_manager = task_manager
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app: XiaozhiTuiApp | None = None
        self._app_task: asyncio.Task | None = None

        self._auto_mode = False
        self._status = "Idle"
        self._connected = False
        self._chat_text = ""
        self._music_line = ""
        self._emotion = "neutral"

    async def start(self, mode: str = "tui"):
        """Start the TUI (awaits until the user quits or the task is cancelled)."""
        try:
            from src.ui.tui.app import XiaozhiTuiApp
        except ImportError as e:
            logger.error(
                "TUI mode requires textual. Please install:\n"
                "  uv sync --extra tui\n"
                "  pip install '.[tui]'\n"
                f"(original error: {e})"
            )
            raise

        logger.info("TuiViewManager: starting TUI interface...")
        self._running = True
        self._loop = asyncio.get_running_loop()

        self._app = XiaozhiTuiApp(
            on_command=self._handle_command,
            on_settings_saved=self._on_settings_saved,
        )
        self._app.status_text = self._status
        self._app.connected = self._connected
        self._app.auto_mode = self._auto_mode
        self._app.chat_text = self._chat_text
        self._app.music_line = self._music_line
        self._app.emotion = self._emotion

        # UIPlugin starts this coroutine via TaskManager.spawn; here we await run_async
        # until the user presses q/Ctrl+C or close() calls app.exit()
        try:
            await self._app.run_async()
        except asyncio.CancelledError:
            logger.info("TuiViewManager: TUI task cancelled")
            app = self._app
            if app is not None:
                try:
                    app.exit()
                except Exception:
                    pass
            raise
        finally:
            self._running = False
            if self._app is not None:
                try:
                    self._app.uninstall_log_handler()
                except Exception:
                    pass
            logger.info("TuiViewManager: TUI finished")

    async def close(self):
        """Close the TUI."""
        logger.info("TuiViewManager: shutting down...")
        self._running = False
        app = self._app
        if app is not None:
            try:
                app.exit()
            except Exception:
                pass
            try:
                app.uninstall_log_handler()
            except Exception:
                pass
        logger.info("TuiViewManager: closed")

    def _handle_command(self, cmd: str):
        """Handle user commands."""
        cmd_lower = cmd.lower().strip()
        if cmd_lower.startswith("/attachment "):
            from src.ui.shared.events import UISendAttachmentRequest

            path = cmd.strip()[len("/attachment ") :].strip()
            if path:
                from pathlib import Path

                from src.mcp.tools.documents.service import IMAGE_EXTENSIONS

                # Images are analyzed by the take_photo camera tool (vision),
                # not by the document tool (OCR).
                is_image = Path(path).suffix.lower() in IMAGE_EXTENSIONS
                self._safe_emit(
                    Events.UI_SEND_ATTACHMENT,
                    UISendAttachmentRequest(
                        path=path, use_document_tool=not is_image
                    ),
                )
            return
        command_parts = cmd.strip().split(maxsplit=1)
        if command_parts and command_parts[0].lower() == "/workspace":
            from src.utils.workspace import get_workspace_root, set_workspace

            if len(command_parts) == 1:
                message = f"Active workspace: {get_workspace_root()}"
            else:
                ok, result = set_workspace(command_parts[1])
                message = f"Workspace changed: {result}" if ok else result
            if self._app is not None:
                self._app.write_log(message)
            return
        if cmd_lower == "r":
            self._safe_emit(Events.UI_MANUAL_TOGGLE)
        elif cmd_lower == "x":
            self._safe_emit(Events.UI_ABORT_REQUEST)
        elif cmd_lower == "q":
            self._safe_emit(Events.UI_QUIT_REQUEST)
        else:
            self._safe_emit(Events.UI_SEND_TEXT, {"text": cmd})

    def _on_settings_saved(self, mcp_tools_changed: bool = False) -> None:
        """Notify runtime hot-reload after settings are saved."""
        self._safe_emit(Events.CONFIG_CHANGED)
        if mcp_tools_changed:
            self._safe_emit(Events.PROTOCOL_RECONNECT_REQUEST)

    def _safe_emit(self, event: str, data=None):
        """Safely emit EventBus events."""

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
                    f"TuiViewManager failed to dispatch event {event} via TaskManager: {e}",
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
                        f"TuiViewManager failed to emit event {event}: {exc}",
                        exc_info=exc,
                    )

            fut.add_done_callback(_done)
        except Exception as e:
            logger.error(f"TuiViewManager failed to dispatch event {event} failed: {e}", exc_info=True)
            if asyncio.iscoroutine(coro):
                coro.close()

    def _set_app_attr(self, name: str, value) -> None:
        app = self._app
        if app is None:
            return

        def _apply() -> None:
            setattr(app, name, value)

        # Schedule the update on the Textual event loop without blocking.
        # call_from_thread() blocks the calling thread on future.result(), which
        # can deadlock the app when a worker thread updates the UI while the
        # event loop is busy (e.g. during heavy RTL rendering or log floods).
        loop = getattr(app, "_loop", None)
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(_apply)
                return
            except Exception:
                pass
        try:
            _apply()
        except Exception as e:
            logger.debug(f"TUI set {name} failed: {e}")

    # ----- ViewPort -----

    @property
    def is_running(self) -> bool:
        return self._running

    def set_status(self, status: str, connected: bool = True):
        self._status = status
        self._connected = connected
        self._set_app_attr("status_text", status)
        self._set_app_attr("connected", connected)

    def set_chat_text(self, text: str):
        self._chat_text = text
        self._set_app_attr("chat_text", text)

    def set_music_line(self, text: str):
        self._music_line = text
        self._set_app_attr("music_line", text)

    def set_emotion(self, emotion: str):
        self._emotion = emotion
        self._set_app_attr("emotion", emotion)

    def set_auto_mode(self, auto_mode: bool):
        self._auto_mode = auto_mode
        self._set_app_attr("auto_mode", auto_mode)

    def set_button_text(self, text: str):
        logger.debug(f"TUI set_button_text: {text}")

    def is_auto_mode(self) -> bool:
        return bool(self._auto_mode)
