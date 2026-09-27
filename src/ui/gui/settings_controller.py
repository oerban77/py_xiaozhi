"""Settings window: lazy loading of the SettingsModel and opening the settings."""

from src.core.event_bus import EventBus, Events
from src.core.task_manager import TaskManager
from src.logging import get_logger
from src.ui.shared.bridge import EventBridge

logger = get_logger()


class SettingsController:
    """Lazily creates the SettingsModel; reloads and notifies QML when the settings are opened."""

    def __init__(
        self,
        event_bus: EventBus,
        tasks: TaskManager,
        bridge: EventBridge,
    ) -> None:
        self._event_bus = event_bus
        self._tasks = tasks
        self._bridge = bridge
        self._settings_model = None

    @property
    def settings_model(self):
        return self.ensure_model()

    def ensure_model(self):
        """Lazily create the SettingsModel (on first QML injection / when the settings are opened)."""
        if self._settings_model is None:
            from src.ui.gui.models import SettingsModel

            self._settings_model = SettingsModel(
                event_bus=self._event_bus,
                task_manager=self._tasks,
            )
            self._settings_model.configSaved.connect(self._on_config_saved)
            self._settings_model.mcpToolsNeedReconnect.connect(
                self._on_mcp_tools_need_reconnect
            )
            logger.debug("SettingsController: SettingsModel lazily loaded")
        return self._settings_model

    def _on_config_saved(self) -> None:
        logger.info("SettingsController: config saved; triggering hot reload")
        self._tasks.spawn(
            self._event_bus.emit(Events.CONFIG_CHANGED), name="ui:config_changed"
        )

    def _on_mcp_tools_need_reconnect(self) -> None:
        """The MCP tool blocklist changed: request the session layer to disconnect and reconnect to refresh the server-side tools/list."""
        logger.info("SettingsController: MCP tool list changed; requesting protocol reconnect")
        self._tasks.spawn(
            self._event_bus.emit(Events.PROTOCOL_RECONNECT_REQUEST),
            name="ui:protocol_reconnect",
        )

    def open_settings(self) -> None:
        """Reload the config/device lists, then have QML show the settings window."""
        self.ensure_model().reload()
        self._bridge.showSettingsWindow.emit()
        logger.debug("SettingsController: open-settings signal sent")

    def close(self) -> None:
        """Stop settings background work before the GUI engine is destroyed."""
        if self._settings_model is not None:
            self._settings_model.stopMqttBrokerScan()
