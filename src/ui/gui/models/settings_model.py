"""Settings window ViewModel.

Split by feature into several mixins under settings/; this file composes them and declares the QML Properties.
Externally it is still SettingsModel, and the QML context name settingsModel is unchanged.
"""

import json
import os
import threading
from typing import Any

from PySide6.QtCore import Property, Signal, Slot

from src.logging import get_logger
from src.ui.gui.models.base_model import BaseModel
from src.ui.gui.models.settings.audio_devices import SettingsAudioDevicesMixin
from src.ui.gui.models.settings.camera_devices import SettingsCameraDevicesMixin
from src.ui.gui.models.settings.camera_options import SettingsCameraOptionsMixin
from src.ui.gui.models.settings.mcp_tools import SettingsMcpToolsMixin
from src.ui.gui.models.settings.shortcuts import SettingsShortcutsMixin
from src.ui.gui.models.settings.smarthome import SettingsSmartHomeMixin
from src.ui.gui.models.settings.system_options import SettingsSystemOptionsMixin
from src.ui.gui.models.settings.wake_word import SettingsWakeWordMixin
from src.utils.config_manager import get_config
from src.utils.resource_finder import get_user_data_dir

logger = get_logger()


class SettingsModel(
    SettingsSystemOptionsMixin,
    SettingsMcpToolsMixin,
    SettingsWakeWordMixin,
    SettingsCameraOptionsMixin,
    SettingsAudioDevicesMixin,
    SettingsShortcutsMixin,
    SettingsCameraDevicesMixin,
    SettingsSmartHomeMixin,
    BaseModel,
):
    """Set the window data model (composed mixin)."""

    settingsChanged = Signal()
    devicesChanged = Signal()
    camerasChanged = Signal()
    statusMessage = Signal(str)
    mqttBrokerScanFinished = Signal(str)
    testComplete = Signal(str, bool)
    wakeWordChanged = Signal()
    configSaved = Signal()
    # Whether the MCP tool blocklist changed relative to when it was opened / last saved -> a reconnect may be triggered after saving
    mcpToolsNeedReconnect = Signal()

    def __init__(self, parent=None, event_bus=None, task_manager=None):
        super().__init__(parent)
        self._config_manager = get_config()
        self._config_path = get_user_data_dir() / "config" / "config.json"
        self._config: dict = {}
        # Optional: used by "Refresh audio devices" to coordinate with AudioPlugin to stop the stream and re-enumerate
        self._event_bus = event_bus
        self._task_manager = task_manager

        self._input_devices: list[dict] = []
        self._output_devices: list[dict] = []

        self._cameras: list[dict] = []
        self._cameras_loading = False
        self._cameras_loaded_once = False
        self._audio_devices_loaded = False
        self._audio_devices_refreshing = False
        self._mqtt_scan_running = False
        self.mqttBrokerScanFinished.connect(self._apply_mqtt_broker_scan)

        self._testing_input = False
        self._testing_output = False

        self._wake_word: str = ""
        self._wake_word_lang: str = "en"
        self._wake_word_preview: str = ""
        # Snapshot taken when the settings are opened; compared at save time to see whether the exposed MCP tools changed
        self._mcp_disabled_snapshot: list[str] = []

        # Startup only reads the config; Audio/Camera/wake-word preview handling is deferred until the settings are opened
        self._load_config()
        self._load_wake_word(update_preview=False)
        self._snapshot_mcp_disabled()

    def _run_worker(
        self,
        target,
        *args,
        name: str | None = None,
        test_kind: str | None = None,
        clear_flags=None,
    ) -> None:
        """Unified entry point for background threads: exceptions always reach statusMessage / testComplete."""

        def _entry():
            try:
                target(*args)
            except Exception as e:
                logger.error(
                    f"Settings background task failed ({name or target}): {e}", exc_info=True
                )
                try:
                    self.statusMessage.emit(f"[ERROR] {e}")
                except Exception:
                    pass
                if test_kind is not None:
                    try:
                        self.testComplete.emit(test_kind, False)
                    except Exception:
                        pass
            finally:
                if clear_flags is not None:
                    try:
                        clear_flags()
                    except Exception:
                        pass

        thread = threading.Thread(
            target=_entry, name=name or "settings:worker", daemon=True
        )
        thread.start()

    # ========== Config read/write ==========

    def _load_config(self):
        """Load config from file."""
        try:
            if self._config_path.exists():
                with open(self._config_path, encoding="utf-8") as f:
                    self._config = json.load(f)
                logger.debug("Settings config loaded")
            else:
                logger.warning(f"Config file does not exist: {self._config_path}")
                self._config = {}
        except Exception as e:
            logger.error(f"Failed to load config: {e}", exc_info=True)
            self._config = {}

    def _get_value(self, path: str, default: Any = None) -> Any:
        """Get a config value, supporting dot-separated paths."""
        keys = path.split(".")
        value = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def _set_value(self, path: str, value: Any):
        """Set a config value, supporting dot-separated paths."""
        keys = path.split(".")
        config = self._config
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value
        self.settingsChanged.emit()

    def _snapshot_mcp_disabled(self) -> None:
        from src.mcp.tool_catalog import normalize_disabled

        raw = self._get_value("MCP_TOOLS.DISABLED", []) or []
        self._mcp_disabled_snapshot = list(normalize_disabled(raw))

    @Slot()
    def save(self):
        """Save the config to the file and have the running ConfigManager reload it from disk."""
        try:
            from src.mcp.tool_catalog import normalize_disabled

            new_disabled = normalize_disabled(
                self._get_value("MCP_TOOLS.DISABLED", []) or []
            )
            mcp_changed = sorted(new_disabled) != sorted(self._mcp_disabled_snapshot)

            # Atomic write: temp file + replace, consistent with ConfigManager
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._config_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(self._config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self._config_path)
            try:
                self._config_manager.reload_config()
            except Exception as e:
                logger.warning(f"ConfigManager reload failed: {e}", exc_info=True)
            logger.info("Settings saved")
            self._snapshot_mcp_disabled()
            if mcp_changed:
                self.statusMessage.emit("Configuration saved (MCP tools changed; will reconnect to update)")
                self.mcpToolsNeedReconnect.emit()
            else:
                self.statusMessage.emit("Configuration saved")
            self.configSaved.emit()
        except Exception as e:
            logger.error(f"Failed to save config: {e}", exc_info=True)
            self.set_error(f"Failed to save configuration: {e}")

    @Slot()
    def reload(self):
        """Reload the config (called when the settings window is opened; devices are enumerated at this point)."""
        self._load_config()
        self._load_audio_devices(force=True)
        self._load_cameras(force=True)
        self._load_wake_word()
        self._snapshot_mcp_disabled()
        self.settingsChanged.emit()
        logger.info("Settings reloaded")

    # ========== QML Properties (implementations live in each mixin) ==========
    clientId = Property(str, SettingsSystemOptionsMixin._get_clientId, SettingsSystemOptionsMixin._set_clientId, notify=settingsChanged)
    deviceId = Property(str, SettingsSystemOptionsMixin._get_deviceId, SettingsSystemOptionsMixin._set_deviceId, notify=settingsChanged)
    otaUrl = Property(str, SettingsSystemOptionsMixin._get_otaUrl, SettingsSystemOptionsMixin._set_otaUrl, notify=settingsChanged)
    websocketUrl = Property(
        str, SettingsSystemOptionsMixin._get_websocketUrl, SettingsSystemOptionsMixin._set_websocketUrl, notify=settingsChanged
    )
    websocketToken = Property(
        str, SettingsSystemOptionsMixin._get_websocketToken, SettingsSystemOptionsMixin._set_websocketToken, notify=settingsChanged
    )
    authorizationUrl = Property(
        str, SettingsSystemOptionsMixin._get_authorizationUrl, SettingsSystemOptionsMixin._set_authorizationUrl, notify=settingsChanged
    )
    activationVersion = Property(
        str, SettingsSystemOptionsMixin._get_activationVersion, SettingsSystemOptionsMixin._set_activationVersion, notify=settingsChanged
    )
    windowSizeMode = Property(
        str, SettingsSystemOptionsMixin._get_windowSizeMode, SettingsSystemOptionsMixin._set_windowSizeMode, notify=settingsChanged
    )
    musicSearchUrl = Property(
        str, SettingsSystemOptionsMixin._get_musicSearchUrl, SettingsSystemOptionsMixin._set_musicSearchUrl, notify=settingsChanged
    )
    musicUrlApi = Property(
        str, SettingsSystemOptionsMixin._get_musicUrlApi, SettingsSystemOptionsMixin._set_musicUrlApi, notify=settingsChanged
    )
    musicUrlApiKey = Property(
        str, SettingsSystemOptionsMixin._get_musicUrlApiKey, SettingsSystemOptionsMixin._set_musicUrlApiKey, notify=settingsChanged
    )
    musicDefaultPlatform = Property(
        str,
        SettingsSystemOptionsMixin._get_musicDefaultPlatform,
        SettingsSystemOptionsMixin._set_musicDefaultPlatform,
        notify=settingsChanged,
    )
    musicDefaultQuality = Property(
        str, SettingsSystemOptionsMixin._get_musicDefaultQuality, SettingsSystemOptionsMixin._set_musicDefaultQuality, notify=settingsChanged
    )
    musicOpusCatalogUrl = Property(
        str, SettingsSystemOptionsMixin._get_musicOpusCatalogUrl, SettingsSystemOptionsMixin._set_musicOpusCatalogUrl, notify=settingsChanged
    )
    musicOpusStreamBase = Property(
        str, SettingsSystemOptionsMixin._get_musicOpusStreamBase, SettingsSystemOptionsMixin._set_musicOpusStreamBase, notify=settingsChanged
    )
    searchEngine = Property(
        str, SettingsSystemOptionsMixin._get_searchEngine, SettingsSystemOptionsMixin._set_searchEngine, notify=settingsChanged
    )
    anysearchUrl = Property(
        str, SettingsSystemOptionsMixin._get_anysearchUrl, SettingsSystemOptionsMixin._set_anysearchUrl, notify=settingsChanged
    )
    mqttEndpoint = Property(
        str, SettingsSystemOptionsMixin._get_mqttEndpoint, SettingsSystemOptionsMixin._set_mqttEndpoint, notify=settingsChanged
    )
    mqttClientId = Property(
        str, SettingsSystemOptionsMixin._get_mqttClientId, SettingsSystemOptionsMixin._set_mqttClientId, notify=settingsChanged
    )
    mqttUsername = Property(
        str, SettingsSystemOptionsMixin._get_mqttUsername, SettingsSystemOptionsMixin._set_mqttUsername, notify=settingsChanged
    )
    mqttPassword = Property(
        str, SettingsSystemOptionsMixin._get_mqttPassword, SettingsSystemOptionsMixin._set_mqttPassword, notify=settingsChanged
    )
    mqttPublishTopic = Property(
        str, SettingsSystemOptionsMixin._get_mqttPublishTopic, SettingsSystemOptionsMixin._set_mqttPublishTopic, notify=settingsChanged
    )
    mqttSubscribeTopic = Property(
        str, SettingsSystemOptionsMixin._get_mqttSubscribeTopic, SettingsSystemOptionsMixin._set_mqttSubscribeTopic, notify=settingsChanged
    )
    aecEnabled = Property(
        bool, SettingsSystemOptionsMixin._get_aecEnabled, SettingsSystemOptionsMixin._set_aecEnabled, notify=settingsChanged
    )
    aecMusicParallel = Property(
        bool, SettingsSystemOptionsMixin._get_aecMusicParallel, SettingsSystemOptionsMixin._set_aecMusicParallel, notify=settingsChanged
    )
    aecFrameDelay = Property(
        int, SettingsSystemOptionsMixin._get_aecFrameDelay, SettingsSystemOptionsMixin._set_aecFrameDelay, notify=settingsChanged
    )
    aecEnablePreprocess = Property(
        bool, SettingsSystemOptionsMixin._get_aecEnablePreprocess, SettingsSystemOptionsMixin._set_aecEnablePreprocess, notify=settingsChanged
    )
    pathCacheDir = Property(
        str,
        SettingsSystemOptionsMixin._get_pathCacheDir,
        SettingsSystemOptionsMixin._set_pathCacheDir,
        notify=settingsChanged,
    )
    pathLogDir = Property(
        str,
        SettingsSystemOptionsMixin._get_pathLogDir,
        SettingsSystemOptionsMixin._set_pathLogDir,
        notify=settingsChanged,
    )
    pathMusicCacheDir = Property(
        str,
        SettingsSystemOptionsMixin._get_pathMusicCacheDir,
        SettingsSystemOptionsMixin._set_pathMusicCacheDir,
        notify=settingsChanged,
    )
    pathKeywordsDir = Property(
        str,
        SettingsSystemOptionsMixin._get_pathKeywordsDir,
        SettingsSystemOptionsMixin._set_pathKeywordsDir,
        notify=settingsChanged,
    )
    pathMcpPluginsDir = Property(
        str,
        SettingsSystemOptionsMixin._get_pathMcpPluginsDir,
        SettingsSystemOptionsMixin._set_pathMcpPluginsDir,
        notify=settingsChanged,
    )
    pathDefaultCacheDir = Property(
        str, SettingsSystemOptionsMixin._get_pathDefaultCacheDir, notify=settingsChanged
    )
    pathDefaultLogDir = Property(
        str, SettingsSystemOptionsMixin._get_pathDefaultLogDir, notify=settingsChanged
    )
    pathDefaultMusicCacheDir = Property(
        str,
        SettingsSystemOptionsMixin._get_pathDefaultMusicCacheDir,
        notify=settingsChanged,
    )
    pathDefaultKeywordsDir = Property(
        str,
        SettingsSystemOptionsMixin._get_pathDefaultKeywordsDir,
        notify=settingsChanged,
    )
    pathDefaultMcpPluginsDir = Property(
        str,
        SettingsSystemOptionsMixin._get_pathDefaultMcpPluginsDir,
        notify=settingsChanged,
    )
    pathHints = Property(
        str, SettingsSystemOptionsMixin._get_pathHints, notify=settingsChanged
    )

    @Slot(str, result=str)
    def browseDirectory(self, which: str) -> str:
        """Open the system directory picker. which: cache|log|music|keywords|mcp.

        Returns the selected path; returns an empty string on cancel (QML must not write it back).
        """
        which = (which or "").strip().lower()
        getters = {
            "cache": self._get_pathCacheDir,
            "log": self._get_pathLogDir,
            "music": self._get_pathMusicCacheDir,
            "keywords": self._get_pathKeywordsDir,
            "mcp": self._get_pathMcpPluginsDir,
        }
        setters = {
            "cache": self._set_pathCacheDir,
            "log": self._set_pathLogDir,
            "music": self._set_pathMusicCacheDir,
            "keywords": self._set_pathKeywordsDir,
            "mcp": self._set_pathMcpPluginsDir,
        }
        titles = {
            "cache": "Select Cache Directory",
            "log": "Select Log Directory",
            "music": "Select Music Cache Directory",
            "keywords": "Select Wake Word Directory",
            "mcp": "Select MCP Plugin Directory",
        }
        if which not in getters:
            return ""
        current = getters[which]()
        path = self._browse_directory(titles[which], current, which)
        if path:
            setters[which](path)
        return path

    @Slot(str)
    def clearPathDir(self, which: str) -> None:
        """Clear a custom path (restore the default)."""
        which = (which or "").strip().lower()
        setters = {
            "cache": self._set_pathCacheDir,
            "log": self._set_pathLogDir,
            "music": self._set_pathMusicCacheDir,
            "keywords": self._set_pathKeywordsDir,
            "mcp": self._set_pathMcpPluginsDir,
        }
        if which in setters:
            setters[which]("")

    mcpToolsCatalogJson = Property(
        str, SettingsMcpToolsMixin._get_mcpToolsCatalogJson, notify=settingsChanged
    )
    mcpToolsPaginationEnabled = Property(
        bool,
        SettingsMcpToolsMixin._get_mcpToolsPaginationEnabled,
        SettingsMcpToolsMixin._set_mcpToolsPaginationEnabled,
        notify=settingsChanged,
    )
    mcpToolsDisabledJson = Property(
        str,
        SettingsMcpToolsMixin._get_mcpToolsDisabledJson,
        SettingsMcpToolsMixin._set_mcpToolsDisabledJson,
        notify=settingsChanged,
    )

    @Slot(str, bool)
    def setMcpToolEnabled(self, name: str, enabled: bool) -> None:
        self._set_mcpToolEnabled(name, enabled)

    @Slot(str, bool)
    def setMcpToolGroupEnabled(self, group: str, enabled: bool) -> None:
        self._set_mcpToolGroupEnabled(group, enabled)

    wakeWordEnabled = Property(
        bool, SettingsWakeWordMixin._get_wakeWordEnabled, SettingsWakeWordMixin._set_wakeWordEnabled, notify=settingsChanged
    )
    modelPath = Property(str, SettingsWakeWordMixin._get_modelPath, SettingsWakeWordMixin._set_modelPath, notify=settingsChanged)
    numThreads = Property(int, SettingsWakeWordMixin._get_numThreads, SettingsWakeWordMixin._set_numThreads, notify=settingsChanged)
    keywordsScore = Property(
        float, SettingsWakeWordMixin._get_keywordsScore, SettingsWakeWordMixin._set_keywordsScore, notify=settingsChanged
    )
    keywordsThreshold = Property(
        float, SettingsWakeWordMixin._get_keywordsThreshold, SettingsWakeWordMixin._set_keywordsThreshold, notify=settingsChanged
    )
    wakeWord = Property(str, SettingsWakeWordMixin._get_wakeWord, SettingsWakeWordMixin._set_wakeWord, notify=wakeWordChanged)
    wakeWordLang = Property(str, SettingsWakeWordMixin._get_wakeWordLang, notify=wakeWordChanged)
    wakeWordPreview = Property(str, SettingsWakeWordMixin._get_wakeWordPreview, notify=wakeWordChanged)
    cameraIndex = Property(
        int, SettingsCameraOptionsMixin._get_cameraIndex, SettingsCameraOptionsMixin._set_cameraIndex, notify=settingsChanged
    )
    frameWidth = Property(int, SettingsCameraOptionsMixin._get_frameWidth, SettingsCameraOptionsMixin._set_frameWidth, notify=settingsChanged)
    frameHeight = Property(
        int, SettingsCameraOptionsMixin._get_frameHeight, SettingsCameraOptionsMixin._set_frameHeight, notify=settingsChanged
    )
    jpegMaxSide = Property(
        int, SettingsCameraOptionsMixin._get_jpegMaxSide, SettingsCameraOptionsMixin._set_jpegMaxSide, notify=settingsChanged
    )
    fps = Property(int, SettingsCameraOptionsMixin._get_fps, SettingsCameraOptionsMixin._set_fps, notify=settingsChanged)
    vlApiUrl = Property(str, SettingsCameraOptionsMixin._get_vlApiUrl, SettingsCameraOptionsMixin._set_vlApiUrl, notify=settingsChanged)
    vlApiKey = Property(str, SettingsCameraOptionsMixin._get_vlApiKey, SettingsCameraOptionsMixin._set_vlApiKey, notify=settingsChanged)
    vlModels = Property(str, SettingsCameraOptionsMixin._get_vlModels, SettingsCameraOptionsMixin._set_vlModels, notify=settingsChanged)
    explainUrl = Property(str, SettingsCameraOptionsMixin._get_explainUrl, SettingsCameraOptionsMixin._set_explainUrl, notify=settingsChanged)
    explainToken = Property(str, SettingsCameraOptionsMixin._get_explainToken, SettingsCameraOptionsMixin._set_explainToken, notify=settingsChanged)
    selectedInputIndex = Property(
        int, SettingsAudioDevicesMixin._get_selectedInputIndex, SettingsAudioDevicesMixin._set_selectedInputIndex, notify=settingsChanged
    )
    selectedOutputIndex = Property(
        int, SettingsAudioDevicesMixin._get_selectedOutputIndex, SettingsAudioDevicesMixin._set_selectedOutputIndex, notify=settingsChanged
    )
    inputDeviceInfo = Property(str, SettingsAudioDevicesMixin._get_inputDeviceInfo, notify=settingsChanged)
    outputDeviceInfo = Property(str, SettingsAudioDevicesMixin._get_outputDeviceInfo, notify=settingsChanged)
    opusOutputSampleRate = Property(
        int,
        SettingsAudioDevicesMixin._get_opusOutputSampleRate,
        SettingsAudioDevicesMixin._set_opusOutputSampleRate,
        notify=settingsChanged,
    )
    frameDuration = Property(
        int, SettingsAudioDevicesMixin._get_frameDuration, SettingsAudioDevicesMixin._set_frameDuration, notify=settingsChanged
    )
    shortcutsEnabled = Property(
        bool, SettingsShortcutsMixin._get_shortcutsEnabled, SettingsShortcutsMixin._set_shortcutsEnabled, notify=settingsChanged
    )
    shortcutManualModifier = Property(
        str,
        SettingsShortcutsMixin._get_shortcutManualModifier,
        SettingsShortcutsMixin._set_shortcutManualModifier,
        notify=settingsChanged,
    )
    shortcutManualKey = Property(
        str, SettingsShortcutsMixin._get_shortcutManualKey, SettingsShortcutsMixin._set_shortcutManualKey, notify=settingsChanged
    )
    shortcutAutoModifier = Property(
        str,
        SettingsShortcutsMixin._get_shortcutAutoModifier,
        SettingsShortcutsMixin._set_shortcutAutoModifier,
        notify=settingsChanged,
    )
    shortcutAutoKey = Property(
        str, SettingsShortcutsMixin._get_shortcutAutoKey, SettingsShortcutsMixin._set_shortcutAutoKey, notify=settingsChanged
    )
    shortcutAbortModifier = Property(
        str,
        SettingsShortcutsMixin._get_shortcutAbortModifier,
        SettingsShortcutsMixin._set_shortcutAbortModifier,
        notify=settingsChanged,
    )
    shortcutAbortKey = Property(
        str, SettingsShortcutsMixin._get_shortcutAbortKey, SettingsShortcutsMixin._set_shortcutAbortKey, notify=settingsChanged
    )
    shortcutModeModifier = Property(
        str,
        SettingsShortcutsMixin._get_shortcutModeModifier,
        SettingsShortcutsMixin._set_shortcutModeModifier,
        notify=settingsChanged,
    )
    shortcutModeKey = Property(
        str, SettingsShortcutsMixin._get_shortcutModeKey, SettingsShortcutsMixin._set_shortcutModeKey, notify=settingsChanged
    )
    shortcutWindowModifier = Property(
        str,
        SettingsShortcutsMixin._get_shortcutWindowModifier,
        SettingsShortcutsMixin._set_shortcutWindowModifier,
        notify=settingsChanged,
    )
    shortcutWindowKey = Property(
        str, SettingsShortcutsMixin._get_shortcutWindowKey, SettingsShortcutsMixin._set_shortcutWindowKey, notify=settingsChanged
    )
    selectedCameraIndex = Property(
        int, SettingsCameraDevicesMixin._get_selectedCameraIndex, SettingsCameraDevicesMixin._set_selectedCameraIndex, notify=settingsChanged
    )
    smartHomeBroker = Property(
        str, SettingsSmartHomeMixin._get_smartHomeBroker, SettingsSmartHomeMixin._set_smartHomeBroker, notify=settingsChanged
    )
    smartHomePort = Property(
        int, SettingsSmartHomeMixin._get_smartHomePort, SettingsSmartHomeMixin._set_smartHomePort, notify=settingsChanged
    )
    smartHomeUsername = Property(
        str, SettingsSmartHomeMixin._get_smartHomeUsername, SettingsSmartHomeMixin._set_smartHomeUsername, notify=settingsChanged
    )
    smartHomePassword = Property(
        str, SettingsSmartHomeMixin._get_smartHomePassword, SettingsSmartHomeMixin._set_smartHomePassword, notify=settingsChanged
    )

