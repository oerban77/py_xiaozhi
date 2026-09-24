"""TUI Settings: config field definitions and read/write (connects to ConfigManager + CONFIG_CHANGED)."""

from __future__ import annotations

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
    kind: str = "str"  # str | int | bool | choice
    choices: tuple[str, ...] = ()
    help: str = ""


# First release: System / Audio / Camera / Wake Word
SETTING_SECTIONS: list[tuple[str, list[SettingField]]] = [
    (
        "System",
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
        ],
    ),
    (
        "Audio",
        [
            SettingField(
                "AUDIO_DEVICES.input_device_name",
                "Input Device Name",
                help="Match the microphone by name (the ID changes after hot-plugging)",
            ),
            SettingField(
                "AUDIO_DEVICES.output_device_name",
                "Output Device Name",
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
                "CAMERA.backend",
                "Capture Backend",
                kind="choice",
                choices=("auto", "opencv", "picamera2"),
                help="auto tries OpenCV first, then Pi CSI on failure",
            ),
            SettingField(
                "CAMERA.device",
                "Device Path",
                help="e.g. /dev/video0; when non-empty it takes precedence over index",
            ),
            SettingField(
                "CAMERA.camera_index",
                "device index",
                kind="int",
                help="OpenCV numeric index",
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
                help="e.g.: Hello Xiaozhi",
            ),
            SettingField(
                "WAKE_WORD_OPTIONS.WAKE_WORD_LANG",
                "Language",
                kind="choice",
                choices=("zh", "en"),
            ),
        ],
    ),
]


def load_setting_values() -> dict[str, str]:
    """Read the current config into a string table (path -> display value)."""
    cfg = get_config()
    values: dict[str, str] = {}
    for _section, fields in SETTING_SECTIONS:
        for f in fields:
            raw = cfg.get_config(f.path, "")
            if f.kind == "bool":
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
    if field.kind == "bool":
        return s.lower() in ("1", "true", "yes", "on", "yes")
    if field.kind == "choice":
        if field.choices and s not in field.choices:
            # Still writes the user value; upper layers validate and show prompts
            return s
        if field.path.endswith("opus_output_sample_rate") or field.path.endswith(
            "frame_duration"
        ):
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
    try:
        for _section, fields in SETTING_SECTIONS:
            for f in fields:
                if f.path not in values:
                    continue
                updates[f.path] = parse_field_value(f, values[f.path])
        if not updates:
            return True, "No changes"
        ok = cfg.update_configs(updates)
        if not ok:
            return False, "Save failed (write error)"
        logger.info(f"TUI saved {len(updates)} config item(s)")
        return True, f"Saved {len(updates)} item(s)"
    except Exception as e:
        logger.error(f"TUI Failed to save config: {e}", exc_info=True)
        return False, f"Save failed: {e}"
