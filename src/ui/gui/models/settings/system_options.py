"""System options: device ID, network, MQTT, music, AEC."""


class SettingsSystemOptionsMixin:
    # ========== System options ==========

    # CLIENT_ID
    def _get_clientId(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.CLIENT_ID", "")

    def _set_clientId(self, value: str):
        self._set_value("SYSTEM_OPTIONS.CLIENT_ID", value)

    # DEVICE_ID
    def _get_deviceId(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.DEVICE_ID", "")

    def _set_deviceId(self, value: str):
        self._set_value("SYSTEM_OPTIONS.DEVICE_ID", value)

    def _get_codingWorkspace(self) -> str:
        return self._get_value("CODING.WORKSPACE", "")

    def _set_codingWorkspace(self, value: str):
        self._set_value("CODING.WORKSPACE", value.strip() if value else "")

    # OTA_VERSION_URL
    def _get_otaUrl(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.OTA_VERSION_URL", "")

    def _set_otaUrl(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.OTA_VERSION_URL", value)

    # WEBSOCKET_URL
    def _get_websocketUrl(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL", "")

    def _set_websocketUrl(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL", value)

    # WEBSOCKET_ACCESS_TOKEN
    def _get_websocketToken(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.WEBSOCKET_ACCESS_TOKEN", "")

    def _set_websocketToken(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.WEBSOCKET_ACCESS_TOKEN", value)

    # AUTHORIZATION_URL
    def _get_authorizationUrl(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.AUTHORIZATION_URL", "")

    def _set_authorizationUrl(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.AUTHORIZATION_URL", value)

    # ACTIVATION_VERSION
    def _get_activationVersion(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.ACTIVATION_VERSION", "v1")

    def _set_activationVersion(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.ACTIVATION_VERSION", value)

    # WINDOW_SIZE_MODE
    def _get_windowSizeMode(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.WINDOW_SIZE_MODE", "default")

    def _set_windowSizeMode(self, value: str):
        self._set_value("SYSTEM_OPTIONS.WINDOW_SIZE_MODE", value)

    # Music configuration
    def _get_musicSearchUrl(self) -> str:
        return self._get_value("MUSIC.SEARCH_URL", "")

    def _set_musicSearchUrl(self, value: str):
        self._set_value("MUSIC.SEARCH_URL", value)

    def _get_musicUrlApi(self) -> str:
        return self._get_value("MUSIC.URL_API", "")

    def _set_musicUrlApi(self, value: str):
        self._set_value("MUSIC.URL_API", value)

    def _get_musicUrlApiKey(self) -> str:
        return self._get_value("MUSIC.URL_API_KEY", "")

    def _set_musicUrlApiKey(self, value: str):
        self._set_value("MUSIC.URL_API_KEY", value)

    def _get_musicDefaultPlatform(self) -> str:
        return self._get_value("MUSIC.DEFAULT_PLATFORM", "kw")

    def _set_musicDefaultPlatform(self, value: str):
        self._set_value("MUSIC.DEFAULT_PLATFORM", value)

    def _get_musicDefaultQuality(self) -> str:
        return self._get_value("MUSIC.DEFAULT_QUALITY", "320k")

    def _set_musicDefaultQuality(self, value: str):
        self._set_value("MUSIC.DEFAULT_QUALITY", value)

    def _get_musicOpusCatalogUrl(self) -> str:
        return self._get_value("MUSIC.OPUS_CATALOG_URL", "")

    def _set_musicOpusCatalogUrl(self, value: str):
        self._set_value("MUSIC.OPUS_CATALOG_URL", value)

    def _get_musicOpusStreamBase(self) -> str:
        return self._get_value("MUSIC.OPUS_STREAM_BASE", "")

    def _set_musicOpusStreamBase(self, value: str):
        self._set_value("MUSIC.OPUS_STREAM_BASE", value)

    # Web search configuration
    def _get_searchEngine(self) -> str:
        return self._get_value("WEB_SEARCH.SEARCH_ENGINE", "anysearch")

    def _set_searchEngine(self, value: str):
        self._set_value("WEB_SEARCH.SEARCH_ENGINE", value)

    def _get_anysearchUrl(self) -> str:
        return self._get_value("WEB_SEARCH.ANYSEARCH_URL", "")

    def _set_anysearchUrl(self, value: str):
        self._set_value("WEB_SEARCH.ANYSEARCH_URL", value)

    # MQTT configuration
    def _get_mqttEndpoint(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.endpoint", "")
    def _set_mqttEndpoint(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.endpoint", value)

    def _get_mqttClientId(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.client_id", "")

    def _set_mqttClientId(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.client_id", value)

    def _get_mqttUsername(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.username", "")

    def _set_mqttUsername(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.username", value)

    def _get_mqttPassword(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.password", "")

    def _set_mqttPassword(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.password", value)

    def _get_mqttPublishTopic(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.publish_topic", "")

    def _set_mqttPublishTopic(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.publish_topic", value)

    def _get_mqttSubscribeTopic(self) -> str:
        return self._get_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.subscribe_topic", "")

    def _set_mqttSubscribeTopic(self, value: str):
        self._set_value("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.subscribe_topic", value)

    # AEC enabled
    def _get_aecEnabled(self) -> bool:
        return self._get_value("AEC_OPTIONS.ENABLED", False)

    def _set_aecEnabled(self, value: bool):
        self._set_value("AEC_OPTIONS.ENABLED", value)

    # When AEC is present, TTS and music play in parallel (ducking mix)
    def _get_aecMusicParallel(self) -> bool:
        return self._get_value("AEC_OPTIONS.MUSIC_PARALLEL", True)

    def _set_aecMusicParallel(self, value: bool):
        self._set_value("AEC_OPTIONS.MUSIC_PARALLEL", value)

    # Delay compensation frames (40ms + N x protocol frame duration)
    def _get_aecFrameDelay(self) -> int:
        try:
            return int(self._get_value("AEC_OPTIONS.FRAME_DELAY", 3))
        except (TypeError, ValueError):
            return 3

    def _set_aecFrameDelay(self, value: int):
        self._set_value("AEC_OPTIONS.FRAME_DELAY", int(value))

    # Noise suppression / high-pass preprocessing
    def _get_aecEnablePreprocess(self) -> bool:
        return self._get_value("AEC_OPTIONS.ENABLE_PREPROCESS", True)

    def _set_aecEnablePreprocess(self, value: bool):
        self._set_value("AEC_OPTIONS.ENABLE_PREPROCESS", value)

    # ========== Writable directories PATHS (the config directory is not changed here) ==========

    def _get_pathCacheDir(self) -> str:
        return self._get_value("PATHS.CACHE_DIR", "") or ""

    def _set_pathCacheDir(self, value: str):
        self._set_value("PATHS.CACHE_DIR", value.strip() if value else "")

    def _get_pathLogDir(self) -> str:
        return self._get_value("PATHS.LOG_DIR", "") or ""

    def _set_pathLogDir(self, value: str):
        self._set_value("PATHS.LOG_DIR", value.strip() if value else "")

    def _get_pathMusicCacheDir(self) -> str:
        return self._get_value("PATHS.MUSIC_CACHE_DIR", "") or ""

    def _set_pathMusicCacheDir(self, value: str):
        self._set_value("PATHS.MUSIC_CACHE_DIR", value.strip() if value else "")

    def _get_pathKeywordsDir(self) -> str:
        return self._get_value("PATHS.KEYWORDS_DIR", "") or ""

    def _set_pathKeywordsDir(self, value: str):
        self._set_value("PATHS.KEYWORDS_DIR", value.strip() if value else "")

    def _get_pathMcpPluginsDir(self) -> str:
        return self._get_value("MCP_PLUGINS.DIR", "") or ""

    def _set_pathMcpPluginsDir(self, value: str):
        self._set_value("MCP_PLUGINS.DIR", value.strip() if value else "")

    def _default_data_paths(self) -> dict[str, str]:
        """The system default absolute paths for each directory when the config is empty (excluding PATHS overrides)."""
        from pathlib import Path

        from src.utils.resource_finder import get_user_data_dir

        data = get_user_data_dir()
        cache_custom = (self._get_pathCacheDir() or "").strip()
        cache_default = Path(cache_custom) if cache_custom else (data / "cache")
        return {
            "cache": str(data / "cache"),
            "log": str(data / "logs"),
            # Music is mounted under the current cache by default: follows a custom cache path
            "music": str(cache_default / "music"),
            "keywords": str(data / "keywords"),
            "mcp": str(data / "mcp_plugins"),
        }

    def _get_pathDefaultCacheDir(self) -> str:
        try:
            return self._default_data_paths()["cache"]
        except Exception:
            return ""

    def _get_pathDefaultLogDir(self) -> str:
        try:
            return self._default_data_paths()["log"]
        except Exception:
            return ""

    def _get_pathDefaultMusicCacheDir(self) -> str:
        try:
            return self._default_data_paths()["music"]
        except Exception:
            return ""

    def _get_pathDefaultKeywordsDir(self) -> str:
        try:
            return self._default_data_paths()["keywords"]
        except Exception:
            return ""

    def _get_pathDefaultMcpPluginsDir(self) -> str:
        try:
            return self._default_data_paths()["mcp"]
        except Exception:
            return ""

    def _get_pathHints(self) -> str:
        """Read-only: the data root and usage hints (the default paths are already shown as placeholders in each input field)."""
        try:
            from src.utils.resource_finder import get_user_data_dir

            data = get_user_data_dir()
            return (
                f"Data root (config stays here): {data}\n"
                f"Empty = default; click \"Browse\" for the system dialog; migrates on next launch after saving"
            )
        except Exception:
            return "Leave empty to use the default path; migrates on next launch after saving"

    def _browse_directory(self, title: str, current: str, which: str = "") -> str:
        """Open the system folder picker; returns an empty string on cancel (the caller must not overwrite)."""
        from pathlib import Path

        from PySide6.QtWidgets import QApplication, QFileDialog

        start = current.strip() if current else ""
        if not start and which:
            try:
                start = self._default_data_paths().get(which, "")
            except Exception:
                start = ""
        if not start:
            try:
                from src.utils.resource_finder import get_user_data_dir

                start = str(get_user_data_dir())
            except Exception:
                start = str(Path.home())
        parent = QApplication.activeWindow()
        path = QFileDialog.getExistingDirectory(
            parent,
            title,
            start,
            QFileDialog.Option.ShowDirsOnly
            | QFileDialog.Option.DontResolveSymlinks,
        )
        return path or ""

