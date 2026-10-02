import copy
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from src.logging import get_logger
from src.utils.resource_finder import (
    get_config_dir,
    get_user_data_dir,
)

logger = get_logger()

# Process-local authoritative configuration: created by initialize_config(), read by get_config(); no lazy singleton
_current: "ConfigManager | None" = None


def initialize_config() -> "ConfigManager":
    """Create or return the initialized config manager (called from the application entry point)."""
    global _current
    if _current is None:
        _current = ConfigManager()
        # Apply application PATH overrides and migrate cache/logs/music/keywords (config directory is not migrated)
        try:
            from src.utils.resource_finder import apply_path_overrides_from_config

            apply_path_overrides_from_config(_current, migrate=True)
        except Exception as e:
            logger.warning("Failed to apply PATHS directory override: %s", e, exc_info=True)
    return _current


def get_config() -> "ConfigManager":
    """Get the initialized configuration manager.

    Raises:
        RuntimeError: initialize_config() has not been called yet
    """
    if _current is None:
        raise RuntimeError(
            "ConfigManager is not initialized: call initialize_config() from the entry point"
        )
    return _current


def reset_config() -> None:
    """Discard the process config (tests only)."""
    global _current
    _current = None


class ConfigManager:
    """
    Config manager (ordinary instance; process authority is held by initialize_config/get_config).
    """

    # Current schema version; migrate_config() upgrades it during load
    CONFIG_VERSION = 4

    # Default config (complete product schema; deep-copied on load; do not share nested objects with the instance)
    DEFAULT_CONFIG = {
        "CONFIG_VERSION": 4,
        "SYSTEM_OPTIONS": {
            "CLIENT_ID": None,
            "DEVICE_ID": None,
            "WINDOW_SIZE_MODE": "default",
            "NETWORK": {
                "OTA_VERSION_URL": "https://api.tenclass.net/xiaozhi/ota/",
                "WEBSOCKET_URL": None,
                "WEBSOCKET_ACCESS_TOKEN": None,
                "MQTT_INFO": None,
                "ACTIVATION_VERSION": "v2",  # Possible values: v1, v2
                "AUTHORIZATION_URL": "https://xiaozhi.me/",
            },
        },
        "WAKE_WORD_OPTIONS": {
            "USE_WAKE_WORD": True,
            "MODEL_PATH": "models/en",
            "NUM_THREADS": 2,
            "PROVIDER": "cpu",
            "MAX_ACTIVE_PATHS": 2,
            "KEYWORDS_SCORE": 1.8,
            "KEYWORDS_THRESHOLD": 0.2,
            "NUM_TRAILING_BLANKS": 1,
            "WAKE_WORD": "Hello Xiaozhi",
            "WAKE_WORD_LANG": "en",
        },
        "CAMERA": {
            "camera_index": 0,
            # Device path takes precedence over index, e.g. "/dev/video0" (Raspberry Pi USB/V4L2)
            "device": "",
            # auto | opencv | picamera2; auto tries OpenCV first, then CSI (picamera2) if it fails
            "backend": "auto",
            "frame_width": 640,
            "frame_height": 480,
            "jpeg_max_side": 1024,
            "fps": 30,
            # Number of warm-up frames to discard after opening (the first few frames are often invalid on USB/Pi)
            "warm_up_frames": 5,
            "Local_VL_url": "https://open.bigmodel.cn/api/paas/v4/",
            "VLapi_key": "",
            "models": "glm-4v-plus",
            # Remote vision service used by NormalCamera (image Q&A over HTTP).
            # Used when no VL API key is configured; the server-provided capability takes precedence.
            "explain_url": "https://api.xiaozhi.me/vision/explain",
            "explain_token": "",
        },
        "SHORTCUTS": {
            "ENABLED": True,
            "MANUAL_PRESS": {"modifier": "ctrl", "key": "j", "description": "Hold to Talk"},
            "AUTO_TOGGLE": {"modifier": "ctrl", "key": "k", "description": "Auto Conversation"},
            "ABORT": {"modifier": "ctrl", "key": "q", "description": "Interrupt Conversation"},
            "MODE_TOGGLE": {"modifier": "ctrl", "key": "m", "description": "Switch Mode"},
            "WINDOW_TOGGLE": {
                "modifier": "ctrl",
                "key": "w",
                "description": "Show/hide window",
            },
        },
        "AEC_OPTIONS": {
            "ENABLED": False,
            # When AEC is active, TTS does not pause music; ducking and parallel playback continue (engine bypass automatically falls back to pause)
            "MUSIC_PARALLEL": True,
            # Delay compensation (protocol frames); actual delay_ms = 40 + N * frame length
            "FRAME_DELAY": 3,
            # Noise suppression / high-pass pre-handle
            "ENABLE_PREPROCESS": True,
        },
        # Writable directory overrides (config remains fixed in the user data/config; null = default)
        # Environment variables take precedence: XIAOZHI_CACHE_DIR / XIAOZHI_LOG_DIR /
        # XIAOZHI_MUSIC_CACHE_DIR / XIAOZHI_KEYWORDS_DIR / XIAOZHI_DATA_DIR
        # After changing CACHE/LOG/MUSIC/KEYWORDS, the app copies from the old directory to the new one on startup (does not delete the old one)
        "PATHS": {
            "CACHE_DIR": None,
            "LOG_DIR": None,
            "MUSIC_CACHE_DIR": None,
            "KEYWORDS_DIR": None,
        },
        # External MCP plugins (user directory mcp_plugins, with bundled lib/)
        "MCP_PLUGINS": {
            "ENABLED": True,
            "DIR": None,  # null = {user data}/mcp_plugins
            "ENABLED_IDS": [],  # empty = use plugin enabled_by_default
            "DISABLED_IDS": [],
            "ALLOW_HOST_GET": ["config_readonly", "logger"],
            # Strict validation (off by default to avoid failing to load example packages without platforms/abi)
            "ENFORCE_PREFIX": False,
            "REQUIRE_PYTHON_ABI": False,
            "REQUIRE_PLATFORMS": False,
        },
        # MCP tool exposure (blacklist: not shown in tools/list, and call is denied)
        "MCP_TOOLS": {
            "DISABLED": [],  # Example: ["music_player.stop", "self.application.launch"]
            # Set to False to return all tools in a single tools/list payload without
            # using nextCursor pagination. This is useful for clients that do not handle
            # paged MCP tool catalogs correctly.
            "PAGINATION_ENABLED": True,
            # Wall-clock budget for a single tools/call. The xiaozhi server closes the
            # session when a tool call takes longer than its own limit, so a slow tool
            # (e.g. an nmap scan) must be answered within this window. 0 = no limit.
            "CALL_TIMEOUT": 45,
        },
        # Web search backend used by the web_search / read_article MCP tools.
        # SEARCH_ENGINE selects the keyless backend: "anysearch" (default) or
        # "gnews" (Google News RSS only). ANYSEARCH_URL lets a self-hosted
        # anysearch instance be used; blank = the public endpoint.
        "WEB_SEARCH": {
            "SEARCH_ENGINE": "anysearch",
            "ANYSEARCH_URL": "",
        },
        "CODING": {
            "WORKSPACE": "",
        },
        "AUDIO_DEVICES": {
            "input_device_id": None,
            "input_device_name": None,
            "output_device_id": None,
            "output_device_name": None,
            "input_sample_rate": None,
            "output_sample_rate": None,
            "input_channels": None,
            "output_channels": None,
            "opus_output_sample_rate": 24000,  # Opus decode sample rate: 24000 (official) or 16000 (third-party)
            "frame_duration": 20,  # audio frame length (ms): 20 (low latency) / 40 (balanced) / 60 (low CPU)
        },
        "LOGGING": {
            "LEVEL": "INFO",  # DEBUG, INFO, WARNING, ERROR, CRITICAL
            "FORMAT_TYPE": "colored",  # colored, json, simple
            "ENABLE_CONSOLE": True,
            "ENABLE_FILE": True,
            "ENABLE_ERROR_FILE": True,
            "ENABLE_JSON_FILE": False,
            "ENABLE_ASYNC": False,
            "ENABLE_SENSITIVE_FILTER": True,
            "MAX_BYTES": 10485760,  # 10MB
            "BACKUP_COUNT": 30,
            "ROTATION_WHEN": "midnight",  # midnight, H, D
            "THIRD_PARTY_LEVELS": {
                "urllib3": "WARNING",
                "websockets": "WARNING",
                "asyncio": "WARNING",
                "paho": "WARNING",
                "PIL": "WARNING",
            },
        },
        # music API (empty string = use the built-in default URL at runtime)
        "MUSIC": {
            "SEARCH_URL": "",
            "URL_API": "",
            "URL_API_KEY": "",
            "LYRICS_URL": "",
            "DEFAULT_PLATFORM": "kw",
            "DEFAULT_QUALITY": "320k",
            # Online Opus song catalog (reference-app style); empty = built-in default
            "OPUS_CATALOG_URL": "",
            "OPUS_STREAM_BASE": "",
        },
    }

    def __init__(self):
        """Initialize the config manager (direct construction; use initialize_config from the application)."""
        self._init_config_paths()
        self._config = self._load_config()

    def _init_config_paths(self):
        """
        Initialize config file paths.

        Configuration files are stored under the user data directory and are writable after packaging.
        On first run, the default configuration is migrated from the install directory.
        """
        self.config_dir = get_user_data_dir() / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.config_file = self.config_dir / "config.json"

        # If the user directory has no config file, try to migrate it from the install directory
        if not self.config_file.exists():
            install_config = get_config_dir() / "config.json"
            if install_config.exists():
                try:
                    # Validate the JSON first, to avoid copying a corrupt install config
                    json.loads(install_config.read_text(encoding="utf-8"))
                    shutil.copy2(install_config, self.config_file)
                    logger.info(
                        f"Migrated config from install directory: {install_config} -> {self.config_file}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to migrate config file: {e}; using default config", exc_info=True
                    )

        logger.info(f"Config directory: {self.config_dir.absolute()}")
        logger.info(f"Config file: {self.config_file.absolute()}")

    def _default_config_copy(self) -> Dict[str, Any]:
        """Deep-copy the default config so the instance does not share nested dicts/lists with the class property."""
        return copy.deepcopy(self.DEFAULT_CONFIG)

    def _load_config(self) -> Dict[str, Any]:
        """Load the config file; create it if it does not exist; back it up and fall back to the default if it is corrupt."""
        try:
            if self.config_file.exists():
                logger.debug(f"Config file found: {self.config_file}")
                try:
                    raw = self.config_file.read_text(encoding="utf-8")
                    config = json.loads(raw)
                except Exception as e:
                    backup = self._backup_corrupt_config(e)
                    logger.error(
                        "Config file corrupt; backed up%s and fell back to default: %s",
                        f" to {backup}" if backup else "",
                        e,
                        exc_info=True,
                    )
                    defaults = self._default_config_copy()
                    self._save_config(defaults)
                    return defaults

                if not isinstance(config, dict):
                    backup = self._backup_corrupt_config(
                        TypeError(f"The root node must be an object, but is {type(config).__name__}")
                    )
                    logger.error(
                        "Config file root node invalid; backed up%s and fell back to default",
                        f" to {backup}" if backup else "",
                    )
                    defaults = self._default_config_copy()
                    self._save_config(defaults)
                    return defaults

                merged = self._merge_configs(self._default_config_copy(), config)
                # The version is taken from the on-disk file; the merge would bring in the default CONFIG_VERSION and cause a false "already migrated" verdict
                try:
                    file_ver = int(config.get("CONFIG_VERSION", 0) or 0)
                except (TypeError, ValueError):
                    file_ver = 0
                return self._migrate_config(merged, from_version=file_ver)

            logger.info("Config file does not exist; creating default config")
            defaults = self._default_config_copy()
            self._save_config(defaults)
            return defaults

        except Exception as e:
            logger.error(f"Config load error: {e}", exc_info=True)
            return self._default_config_copy()

    def _migrate_config(
        self, config: Dict[str, Any], *, from_version: int | None = None
    ) -> Dict[str, Any]:
        """Forward-migrate according to CONFIG_VERSION; write back to disk if needed.

        from_version: the version in the on-disk file (before the merge). If omitted, the field
        inside the config is read (in that case, if the defaults have already been merged, it may
        already be the latest version and the migration will be skipped).

        Convention:
        - default / invalid version treated as 0
        - each step only performs low-reversibility patches (renaming, filling in sections, normalizing)
        - after reaching CONFIG_VERSION it is written back to avoid repeating the migration next time
        """
        if from_version is not None:
            ver = int(from_version)
        else:
            try:
                raw_ver = config.get("CONFIG_VERSION", 0)
                try:
                    ver = int(raw_ver)
                except (TypeError, ValueError):
                    ver = 0
            except Exception:
                ver = 0

        original = ver
        # --- Migration steps (append in version order) ---
        if ver < 1:
            # v1: introduced the version number; filled in MCP_TOOLS; normalized the subscribe_topic string "null"
            config.setdefault("MCP_TOOLS", {"DISABLED": []})
            if not isinstance(config.get("MCP_TOOLS"), dict):
                config["MCP_TOOLS"] = {"DISABLED": []}
            config["MCP_TOOLS"].setdefault("DISABLED", [])

            try:
                net = config.get("SYSTEM_OPTIONS", {}).get("NETWORK", {})
                mqtt = net.get("MQTT_INFO")
                if isinstance(mqtt, dict) and mqtt.get("subscribe_topic") == "null":
                    mqtt["subscribe_topic"] = None
            except Exception:
                pass
            ver = 1

        # v2: the default wake word is now English ("Hello Xiaozhi"). Earlier defaults paired that
        # English phrase with the Chinese model (WAKE_WORD_LANG="zh", MODEL_PATH="models/zh"), so
        # detection never fired. Only configs that still carry that broken default are repaired;
        # user-customized wake words are left untouched.
        if ver < 2:
            try:
                ww = config.get("WAKE_WORD_OPTIONS")
                if not isinstance(ww, dict):
                    ww = {}
                    config["WAKE_WORD_OPTIONS"] = ww
                if ww.get("WAKE_WORD") == "Hello Xiaozhi" and ww.get("WAKE_WORD_LANG") == "zh":
                    ww["WAKE_WORD_LANG"] = "en"
                    ww["MODEL_PATH"] = "models/en"
            except Exception:
                pass
            ver = 2

        # v3: tools/call now has a wall-clock budget (MCP_TOOLS.CALL_TIMEOUT). Without it a
        # slow tool (an nmap scan can run for minutes) outlives the server-side tool-call
        # limit: the session is torn down and the result is discarded, so the user sees a
        # disconnect instead of an answer.
        if ver < 3:
            config.setdefault("MCP_TOOLS", {})
            if not isinstance(config.get("MCP_TOOLS"), dict):
                config["MCP_TOOLS"] = {}
            config["MCP_TOOLS"].setdefault("DISABLED", [])
            config["MCP_TOOLS"].setdefault("CALL_TIMEOUT", 45)
            ver = 3

        # v4: web search has a pluggable keyless backend. Earlier configs have no
        # WEB_SEARCH section at all, so the defaults are filled in here; users who
        # never touched search get the new backend automatically.
        if ver < 4:
            config.setdefault("WEB_SEARCH", {})
            if not isinstance(config.get("WEB_SEARCH"), dict):
                config["WEB_SEARCH"] = {}
            config["WEB_SEARCH"].setdefault("SEARCH_ENGINE", "anysearch")
            config["WEB_SEARCH"].setdefault("ANYSEARCH_URL", "")
            ver = 4

        if ver != original or config.get("CONFIG_VERSION") != self.CONFIG_VERSION:
            config["CONFIG_VERSION"] = self.CONFIG_VERSION
            if self._save_config(config):
                logger.info(
                    "Config migrated: v%s -> v%s", original, self.CONFIG_VERSION
                )
            else:
                logger.warning(
                    "Failed to write back after config migration (in-memory is v%s)", self.CONFIG_VERSION
                )
        else:
            config["CONFIG_VERSION"] = self.CONFIG_VERSION
        return config

    def _backup_corrupt_config(self, error: Exception) -> str | None:
        """Back up the corrupt config.json as .corrupt-<timestamp> and return the backup path."""
        try:
            if not self.config_file.exists():
                return None
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.config_file.with_name(f"config.json.corrupt-{ts}")
            # Avoid duplicate names in extreme cases
            n = 0
            while backup.exists():
                n += 1
                backup = self.config_file.with_name(f"config.json.corrupt-{ts}-{n}")
            shutil.copy2(self.config_file, backup)
            logger.warning("Corrupt config backed up: %s (%s)", backup, error)
            return str(backup)
        except Exception as e:
            logger.error("Failed to back up corrupt config: %s", e, exc_info=True)
            return None

    def _save_config(self, config: dict) -> bool:
        """Atomically write the config file (temp file + rename to prevent corruption from interrupted writes)."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)

            tmp_file = self.config_file.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp_file, self.config_file)
            logger.debug(f"Config saved to: {self.config_file}")
            return True

        except Exception as e:
            logger.error(f"Config save error: {e}", exc_info=True)
            return False

    @staticmethod
    def _merge_configs(default: dict, custom: dict) -> dict:
        """Recursively merge the configs: default is the base, custom overrides; when both sides are dicts they are deep-merged.

        Note: the caller should pass an already deep-copied default to avoid polluting the class-level DEFAULT_CONFIG.
        """
        result = default  # If it is already an independent copy, merge in place
        for key, value in custom.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = ConfigManager._merge_configs(result[key], value)
            else:
                result[key] = value
        return result

    def get_config(self, path: str, default: Any = None) -> Any:
        """
        Get config value by path
        path: the dot-separated config path, e.g. "SYSTEM_OPTIONS.NETWORK.MQTT_INFO"
        """
        try:
            value = self._config
            for key in path.split("."):
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def update_config(self, path: str, value: Any, *, save: bool = True) -> bool:
        """
        Update a specific config item
        path: the dot-separated config path, e.g. "SYSTEM_OPTIONS.NETWORK.MQTT_INFO"
        save: whether to persist immediately; pass False during batch updates and call save_config()/update_configs at the end

        If an intermediate node is None / not a dict, it is promoted to a dict before writing
        (compatible with defaults such as MQTT_INFO: null).
        """
        try:
            current: Any = self._config
            *parts, last = path.split(".")
            for part in parts:
                if not isinstance(current, dict):
                    raise TypeError(
                        f"The config path prefix is not an object; cannot write {path} (current node type {type(current).__name__})"
                    )
                existing = current.get(part, None)
                if not isinstance(existing, dict):
                    # Key missing, or the value is None/scalar: create an empty object so we can keep descending
                    existing = {}
                    current[part] = existing
                current = existing
            if not isinstance(current, dict):
                raise TypeError(f"Config path cannot be written: {path}")
            current[last] = value
            if not save:
                return True
            return self._save_config(self._config)
        except Exception as e:
            logger.error(f"Config update error {path}: {e}", exc_info=True)
            return False

    def update_configs(self, updates: Dict[str, Any]) -> bool:
        """Batch update multiple config paths, writing to disk only once.

        Args:
            updates: path -> value, e.g.
                {"SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL": "wss://..."}
        """
        if not updates:
            return True
        try:
            for path, value in updates.items():
                if not self.update_config(path, value, save=False):
                    return False
            return self._save_config(self._config)
        except Exception as e:
            logger.error(f"Batch config update error: {e}", exc_info=True)
            return False

    def save_config(self) -> bool:
        """Persist the current in-memory config to disk."""
        return self._save_config(self._config)

    def reload_config(self, *, apply_paths: bool = True) -> bool:
        """Reload the config file.

        apply_paths: whether to re-apply the PATHS overrides (no directory migration).
        PATHS directory migration is only performed in initialize_config.
        """
        try:
            self._config = self._load_config()
            if apply_paths:
                try:
                    from src.utils.resource_finder import (
                        apply_path_overrides_from_config,
                    )

                    apply_path_overrides_from_config(self, migrate=False)
                except Exception as e:
                    logger.warning(
                        "Failed to apply PATHS after reload: %s", e, exc_info=True
                    )
            logger.info("Config file reloaded")
            return True
        except Exception as e:
            logger.error(f"Config reload failed: {e}", exc_info=True)
            return False

    def generate_uuid(self) -> str:
        """Generate UUID v4."""
        return str(uuid.uuid4())

    def initialize_client_id(self):
        """Ensure a client ID exists."""
        if not self.get_config("SYSTEM_OPTIONS.CLIENT_ID"):
            client_id = self.generate_uuid()
            success = self.update_config("SYSTEM_OPTIONS.CLIENT_ID", client_id)
            if success:
                logger.info(f"New client ID generated: {client_id}")
            else:
                logger.error("Failed to save new client ID")
