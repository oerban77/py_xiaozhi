from collections import deque

import numpy as np
import soxr

from src.logging import get_logger
from src.utils.audio_utils import downmix_to_mono, upmix_mono_to_channels

logger = get_logger()


class AudioConverter:
    """Audio format converter

    Handles sample-rate conversion and channel conversion, and internally maintains
    buffers to accumulate enough samples for the target frame size.
    """

    def __init__(self):
        """Initialize the converter"""
        self.input_resampler = None
        self.output_resampler = None
        self._input_buffer = deque()
        self._output_buffer = deque()
        self.needs_input_downmix = False
        self.needs_output_upmix = False
        self.input_channels = 1
        self.output_channels = 1

    def setup_input_converter(
        self, from_rate: int, to_rate: int, from_channels: int, to_channels: int = 1
    ):
        """Configure the input conversion chain (device → protocol)

        Args:
            from_rate: device sample rate
            to_rate: protocol sample rate (usually 16kHz)
            from_channels: device channel count
            to_channels: protocol channel count (usually 1)
        """
        self.input_channels = from_channels
        self.needs_input_downmix = from_channels > to_channels

        if self.needs_input_downmix:
            logger.info(f"Input channel downmix: {from_channels}ch → {to_channels}ch")

        if from_rate != to_rate:
            self.input_resampler = soxr.ResampleStream(
                from_rate,
                to_rate,
                num_channels=to_channels,  # Channel count after downmix
                dtype="float32",
                quality="QQ",  # Fast quality (suitable for real-time processing)
            )
            logger.info(f"Input resample: {from_rate}Hz → {to_rate}Hz")

    def setup_output_converter(
        self, from_rate: int, to_rate: int, from_channels: int = 1, to_channels: int = 2
    ):
        """Configure the output conversion chain (protocol → device)

        Args:
            from_rate: protocol sample rate (usually 24kHz)
            to_rate: device sample rate
            from_channels: protocol channel count (usually 1)
            to_channels: device channel count
        """
        self.output_channels = to_channels
        self.needs_output_upmix = to_channels > from_channels

        if from_rate != to_rate:
            self.output_resampler = soxr.ResampleStream(
                from_rate,
                to_rate,
                num_channels=from_channels,  # Channel count before upmix
                dtype="float32",
                quality="QQ",
            )
            logger.info(f"Output resample: {from_rate}Hz → {to_rate}Hz")

        if self.needs_output_upmix:
            logger.info(f"Output channel upmix: {from_channels}ch → {to_channels}ch")

    def convert_input(
        self, audio: np.ndarray, target_size: int
    ) -> np.ndarray | None:
        """Input conversion: multichannel / high sample rate → mono / 16kHz

        Args:
            audio: float32 audio data
            target_size: target sample count

        Returns:
            converted float32 data, or None (not enough data)
        """
        # 1. Downmix (using audio_utils)
        if self.needs_input_downmix:
            audio = downmix_to_mono(audio, keepdims=False)
        else:
            audio = audio.flatten()

        # 2. Resample
        if self.input_resampler:
            resampled = self.input_resampler.resample_chunk(audio, last=False)
            if len(resampled) > 0:
                self._input_buffer.extend(resampled)

            # Accumulate to the target size
            if len(self._input_buffer) < target_size:
                return None

            # Take out one frame
            frame_data = [self._input_buffer.popleft() for _ in range(target_size)]
            return np.array(frame_data, dtype=np.float32)

        return audio

    def convert_output(
        self, audio: np.ndarray, target_frames: int
    ) -> np.ndarray | None:
        """Output conversion: mono / 24kHz → multichannel / high sample rate

        Args:
            audio: float32 audiodata
            target_frames: target frame count

        Returns:
            converted float32 data
        """
        # 1. Resample
        if self.output_resampler:
            resampled = self.output_resampler.resample_chunk(audio, last=False)
            if len(resampled) > 0:
                self._output_buffer.extend(resampled)

            # Take out the target frame count
            if len(self._output_buffer) < target_frames:
                return None

            frame_data = [self._output_buffer.popleft() for _ in range(target_frames)]
            audio = np.array(frame_data, dtype=np.float32)

        # 2. Upmix (using audio_utils)
        if self.needs_output_upmix:
            audio = upmix_mono_to_channels(audio, self.output_channels)
        else:
            audio = audio.reshape(-1, 1)

        return audio

    def drain_output_buffer(self, target_frames: int) -> np.ndarray | None:
        """Drain the remaining data from the resampler buffer (with upmix).

        Used when the queue is exhausted but the buffer is a few samples short,
        to avoid a whole frame of silence.
        """
        available = min(len(self._output_buffer), target_frames)
        if available == 0:
            return None

        frame_data = [self._output_buffer.popleft() for _ in range(available)]
        audio = np.array(frame_data, dtype=np.float32)

        if self.needs_output_upmix:
            audio = upmix_mono_to_channels(audio, self.output_channels)
        else:
            audio = audio.reshape(-1, 1)

        return audio

    def clear_output_buffer(self):
        """Clear only the output buffer (used to prevent echo when TTS stops; the input pipeline is unaffected)."""
        self._output_buffer.clear()

    def clear_buffers(self):
        """Clear the buffer"""
        self._input_buffer.clear()
        self._output_buffer.clear()
        logger.debug("Audio converter buffer cleared")

    def close(self):
        """Release the soxr resamplers to prevent nanobind C++ object leaks."""
        self.clear_buffers()
        self.input_resampler = None
        self.output_resampler = None
