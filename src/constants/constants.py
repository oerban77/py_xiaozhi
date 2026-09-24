import platform
from enum import Enum


class ListeningMode(str, Enum):
    """
    Listening mode.
    """

    REALTIME = "realtime"
    AUTO_STOP = "auto_stop"
    MANUAL = "manual"


class AbortReason(str, Enum):
    """
    Abort reason.
    """

    NONE = "none"
    WAKE_WORD_DETECTED = "wake_word_detected"
    USER_INTERRUPTION = "user_interruption"


class DeviceState(str, Enum):
    """
    Device state.
    """

    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"


class EventType:
    """
    Event types.
    """

    SCHEDULE_EVENT = "schedule_event"
    AUDIO_INPUT_READY_EVENT = "audio_input_ready_event"
    AUDIO_OUTPUT_READY_EVENT = "audio_output_ready_event"


def get_frame_duration() -> int:
    """Return the frame duration for this device.

    Reads from the initialized configuration first; when no configuration is
    available it auto-detects based on the device architecture.
    The configuration is not read at constants import time.

    Returns:
        int: Frame duration in milliseconds; one of 20/40/60
    """
    try:
        from src.utils.config_manager import get_config

        configured = get_config().get_config(
            "AUDIO_DEVICES.frame_duration"
        )
        if configured in [20, 40, 60]:
            return configured

        # Auto-detect when there is no configuration (kept for backward compatibility)
        machine = platform.machine().lower()
        arm_archs = ["arm", "aarch64", "armv7l", "armv6l"]
        is_arm_device = any(arch in machine for arch in arm_archs)

        if is_arm_device:
            # ARM devices (e.g. Raspberry Pi) use a larger frame duration to reduce CPU load
            return 60
        else:
            # Other devices (Windows/macOS/Linux x86) are fast enough to use low latency
            return 20

    except Exception:
        # If retrieval fails, return the default 20 ms (suitable for most modern devices)
        return 20


class AudioConfig:
    """
    Audio configuration (protocol layer), independent of the device layer (DeviceConfig).

    Defaults are declared in the class body and dynamically loaded from
    ConfigManager via reload(). reload() is not forced at import time to avoid
    side effects in the constants module.
    """

    # Fixed server-side protocol values (do not change with configuration)
    INPUT_SAMPLE_RATE = 16000  # Protocol requirement: 16 kHz input
    CHANNELS = 1  # Protocol requirement: mono

    # Dynamic values below; re-read from configuration on reload()
    OUTPUT_SAMPLE_RATE: int = 24000
    FRAME_DURATION: int = 20
    INPUT_FRAME_SIZE: int = 320

    @classmethod
    def reload(cls):
        """Reload protocol audio parameters from ConfigManager (supports runtime hot reload).

        After the Settings UI changes opus_output_sample_rate / frame_duration,
        call this method so the new values take effect on the next
        initialize/reload_devices.
        """
        try:
            from src.utils.config_manager import get_config

            config = get_config()
            cls.OUTPUT_SAMPLE_RATE = config.get_config(
                "AUDIO_DEVICES.opus_output_sample_rate", 24000
            )
        except Exception:
            # Keep the current/default values when the configuration is unavailable
            pass
        cls.FRAME_DURATION = get_frame_duration()
        cls.INPUT_FRAME_SIZE = int(cls.INPUT_SAMPLE_RATE * (cls.FRAME_DURATION / 1000))
