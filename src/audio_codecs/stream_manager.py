"""Audio stream manager.

Responsibilities: sounddevice stream creation and lifecycle management
"""

from collections.abc import Callable

import numpy as np
import sounddevice as sd

from src.logging import get_logger
from src.utils.audio_device import DeviceConfig
from src.utils.audio_utils import ALSAErrorSuppressor

logger = get_logger()


class AudioStreamManager:
    """Audio stream manager (sounddevice wrapper)

    Creates and manages the lifecycle of audio input/output streams.
    """

    def __init__(self, device_config: DeviceConfig):
        """Initialize the stream manager

        Args:
            device_config: device configuration
        """
        self.device_config = device_config
        self.input_stream = None
        self.output_stream = None
        self._stopped = False

    def create_streams(
        self, input_callback: Callable, output_callback: Callable
    ) -> None:
        """Create a duplex audio stream

        Args:
            input_callback: inputCallback function
            output_callback: outputCallback function

        Raises:
            Exception: stream creation failed
        """
        # Allow create() after stop() (hot-reload path)
        self._stopped = False
        try:
            # Use ALSAErrorSuppressor to suppress ALSA warnings on Linux
            with ALSAErrorSuppressor():
                # Input stream
                self.input_stream = sd.InputStream(
                    device=self.device_config.input_device_id,
                    samplerate=self.device_config.input_sample_rate,
                    channels=self.device_config.input_channels,
                    dtype=np.float32,  # Unified float32
                    blocksize=self.device_config.input_frame_size,
                    callback=input_callback,
                    latency="low",
                )

                # Output stream
                self.output_stream = sd.OutputStream(
                    device=self.device_config.output_device_id,
                    samplerate=self.device_config.output_sample_rate,
                    channels=self.device_config.output_channels,
                    dtype=np.float32,  # Unified float32
                    blocksize=0,
                    callback=output_callback,
                    latency="low",
                )

            logger.info(
                f"Audio stream created | "
                f"input: {self.device_config.input_sample_rate}Hz "
                f"{self.device_config.input_channels}ch | "
                f"output: {self.device_config.output_sample_rate}Hz "
                f"{self.device_config.output_channels}ch"
            )
        except Exception as e:
            logger.error(f"Failed to create audio stream: {e}", exc_info=True)
            raise

    def start(self) -> None:
        """Start the audio stream

        Raises:
            Exception: startup failed
        """
        try:
            if self.input_stream:
                self.input_stream.start()
            if self.output_stream:
                self.output_stream.start()
            logger.info("Audio stream started")
        except Exception as e:
            logger.error(f"Failed to start audio stream: {e}", exc_info=True)
            raise

    def stop(self) -> None:
        """Stop the audio stream; idempotent and safe to call repeatedly"""
        if getattr(self, "_stopped", False):
            return
        self._stopped = True

        try:
            if self.input_stream:
                self.input_stream.stop()
                self.input_stream.close()
                self.input_stream = None

            if self.output_stream:
                self.output_stream.stop()
                self.output_stream.close()
                self.output_stream = None

            logger.info("Audio stream stopped")
        except Exception as e:
            logger.error(f"Failed to stop audio stream: {e}", exc_info=True)

    def is_active(self) -> bool:
        """Whether it still holds an unclosed input/output stream."""
        return bool(self.input_stream or self.output_stream)

    def reinitialize_stream(
        self,
        is_input: bool,
        input_callback: Callable = None,
        output_callback: Callable = None,
    ) -> bool:
        """Rebuild the audio stream (supports hot-plugging)

        Args:
            is_input: True=Input stream, False=Output stream
            input_callback: input callback function (only needed when rebuilding the input stream)
            output_callback: output callback function (only needed when rebuilding the output stream)

        Returns:
            bool: Whether successful
        """
        try:
            # Use ALSAErrorSuppressor to suppress ALSA warnings on Linux
            with ALSAErrorSuppressor():
                if is_input and input_callback:
                    # Rebuild the input stream
                    if self.input_stream:
                        self.input_stream.stop()
                        self.input_stream.close()

                    self.input_stream = sd.InputStream(
                        device=self.device_config.input_device_id,
                        samplerate=self.device_config.input_sample_rate,
                        channels=self.device_config.input_channels,
                        dtype=np.float32,
                        blocksize=self.device_config.input_frame_size,
                        callback=input_callback,
                        latency="low",
                    )
                    self.input_stream.start()
                    logger.info("Input stream reinitialized")
                    return True

                elif not is_input and output_callback:
                    # Rebuild the output stream
                    if self.output_stream:
                        self.output_stream.stop()
                        self.output_stream.close()

                    self.output_stream = sd.OutputStream(
                        device=self.device_config.output_device_id,
                        samplerate=self.device_config.output_sample_rate,
                        channels=self.device_config.output_channels,
                        dtype=np.float32,
                        blocksize=0,
                        callback=output_callback,
                        latency="low",
                    )
                    self.output_stream.start()
                    logger.info("Output stream reinitialized")
                    return True

            return False

        except Exception as e:
            stream_type = "input" if is_input else "output"
            logger.error(f"{stream_type} stream rebuild failed: {e}", exc_info=True)
            return False
