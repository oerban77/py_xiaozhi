"""Audio device manager.

Responsibilities:
- Device discovery and selection
- Configuration persistence (by device name, not ID)
- Device information queries
"""

from dataclasses import dataclass

from src.constants.constants import AudioConfig
from src.logging import get_logger
from src.utils.audio_utils import find_device_by_name, select_audio_device
from src.utils.config_manager import ConfigManager

logger = get_logger()


@dataclass
class DeviceConfig:
    """Device configuration data class"""

    input_device_id: int
    output_device_id: int
    input_sample_rate: int
    output_sample_rate: int
    input_channels: int
    output_channels: int
    input_frame_size: int
    output_frame_size: int


class AudioDeviceManager:
    """Audio device manager (stateless, pure logic)"""

    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager

    def load_or_detect_devices(self) -> DeviceConfig:
        """Load the configuration or auto-detect devices (match by name)

        Returns:
            DeviceConfig: the device configuration

        Raises:
            RuntimeError: no usable device could be found
        """
        audio_config = self.config.get_config("AUDIO_DEVICES", {}) or {}

        input_device_name = audio_config.get("input_device_name")
        output_device_name = audio_config.get("output_device_name")

        # 1. Try to find the devices by name
        input_info = None
        output_info = None

        if input_device_name:
            logger.info(f"Trying to find input device: {input_device_name}")
            input_info = find_device_by_name("input", input_device_name)
            if input_info:
                logger.info(f"✓ Input device found: {input_info['name']} (ID: {input_info['index']})")
            else:
                logger.warning(f"✗ Device '{input_device_name}' not found; will reselect")

        if output_device_name:
            logger.info(f"Trying to find output device: {output_device_name}")
            output_info = find_device_by_name("output", output_device_name)
            if output_info:
                logger.info(f"✓ Output device found: {output_info['name']} (ID: {output_info['index']})")
            else:
                logger.warning(f"✗ Device '{output_device_name}' not found; will reselect")

        # 2. If looking up by name failed, auto-select a new device
        if not input_info:
            logger.info("Auto-selecting input device...")
            input_info = select_audio_device("input")
            if not input_info:
                raise RuntimeError("Cannot find available input device")

        if not output_info:
            logger.info("Auto-selecting output device...")
            output_info = select_audio_device("output")
            if not output_info:
                raise RuntimeError("Cannot find available output device")

        # 3. The input is fixed to mono (required by the protocol + avoids array-driver latency);
        # the output keeps the device's channel count
        input_channels = 1
        output_channels = output_info["channels"]

        device_input_sample_rate = input_info["sample_rate"]
        device_output_sample_rate = output_info["sample_rate"]

        logger.info(
            f"Using input device: {input_info['name']} | "
            f"{device_input_sample_rate}Hz {input_channels}ch"
        )
        logger.info(
            f"Using output device: {output_info['name']} | "
            f"{device_output_sample_rate}Hz {output_channels}ch"
        )

        # 4. Save the device names (not the IDs) to the configuration
        if (
            input_device_name != input_info["name"]
            or output_device_name != output_info["name"]
        ):
            self.config.update_config("AUDIO_DEVICES.input_device_name", input_info["name"])
            self.config.update_config("AUDIO_DEVICES.input_sample_rate", device_input_sample_rate)
            self.config.update_config("AUDIO_DEVICES.input_channels", input_channels)
            self.config.update_config("AUDIO_DEVICES.output_device_name", output_info["name"])
            self.config.update_config("AUDIO_DEVICES.output_sample_rate", device_output_sample_rate)
            self.config.update_config("AUDIO_DEVICES.output_channels", output_channels)
            logger.info("Device configuration saved")

        return DeviceConfig(
            input_device_id=input_info["index"],
            output_device_id=output_info["index"],
            input_sample_rate=device_input_sample_rate,
            output_sample_rate=device_output_sample_rate,
            input_channels=input_channels,
            output_channels=output_channels,
            input_frame_size=int(
                device_input_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
            ),
            output_frame_size=int(
                device_output_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
            ),
        )
