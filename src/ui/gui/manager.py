"""GUI ViewManager: composes the QmlAppHost / main interface / settings controller to implement ViewPort."""

import asyncio

from PySide6.QtCore import QObject, Slot

from src.core.event_bus import EventBus, Events
from src.core.task_manager import TaskManager
from src.logging import get_logger
from src.ui.gui.main_controller import MainWindowController
from src.ui.gui.qml_host import QmlAppHost
from src.ui.gui.services import TrayService
from src.ui.gui.settings_controller import SettingsController
from src.ui.shared.bridge import EventBridge

logger = get_logger()


class GuiViewManager(QObject):
    """GUI interface entry point (ViewPort + settings helpers).

    Device activation is done before the container starts by a separate GuiActivation window; the main interface no longer carries the activation Model/API.
    """

    def __init__(self, event_bus: EventBus, task_manager: TaskManager | None = None):
        super().__init__()
        self._event_bus = event_bus
        self._running = False

        self._owns_tasks = task_manager is None
        if task_manager is not None:
            self._tasks = task_manager
        else:
            self._tasks = TaskManager()
            self._tasks.initialize()

        self._bridge = EventBridge(event_bus, task_manager=self._tasks)
        self._host = QmlAppHost()
        self._main = MainWindowController()
        self._settings = SettingsController(event_bus, self._tasks, self._bridge)
        self._tray_service: TrayService | None = None
        self._volume_controller = None

        self._event_bus.on(Events.UI_TOGGLE_WINDOW, self._on_toggle_window)
        self._event_bus.on(Events.UI_MUTE_TOGGLE, self._on_mute_toggle)
        logger.debug("GuiViewManager: subscribed to window/mute toggle events")

    async def start(self, mode: str = "gui"):
        if mode == "cli":
            logger.info("GuiViewManager: CLI mode; skipping GUI init")
            return

        logger.info("GuiViewManager: starting GUI...")
        self._running = True

        self._host.create_engine()
        self._host.inject_context(
            {
                "eventBridge": self._bridge,
                "mainModel": self._main.main_model,
                "settingsModel": self._settings.ensure_model(),
                "emotionService": self._main.emotion_service,
            }
        )
        self._host.load_main()
        # Cold start: only show, do not steal the foreground, to avoid pushing aside the Space of another full-screen app on macOS
        self._host.show_root(activate=False)
        self._setup_tray()
        self._main.set_neutral_emotion()
        await self._refresh_muted()
        logger.info("GuiViewManager: GUI started")

    async def close(self):
        logger.info("GuiViewManager: shutting down...")
        self._running = False
        self._settings.close()
        if self._tray_service:
            self._tray_service.hide()
        self._host.shutdown()
        logger.info("GuiViewManager: closed")

    def _setup_tray(self) -> None:
        root = self._host.root_window()
        if root is None:
            return
        self._tray_service = TrayService(root)
        self._tray_service.setup(
            on_show=self._host.show_root,
            on_quit=self._request_quit,
        )

    def _request_quit(self) -> None:
        self._tasks.spawn(
            self._event_bus.emit(Events.UI_QUIT_REQUEST), name="ui:quit_request"
        )

    async def _on_toggle_window(self, data=None):
        logger.debug("GuiViewManager: window toggle event received")
        self.toggle_window()

    async def _on_mute_toggle(self, data=None):
        """Toggle the speaker mute and push the new state to the model.

        Runs on the asyncio loop; the blocking volume backend call is offloaded to a worker thread.
        """
        controller = self._get_volume_controller()
        if controller is None:
            logger.warning("GuiViewManager: volume controller unavailable; mute toggle ignored")
            return

        def _apply():
            muted = controller.get_muted()
            controller.set_muted(not muted)
            return controller.get_muted()

        try:
            new_muted = await asyncio.to_thread(_apply)
        except Exception as e:
            logger.warning(f"GuiViewManager: mute toggle failed: {e}", exc_info=True)
            return

        self._main.main_model.set_muted(bool(new_muted))
        logger.debug(f"GuiViewManager: mute toggled -> {new_muted}")

    async def _refresh_muted(self) -> None:
        """Read the current mute state and push it to the model (best effort)."""
        controller = self._get_volume_controller()
        if controller is None:
            return

        def _read():
            return controller.get_muted()

        try:
            muted = await asyncio.to_thread(_read)
            self._main.main_model.set_muted(bool(muted))
        except Exception as e:
            logger.warning(f"GuiViewManager: failed to read mute state: {e}", exc_info=True)

    def _get_volume_controller(self):
        """Lazily create the volume controller; returns None when unavailable."""
        if self._volume_controller is None:
            try:
                from src.mcp.tools.volume import create_volume_controller

                self._volume_controller = create_volume_controller()
            except Exception as e:
                logger.warning(f"GuiViewManager: volume controller init failed: {e}", exc_info=True)
                self._volume_controller = None
        return self._volume_controller

    # ----- ViewPort -----

    @property
    def is_running(self) -> bool:
        return self._running

    def set_chat_text(self, text: str) -> None:
        self._main.set_chat_text(text)

    def set_music_line(self, text: str) -> None:
        self._main.set_music_line(text)

    def set_emotion(self, emotion: str) -> None:
        self._main.set_emotion(emotion)

    def set_status(self, status: str, connected: bool = True) -> None:
        self._main.set_status(status, connected)

    def set_button_text(self, text: str) -> None:
        self._main.set_button_text(text)

    def set_auto_mode(self, auto_mode: bool) -> None:
        self._main.set_auto_mode(auto_mode)

    def is_auto_mode(self) -> bool:
        return self._main.is_auto_mode()

    # ----- Settings / window -----

    @property
    def main_model(self):
        return self._main.main_model

    @property
    def settings_model(self):
        return self._settings.settings_model

    @Slot()
    def toggle_mode(self):
        self._tasks.spawn(
            self._event_bus.emit(Events.UI_AUTO_TOGGLE), name="ui:auto_toggle"
        )

    @Slot()
    def toggle_window(self):
        self._host.toggle_root_visible()

    def open_settings(self):
        if not self._host.engine:
            logger.warning("GuiViewManager: engine not initialized; cannot open settings")
            return
        self._settings.open_settings()
