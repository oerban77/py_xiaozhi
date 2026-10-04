"""TUI Settings: config field definitions and read/write (connects to ConfigManager + CONFIG_CHANGED)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()


@dataclass(frozen=True)
class SettingField:
    """An editable config item."""

    path: str
    label: str
    kind: str = "str"  # str | int | float | bool | choice | password | device | wake_word | mcp_tools
    choices: tuple[str, ...] = ()
    help: str = ""


# Editable settings mirrored from the GUI tabs.
SETTING_SECTIONS: list[tuple[str, list[SettingField]]] = [
    (
        "System Options",
        [
            SettingField(
                "SYSTEM_OPTIONS.NETWORK.OTA_VERSION_URL",
                "OTA URL",
                help="OTA / activation config address",
            ),
            SettingField(
                "SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL",
                "WebSocket URL",
                help="WebSocket service address",
            ),
            SettingField(
                "SYSTEM_OPTIONS.DEVICE_ID",
                "Device ID",
                help="Device-Id (usually MAC)",
            ),
            SettingField(
                "SYSTEM_OPTIONS.CLIENT_ID",
                "Client ID",
                help="Client-Id",
            ),
            SettingField(
                "CODING.WORKSPACE",
                "Coding Workspace",
                kind="workspace",
                help="Workspace folder for coding tools; empty uses the app launch folder",
            ),
            SettingField("AEC_OPTIONS.ENABLED", "Echo Cancellation", kind="bool"),
            SettingField("AEC_OPTIONS.MUSIC_PARALLEL", "Parallel Music", kind="bool"),
            SettingField("AEC_OPTIONS.FRAME_DELAY", "AEC Delay Frames", kind="int"),
            SettingField("AEC_OPTIONS.ENABLE_PREPROCESS", "Noise Suppression", kind="bool"),
            SettingField("SYSTEM_OPTIONS.NETWORK.WEBSOCKET_ACCESS_TOKEN", "WebSocket Token", kind="password"),
            SettingField("SYSTEM_OPTIONS.NETWORK.AUTHORIZATION_URL", "Authorization URL"),
            SettingField("SYSTEM_OPTIONS.NETWORK.ACTIVATION_VERSION", "Activation Version", kind="choice", choices=("v1", "v2")),
            SettingField("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.endpoint", "MQTT Endpoint"),
            SettingField("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.client_id", "MQTT Client ID"),
            SettingField("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.username", "MQTT Username"),
            SettingField("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.password", "MQTT Password", kind="password"),
            SettingField("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.publish_topic", "MQTT Publish Topic"),
            SettingField("SYSTEM_OPTIONS.NETWORK.MQTT_INFO.subscribe_topic", "MQTT Subscribe Topic"),
            SettingField("PATHS.CACHE_DIR", "Cache Directory", help="Empty uses the default"),
            SettingField("PATHS.LOG_DIR", "Log Directory", help="Empty uses the default"),
            SettingField("PATHS.MUSIC_CACHE_DIR", "Music Cache Directory", help="Empty uses the default"),
            SettingField("PATHS.KEYWORDS_DIR", "Wake Word Directory", help="Empty uses the default"),
            SettingField("MCP_PLUGINS.DIR", "MCP Plugin Directory", help="Empty uses the default"),
            SettingField("WEB_SEARCH.SEARCH_ENGINE", "Search Engine", kind="choice", choices=("anysearch", "gnews")),
            SettingField("WEB_SEARCH.ANYSEARCH_URL", "Anysearch URL", help="Empty uses the public endpoint"),
        ],
    ),
    (
        "Audio Devices",
        [
            SettingField(
                "AUDIO_DEVICES.input_device_name",
                "Input Device",
                kind="audio_input",
                help="Match the microphone by name (the ID changes after hot-plugging)",
            ),
            SettingField(
                "AUDIO_DEVICES.output_device_name",
                "Output Device",
                kind="audio_output",
                help="Match speakers/headphones by name",
            ),
            SettingField(
                "AUDIO_DEVICES.opus_output_sample_rate",
                "Opus output sample rate",
                kind="choice",
                choices=("24000", "16000"),
                help="Official 24000 / third-party often 16000",
            ),
            SettingField(
                "AUDIO_DEVICES.frame_duration",
                "Frame duration ms",
                kind="choice",
                choices=("20", "40", "60"),
                help="20 low latency / 60 low CPU",
            ),
        ],
    ),
    (
        "Camera",
        [
            SettingField(
                "CAMERA.selected_device",
                "Camera Device",
                kind="camera_device",
                help="Detected camera devices",
            ),
            SettingField(
                "CAMERA.frame_width",
                "Width",
                kind="int",
            ),
            SettingField(
                "CAMERA.frame_height",
                "Height",
                kind="int",
            ),
            SettingField("CAMERA.fps", "Frame Rate", kind="int"),
            SettingField(
                "CAMERA.jpeg_max_side",
                "Max JPEG side",
                kind="choice",
                choices=("320", "640", "1024", "1280", "1920"),
                help="Maximum image edge sent for analysis; larger images use more bandwidth",
            ),
            SettingField("CAMERA.Local_VL_url", "VL API URL"),
            SettingField("CAMERA.VLapi_key", "VL API Key", kind="password"),
            SettingField("CAMERA.models", "VL Model"),
            SettingField("CAMERA.explain_url", "Vision Service URL"),
            SettingField("CAMERA.explain_token", "Vision Service Token", kind="password"),
        ],
    ),
    (
        "Wake Word",
        [
            SettingField(
                "WAKE_WORD_OPTIONS.USE_WAKE_WORD",
                "Enable Wake Word",
                kind="bool",
            ),
            SettingField(
                "WAKE_WORD_OPTIONS.WAKE_WORD",
                "Wake Word",
                kind="wake_word",
            ),
            SettingField("WAKE_WORD_OPTIONS.NUM_THREADS", "Threads", kind="int"),
            SettingField("WAKE_WORD_OPTIONS.KEYWORDS_SCORE", "Keyword Score", kind="float"),
            SettingField("WAKE_WORD_OPTIONS.KEYWORDS_THRESHOLD", "Keyword Threshold", kind="float"),
        ],
    ),
    (
        "MCP Tools",
        [
            SettingField("MCP_TOOLS.PAGINATION_ENABLED", "Enable MCP Pagination", kind="bool"),
            SettingField("SMART_HOME.MQTT.BROKER", "Smart Home Broker"),
            SettingField("SMART_HOME.MQTT.PORT", "Smart Home Port", kind="int"),
            SettingField("SMART_HOME.MQTT.USERNAME", "Smart Home Username"),
            SettingField("SMART_HOME.MQTT.PASSWORD", "Smart Home Password", kind="password"),
            SettingField("MCP_TOOLS.DISABLED", "Enabled Tools", kind="mcp_tools"),
        ],
    ),
    (
        "Shortcuts",
        [
            SettingField("SHORTCUTS.ENABLED", "Enable Global Shortcuts", kind="bool"),
            SettingField("SHORTCUTS.MANUAL_PRESS.modifier", "Hold to Talk Modifier", kind="choice", choices=("ctrl", "alt", "shift", "cmd")),
            SettingField("SHORTCUTS.MANUAL_PRESS.key", "Hold to Talk Key"),
            SettingField("SHORTCUTS.AUTO_TOGGLE.modifier", "Auto Conversation Modifier", kind="choice", choices=("ctrl", "alt", "shift", "cmd")),
            SettingField("SHORTCUTS.AUTO_TOGGLE.key", "Auto Conversation Key"),
            SettingField("SHORTCUTS.ABORT.modifier", "Interrupt Modifier", kind="choice", choices=("ctrl", "alt", "shift", "cmd")),
            SettingField("SHORTCUTS.ABORT.key", "Interrupt Key"),
            SettingField("SHORTCUTS.MODE_TOGGLE.modifier", "Switch Mode Modifier", kind="choice", choices=("ctrl", "alt", "shift", "cmd")),
            SettingField("SHORTCUTS.MODE_TOGGLE.key", "Switch Mode Key"),
            SettingField("SHORTCUTS.WINDOW_TOGGLE.modifier", "Show/Hide Modifier", kind="choice", choices=("ctrl", "alt", "shift", "cmd")),
            SettingField("SHORTCUTS.WINDOW_TOGGLE.key", "Show/Hide Key"),
        ],
    ),
    (
        "Music",
        [
            SettingField("MUSIC.SEARCH_URL", "Search API URL", help="Empty uses the default API"),
            SettingField("MUSIC.URL_API", "Direct Link API URL", help="Empty uses the default API"),
            SettingField("MUSIC.URL_API_KEY", "Direct Link API Key", kind="password"),
            SettingField("MUSIC.OPUS_CATALOG_URL", "Opus Catalog URL"),
            SettingField("MUSIC.OPUS_STREAM_BASE", "Opus Stream Base"),
            SettingField("MUSIC.DEFAULT_QUALITY", "Default Quality", kind="choice", choices=("128k", "320k")),
            SettingField("MUSIC.VOLUME", "Music Volume", kind="int", help="Playback gain for online/local songs (0-200%)"),
        ],
    ),
]


def load_setting_values() -> dict[str, str]:
    """Read the current config into a string table (path -> display value)."""
    cfg = get_config()
    values: dict[str, str] = {}
    for _section, fields in SETTING_SECTIONS:
        for f in fields:
            if f.kind == "camera_device":
                backend = str(cfg.get_config("CAMERA.backend", "auto") or "auto")
                device = str(cfg.get_config("CAMERA.device", "") or "").strip()
                index = cfg.get_config("CAMERA.camera_index", 0)
                if backend == "picamera2":
                    values[f.path] = "picamera2"
                elif device:
                    values[f.path] = device
                else:
                    values[f.path] = str(index)
                continue
            if f.kind == "workspace":
                values[f.path] = str(cfg.get_config(f.path, "") or "")
                continue
            raw = cfg.get_config(f.path, "")
            if f.kind == "mcp_tools":
                values[f.path] = json.dumps(raw or [], ensure_ascii=False)
            elif f.kind == "bool":
                values[f.path] = "true" if bool(raw) else "false"
            elif raw is None:
                values[f.path] = ""
            else:
                values[f.path] = str(raw)
    return values


def parse_field_value(field: SettingField, text: str) -> Any:
    """Convert the input box string to a config value."""
    s = (text or "").strip()
    if field.kind == "int":
        if s == "":
            return 0
        return int(s)
    if field.kind == "float":
        if s == "":
            return 0.0
        return float(s)
    if field.kind == "bool":
        return s.lower() in ("1", "true", "yes", "on", "yes")
    if field.kind == "choice":
        if field.choices and s not in field.choices:
            # Still writes the user value; upper layers validate and show prompts
            return s
        if field.path.endswith(("opus_output_sample_rate", "frame_duration", "jpeg_max_side")):
            try:
                return int(s)
            except ValueError:
                return s
        return s
    return s


def save_settings(values: dict[str, str]) -> tuple[bool, str]:
    """Write multiple config values and persist them to disk.

    Returns:
        (ok, message)
    """
    cfg = get_config()
    updates: dict[str, Any] = {}
    wake_word_file: tuple[str, str] | None = None
    try:
        for _section, fields in SETTING_SECTIONS:
            for f in fields:
                if f.path not in values:
                    continue
                if f.kind == "camera_device":
                    from src.mcp.tools.camera.capture_backend import (
                        apply_device_selection,
                    )

                    updates.update(apply_device_selection(values[f.path]))
                    continue
                if f.kind == "workspace":
                    from src.utils.workspace import normalize_workspace

                    updates[f.path] = normalize_workspace(values[f.path])
                    continue
                if f.kind == "mcp_tools":
                    from src.mcp.tool_catalog import normalize_disabled

                    updates[f.path] = normalize_disabled(json.loads(values[f.path] or "[]"))
                    continue
                if f.kind in ("audio_input", "audio_output"):
                    prefix = "input" if f.kind == "audio_input" else "output"
                    try:
                        selected = json.loads(values[f.path]) if values[f.path] else None
                    except (TypeError, ValueError):
                        selected = None
                    if selected and selected.get("configured"):
                        updates[f.path] = selected.get("raw_name", "")
                        continue
                    if selected:
                        updates.update({
                            f"AUDIO_DEVICES.{prefix}_device_id": selected.get("index"),
                            f"AUDIO_DEVICES.{prefix}_device_name": selected.get("raw_name", ""),
                            f"AUDIO_DEVICES.{prefix}_sample_rate": selected.get("sample_rate"),
                            f"AUDIO_DEVICES.{prefix}_channels": min(
                                int(selected.get("channels", 0)), 1 if prefix == "input" else 2
                            ),
                        })
                    else:
                        updates.update({
                            f"AUDIO_DEVICES.{prefix}_device_id": None,
                            f"AUDIO_DEVICES.{prefix}_device_name": None,
                            f"AUDIO_DEVICES.{prefix}_sample_rate": None,
                            f"AUDIO_DEVICES.{prefix}_channels": None,
                        })
                    continue
                if f.kind == "wake_word":
                    wake_word = values[f.path].strip()
                    if not wake_word:
                        return False, "Wake word cannot be empty"
                    from src.audio_processing.keyword_converters import (
                        convert_wake_word,
                    )

                    keyword_line, language, model_path = convert_wake_word(wake_word)
                    updates["WAKE_WORD_OPTIONS.WAKE_WORD"] = wake_word
                    updates["WAKE_WORD_OPTIONS.WAKE_WORD_LANG"] = language
                    updates["WAKE_WORD_OPTIONS.MODEL_PATH"] = model_path
                    wake_word_file = (language, keyword_line)
                    continue
                updates[f.path] = parse_field_value(f, values[f.path])
        if not updates:
            return True, "No changes"
        ok = cfg.update_configs(updates)
        if not ok:
            return False, "Save failed (write error)"
        if wake_word_file:
            from src.utils.resource_finder import get_keywords_dir

            language, keyword_line = wake_word_file
            keywords_path = get_keywords_dir() / f"{language}_keywords.txt"
            keywords_path.write_text(keyword_line + "\n", encoding="utf-8")
        logger.info(f"TUI saved {len(updates)} config item(s)")
        return True, f"Saved {len(updates)} item(s)"
    except Exception as e:
        logger.error(f"TUI Failed to save config: {e}", exc_info=True)
        return False, f"Save failed: {e}"
