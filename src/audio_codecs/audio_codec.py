import asyncio
import threading
import time
from collections.abc import Callable
from typing import Protocol

import numpy as np

from src.audio_codecs.audio_buffer import PcmFifo
from src.audio_codecs.audio_converter import AudioConverter
from src.audio_codecs.opus_codec import OpusCodec, parse_opus_toc
from src.audio_codecs.stream_manager import AudioStreamManager
from src.constants.constants import AudioConfig
from src.logging import get_logger
from src.utils.audio_device import AudioDeviceManager, DeviceConfig
from src.utils.config_manager import get_config

logger = get_logger()

# Mixing gain for music when TTS is present (ducking), and its hold duration (in 20ms chunks)
_MUSIC_DUCK_GAIN = 0.35
_DUCK_HOLD_CHUNKS = 10
# Backlog target (seconds) for the music write side: smaller means pause/interruption feels more responsive, larger means stronger jitter resistance
_MUSIC_BACKLOG_TARGET_S = 0.30
# TTS / music FIFO capacity (seconds); drop the oldest when exceeded
_TTS_FIFO_MAX_S = 10.0
_MUSIC_FIFO_MAX_S = 2.0


class AudioListener(Protocol):
    """Audio listener protocol"""

    def on_audio_data(self, audio_data: np.ndarray) -> None:
        """Receive audio data

        Args:
            audio_data: float32 audiodata
        """
        ...


class AudioCodec:
    """Audio codec - coordinator mode

    Combine the components and coordinate the data flow

    data flow:
    - input: device(float32) → downmix + resample(float32) → Opus encode(float32→bytes) → network
    - output: network → Opus decode(bytes→float32) → resample + upmix(float32) → device(float32)
    """

    def __init__(self):
        """Initialize the audio codec"""
        # Refresh protocol config (so changes in the Settings UI take effect)
        AudioConfig.reload()

        # Components (dependency injection)
        self.device_manager = AudioDeviceManager(get_config())
        self.opus_codec = OpusCodec(
            input_sample_rate=AudioConfig.INPUT_SAMPLE_RATE,
            output_sample_rate=AudioConfig.OUTPUT_SAMPLE_RATE,
            channels=AudioConfig.CHANNELS,
        )
        self.converter = AudioConverter()
        self.stream_manager = None

        # Split TTS and music into separate FIFOs and mix them in the output callback (so neither blocks the other)
        self._tts_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _TTS_FIFO_MAX_S)
        )
        self._music_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _MUSIC_FIFO_MAX_S)
        )
        self._mix_chunk = int(AudioConfig.OUTPUT_SAMPLE_RATE * 0.02)  # 20ms
        self._duck_hold = 0

        # listeners (thread-safe)
        self._encoded_callback: Callable | None = None
        self._audio_listeners: list[AudioListener] = []
        self._listeners_lock = threading.Lock()

        # Device configuration (filled after initialization)
        self.device_config: DeviceConfig | None = None

        # AEC (self far: the final PCM from the play callback is used as the reference); created during initialize according to the config
        self._aec = None

        # Status flags
        self._is_closing = False
        self._closed = False
        self._server_opus_logged = False
        self._last_output_status_log = 0.0

    async def initialize(self):
        """Initialize all components

        Steps:
        1. Load / detect devices
        2. Refresh protocol config and initialize Opus
        3. Configure the format conversion pipeline
        4. Create the audio stream
        5. Start the audio stream
        """
        try:
            # 1. load/detectdevice
            self.device_config = self.device_manager.load_or_detect_devices()

            # 2. Refresh protocol config and initialize Opus
            AudioConfig.reload()
            self.opus_codec.close()
            self.opus_codec = OpusCodec(
                input_sample_rate=AudioConfig.INPUT_SAMPLE_RATE,
                output_sample_rate=AudioConfig.OUTPUT_SAMPLE_RATE,
                channels=AudioConfig.CHANNELS,
            )
            self.opus_codec.initialize()

            # 3. Configure the format conversion pipeline
            self._configure_pipeline()

            # 4. Create AEC according to the config (self far); bypass automatically on failure
            self._setup_aec()

            # 5. Create the audio stream
            self.stream_manager = AudioStreamManager(self.device_config)
            self.stream_manager.create_streams(
                input_callback=self._input_callback,
                output_callback=self._output_callback,
            )

            # 6. Start the audio stream
            self.stream_manager.start()

            logger.info("AudioCodec initialized")

        except Exception as e:
            logger.error(f"Failed to initialize audio device: {e}", exc_info=True)
            await self.close()
            raise

    def _input_callback(self, indata, frames, time_info, status):
        """Input callback: device → encode → send

        data flow: multichannel / high sample rate → downmix → resample → Opus encode → network

        Args:
            indata: float32 audio data, shape (frames, channels)
            frames: number of frames
            time_info: timing information
            status: status flags
        """
        if status and "overflow" not in str(status).lower():
            logger.warning(f"Input stream status: {status}")

        if self._is_closing:
            return

        try:
            # 1. Format conversion (downmix + resample)
            # Keep the (frames, channels) shape of indata so downmix_to_mono downmixes correctly
            audio_converted = self.converter.convert_input(
                indata, AudioConfig.INPUT_FRAME_SIZE
            )
            if audio_converted is None:
                return  # Insufficient data; wait for the next frame

            # 1.5 AEC: remove echo using the far reference extracted from the play callback (returned as-is when bypassed)
            if self._aec is not None and self._aec.active:
                audio_converted = self._aec.process_near(audio_converted)

            # 2. Opus encoding (float32 input)
            if self._encoded_callback:
                try:
                    opus_data = self.opus_codec.encode(
                        audio_converted, AudioConfig.INPUT_FRAME_SIZE
                    )
                    self._encoded_callback(opus_data)
                except Exception as e:
                    logger.warning(f"Encoding failed: {e}", exc_info=True)

            # 3. Notification listeners (thread-safe)
            with self._listeners_lock:
                for listener in self._audio_listeners:
                    try:
                        listener.on_audio_data(audio_converted.copy())
                    except Exception as e:
                        logger.warning(f"Listener processing failed: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Input callback error: {e}", exc_info=True)

    def _output_callback(self, outdata, frames, time_info, status):
        """Output callback: decode → convert → play

        data flow: queue → resample → upmix → device

        The loop takes chunks from the queue and feeds them to convert_output until the
        resampler's internal buffer accumulates enough frames or the queue is exhausted.
        This avoids glitches when the sample rates are not integer multiples
        (e.g. 16kHz → 44100Hz) or the server frame duration does not match.

        Args:
            outdata: float32 output buffer, shape (frames, channels)
            frames: number of frames
            time_info: timing information
            status: status flags
        """
        if status:
            # Rate limiting: logging inside the callback (file IO) itself worsens underruns, so at most one entry every 2 seconds
            now = time.monotonic()
            if now - self._last_output_status_log > 2.0:
                self._last_output_status_log = now
                logger.warning(f"Output stream status: {status}")

        try:
            audio_converted = None

            while audio_converted is None:
                audio_data = self._pull_mixed(self._mix_chunk)
                if audio_data is None:
                    break
                audio_converted = self.converter.convert_output(audio_data, frames)

            if audio_converted is None:
                audio_converted = self.converter.drain_output_buffer(frames)

            if audio_converted is None or len(audio_converted) < frames:
                outdata.fill(0.0)
                if audio_converted is not None and len(audio_converted) > 0:
                    outdata[: len(audio_converted)] = audio_converted
            else:
                outdata[:] = audio_converted[:frames]

            # AEC far: the final PCM actually written to the device (TTS + music mixed, silence included to stay continuous)
            if self._aec is not None and self._aec.active:
                self._aec.feed_far(outdata)

        except Exception as e:
            logger.error(f"Output callback error: {e}", exc_info=True)
            outdata.fill(0.0)

    def _configure_pipeline(self):
        """Configure the format conversion pipeline (device ↔ protocol).

        Set up the input/output conversion chains based on the device's native parameters and the parameters required by the protocol.
        input: device(f32, device_rate, device_ch) → protocol(f32, 16kHz, 1ch)
        output: protocol(f32, opus_out_rate, 1ch) → device(f32, device_rate, device_ch)
        """
        self.converter.setup_input_converter(
            from_rate=self.device_config.input_sample_rate,
            to_rate=AudioConfig.INPUT_SAMPLE_RATE,
            from_channels=self.device_config.input_channels,
            to_channels=1,
        )
        self.converter.setup_output_converter(
            from_rate=AudioConfig.OUTPUT_SAMPLE_RATE,
            to_rate=self.device_config.output_sample_rate,
            from_channels=1,
            to_channels=self.device_config.output_channels,
        )

        # The protocol output sample rate may change when the config is hot-reloaded; rebuild the FIFOs and the mix chunk accordingly
        # (the audio stream is already stopped at this point, so there are no concurrent reads)
        self._tts_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _TTS_FIFO_MAX_S)
        )
        self._music_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _MUSIC_FIFO_MAX_S)
        )
        self._mix_chunk = int(AudioConfig.OUTPUT_SAMPLE_RATE * 0.02)
        self._duck_hold = 0

    def _pull_mixed(self, n: int) -> np.ndarray | None:
        """Output callback thread: fetches n samples from each TTS/music FIFO and mixes them.

        Rules:
        - Both streams empty → None (the caller enters the silence / underrun path)
        - When TTS is present, music is ducked by _MUSIC_DUCK_GAIN, and the ducking is held for a few extra frames after TTS ends
          Keep a few blocks of frame gap (to avoid gain flutter in ducking)
        """
        tts = self._tts_fifo.pull(n)
        music = self._music_fifo.pull(n)

        if tts is None and music is None:
            return None

        if tts is not None:
            self._duck_hold = _DUCK_HOLD_CHUNKS
        elif self._duck_hold > 0:
            self._duck_hold -= 1

        if music is None:
            return tts
        if tts is None:
            if self._duck_hold > 0:
                music *= _MUSIC_DUCK_GAIN
            return music
        return np.clip(tts + music * _MUSIC_DUCK_GAIN, -1.0, 1.0)

    def _setup_aec(self):
        """Create / rebuild the AEC engine according to AEC_OPTIONS.ENABLED (self far).

        The far sample rate may change after a device hot-reload, so the engine must be
        rebuilt together with device_config; a creation failure does not raise — the engine
        bypasses itself (active=False).
        """
        if self._aec is not None:
            self._aec.close()
            self._aec = None

        try:
            config = get_config()
            if not bool(config.get_config("AEC_OPTIONS.ENABLED", False)):
                logger.info("AEC disabled (AEC_OPTIONS.ENABLED=false)")
                return

            from src.audio_processing.aec_engine import AecEngine

            frame_delay = config.get_config("AEC_OPTIONS.FRAME_DELAY", 3)
            self._aec = AecEngine(
                near_rate=AudioConfig.INPUT_SAMPLE_RATE,
                far_rate=self.device_config.output_sample_rate,
                # FRAME_DELAY is in protocol frames; convert to milliseconds and add it to the base output delay
                delay_ms=40 + int(frame_delay) * AudioConfig.FRAME_DURATION,
                enable_preprocess=bool(
                    config.get_config("AEC_OPTIONS.ENABLE_PREPROCESS", True)
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to create AEC engine; bypassed: {e}", exc_info=True)
            self._aec = None

    # === Public interface (keep compatibility) ===

    @property
    def aec_active(self) -> bool:
        """Whether the AEC engine is present and working (the music parallel strategy depends on this)."""
        aec = self._aec
        return bool(aec is not None and aec.active)

    def set_encoded_callback(self, callback: Callable[[bytes], None]):
        """Set the encoding callback

        Args:
            callback: Callback function that receives Opus encoded data
        """
        self._encoded_callback = callback
        if callback:
            logger.info("Encoded audio callback set")
        else:
            logger.info("Encoded audio callback cleared")

    def add_audio_listener(self, listener: AudioListener):
        """Add audio listener (thread-safe)

        Args:
            listener: listener object implementing the AudioListener protocol
        """
        with self._listeners_lock:
            if listener not in self._audio_listeners:
                self._audio_listeners.append(listener)
                logger.info(f"Audio listener added: {listener.__class__.__name__}")

    def remove_audio_listener(self, listener: AudioListener):
        """Remove audio listener (thread-safe)

        Args:
            listener: listener object to remove
        """
        with self._listeners_lock:
            if listener in self._audio_listeners:
                self._audio_listeners.remove(listener)
                logger.info(f"Audio listener removed: {listener.__class__.__name__}")

    async def write_audio(self, opus_data: bytes):
        """Decode and play audio (Opus → speaker)

        Automatically detect the frame duration from the Opus TOC bytes, without relying on client configuration.

        Args:
            opus_data: Opus encoded data
        """
        try:
            toc_info = parse_opus_toc(opus_data)
            if toc_info is None:
                return

            if not self._server_opus_logged:
                self._server_opus_logged = True
                logger.info(
                    f"Server Opus params: "
                    f"{toc_info['mode']} {toc_info['bandwidth_hz']} | "
                    f"frame duration {toc_info['duration_ms']}ms "
                    f"({toc_info['frame_ms']}ms x {toc_info['num_frames']})"
                )

            frame_size = int(
                AudioConfig.OUTPUT_SAMPLE_RATE * toc_info["duration_ms"] / 1000
            )
            audio_float32 = self.opus_codec.decode(opus_data, frame_size)

            self._tts_fifo.push(audio_float32)

        except Exception as e:
            logger.warning(f"Audio write failed: {e}", exc_info=True)

    async def write_pcm_direct(self, pcm_float32: np.ndarray):
        """Write music PCM (float32, used by MusicPlayer) with backlog backpressure.

        After writing, if the music buffer exceeds the target level, wait for playback to
        consume it — this is the only clock source on the music path (the decoder clock
        becomes inaccurate after a pause, so it cannot be used as a reference).
        Backpressure gives up after 2 seconds as a safety net, and the FIFO drops the
        oldest samples when full so it never grows without bound.
        """
        self._music_fifo.push(pcm_float32)

        target = int(AudioConfig.OUTPUT_SAMPLE_RATE * _MUSIC_BACKLOG_TARGET_S)
        for _ in range(100):
            if self._is_closing or self._music_fifo.size <= target:
                break
            await asyncio.sleep(0.02)

    async def clear_audio_queue(self):
        """Clear the TTS playback queue (used on interrupt / abort; the music queue is unaffected)."""
        self._server_opus_logged = False
        self.converter.clear_output_buffer()
        count = self._tts_fifo.clear()
        if count > 0:
            logger.info(f"TTS queue cleared; discarded {count} samples")

    async def clear_music_queue(self):
        """Clear the music playback queue (used on stop / skip; the TTS queue is unaffected)."""
        count = self._music_fifo.clear()
        if count > 0:
            logger.info(f"Music queue cleared; discarded {count} samples")

    async def reinitialize_stream(self, is_input: bool = True):
        """Rebuild the audio stream (supports hot-plugging)

        Args:
            is_input: True=Input stream, False=Output stream

        Returns:
            bool: Whether successful
        """
        if not self.stream_manager:
            return False

        if is_input:
            return self.stream_manager.reinitialize_stream(
                is_input=True, input_callback=self._input_callback
            )
        else:
            return self.stream_manager.reinitialize_stream(
                is_input=False, output_callback=self._output_callback
            )

    def stop_streams_for_enumeration(self) -> None:
        """Stop the sounddevice streams held by this codec; call before hot-plug enumeration.

        Afterwards you must call ``reload_devices()`` or ``create_streams`` yourself,
        otherwise there will be no capture / playback.
        """
        if self.stream_manager:
            self.stream_manager.stop()
            logger.info("AudioCodec: audio stream stopped (for device enumeration)")

    async def reload_devices(self, *, reenumerate: bool = True):
        """Hot-reload audio device configuration

        Steps:
        1. Stop the current audio stream
        2. (Optional) Reinitialize PortAudio and re-enumerate (helps late-connected Bluetooth devices appear)
        3. Reload device config + protocol config
        4. Rebuild the format converter + Opus codec
        5. Recreate and restart the audio stream

        Args:
            reenumerate: whether to force-refresh the PortAudio device table after stopping the streams

        Returns:
            bool: Whether successful
        """
        logger.info("AudioCodec: hot-reloading audio device...")

        try:
            # 1. Stop the current stream (must happen before the PortAudio reinit)
            if self.stream_manager:
                self.stream_manager.stop()
                logger.debug("AudioCodec: current audio stream stopped")

            # 2. Hot-plug: rebuild the PortAudio context, then match by name
            if reenumerate:
                from src.utils.audio_utils import refresh_portaudio_devices

                refresh_portaudio_devices(reinitialize=True)

            # 3. Reload the device config
            self.device_manager.config.reload_config()
            self.device_config = self.device_manager.load_or_detect_devices()
            logger.info(
                "AudioCodec: new device config - input ID: "
                f"{self.device_config.input_device_id}, output ID: "
                f"{self.device_config.output_device_id}"
            )

            # 4. Refresh the protocol config and rebuild the Opus codec
            AudioConfig.reload()
            self.opus_codec.close()
            self.opus_codec = OpusCodec(
                input_sample_rate=AudioConfig.INPUT_SAMPLE_RATE,
                output_sample_rate=AudioConfig.OUTPUT_SAMPLE_RATE,
                channels=AudioConfig.CHANNELS,
            )
            self.opus_codec.initialize()

            # 5. Rebuild the format converter
            self.converter.clear_buffers()
            self._configure_pipeline()

            # 5.5 Rebuild AEC (the far sample rate follows the new output device; filter state is reset)
            self._setup_aec()

            # 6. Recreate the audio stream
            self.stream_manager = AudioStreamManager(self.device_config)
            self.stream_manager.create_streams(
                input_callback=self._input_callback,
                output_callback=self._output_callback,
            )

            # 7. Start the audio stream
            self.stream_manager.start()

            logger.info("AudioCodec: audio device hot-reload complete")
            return True

        except Exception as e:
            logger.error(f"AudioCodec: audio device hot-reload failed: {e}", exc_info=True)
            return False

    async def close(self):
        """Close the audio codec"""
        self._is_closing = True

        try:
            # 1. Stop the audio stream
            if self.stream_manager:
                self.stream_manager.stop()

            # 2. Clear the queue
            await self.clear_audio_queue()
            await self.clear_music_queue()

            # 2.5 Release AEC (must happen after the stream stops)
            if self._aec is not None:
                self._aec.close()
                self._aec = None

            # 3. Release the converter (including the soxr resampler)
            self.converter.close()

            # 4. Release Opus
            self.opus_codec.close()

            # 5. Clean up listeners
            with self._listeners_lock:
                self._audio_listeners.clear()

            logger.info("AudioCodec closed")
            self._closed = True

        except Exception as e:
            logger.error(f"Failed to close audio codec: {e}", exc_info=True)
        finally:
            self._is_closing = False

    def __del__(self):
        """Destructor - perform synchronous cleanup"""
        # If already closed or closing, skip
        if getattr(self, "_closed", False) or getattr(self, "_is_closing", False):
            return

        logger.warning("AudioCodec not closed properly; running emergency cleanup (prefer async close())")

        try:
            # 1. Stop the audio stream (synchronously)
            if self.stream_manager:
                self.stream_manager.stop()

            # 2. Clear queue (synchronous version)
            count = self._tts_fifo.clear() + self._music_fifo.clear()
            if count > 0:
                logger.debug(f"Destructor cleared {count} audio samples")

            # 3. Release the converter (synchronous; includes the soxr resampler)
            if self.converter:
                self.converter.close()

            # 4. Release Opus (synchronously)
            if self.opus_codec:
                self.opus_codec.close()

            # 5. Clean up listeners (synchronously)
            try:
                with self._listeners_lock:
                    self._audio_listeners.clear()
            except Exception as e:
                logger.warning(
                    f"Failed to clean audio listeners (lock may be broken): {e}", exc_info=True
                )

            logger.debug("AudioCodec destructor cleanup complete")

        except Exception as e:
            logger.error(f"Destructor cleanup failed: {e}", exc_info=True)
