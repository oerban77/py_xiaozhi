"""Audio plugin.

Handles audio capture, encoding, playback, and sending.
The AudioCodec is published via Events.AUDIO_CODEC_CHANGED instead of connecting to MusicPlayer directly.
"""

import asyncio
import os
from typing import TYPE_CHECKING

from src.audio_codecs.audio_codec import AudioCodec
from src.logging import get_logger
from src.plugins.base import Plugin

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext

logger = get_logger()

MAX_CONCURRENT_AUDIO_SENDS = 4


class AudioPlugin(Plugin):
    name = "audio"
    priority = 10  # Highest priority; other plugins depend on audio_codec

    def __init__(self) -> None:
        super().__init__()
        self.codec: AudioCodec | None = None
        self._send_sem = asyncio.Semaphore(MAX_CONCURRENT_AUDIO_SENDS)
        self._in_silence_period = False

    async def setup(self, ctx: "PluginContext", cmd: "PluginCommands") -> None:
        await super().setup(ctx, cmd)

        if os.getenv("XIAOZHI_DISABLE_AUDIO") == "1":
            logger.warning("XIAOZHI_DISABLE_AUDIO=1; audio plugin running in disabled mode")
            return

        try:
            self.codec = AudioCodec()
            await self.codec.initialize()
            self.codec.set_encoded_callback(self._on_encoded_audio)

            from src.core.event_bus import Events

            ctx.event_bus.on(Events.CONFIG_CHANGED, self._on_config_changed)
            ctx.event_bus.on(
                Events.AUDIO_DEVICES_REFRESH_REQUEST, self._on_devices_refresh_request
            )
            # The codec is published in start(): MusicPlayer subscribes to the EventBus during McpPlugin.setup

        except Exception as e:
            logger.error(f"Audio plugin init failed: {e}", exc_info=True)
            self.codec = None
            self.mark_failed()
            raise

    async def start(self) -> None:
        await super().start()
        if self.codec and not self.failed:
            await self._publish_audio_codec(self.codec)

    async def _publish_audio_codec(self, codec) -> None:
        """Publish the AudioCodec instance (or None) to subscribers such as MusicPlayer."""
        if not self._ctx or not self._ctx.event_bus:
            logger.warning(
                "Could not publish AUDIO_CODEC_CHANGED: PluginContext / EventBus not ready"
            )
            return
        from src.core.event_bus import Events

        try:
            await self._ctx.event_bus.emit(Events.AUDIO_CODEC_CHANGED, codec)
        except Exception as e:
            logger.warning(f"Failed to publish AUDIO_CODEC_CHANGED: {e}", exc_info=True)

    async def _on_config_changed(self, data=None):
        """Reload the audio device when the configuration changes (including a PortAudio re-enumeration)."""
        if self.codec:
            logger.info("AudioPlugin: config change event received; reloading audio device")
            await self.codec.reload_devices(reenumerate=True)

    async def _on_devices_refresh_request(self, data=None):
        """The settings page requests a device list refresh: stop streams -> re-enumerate -> reopen streams.

        The payload may be an asyncio.Future; once done, set_result is called with the list_audio_devices result.
        """
        from src.utils.audio_utils import list_audio_devices, refresh_portaudio_devices

        future = data if isinstance(data, asyncio.Future) else None
        result = {"input": [], "output": []}
        try:
            if self.codec:
                # PortAudio can only be terminated safely after the streams are stopped
                self.codec.stop_streams_for_enumeration()
                refresh_portaudio_devices(reinitialize=True)
                result = list_audio_devices(include_virtual=True)
                # Reopen the streams with the current configuration (the name match may now point to a new index)
                ok = await self.codec.reload_devices(reenumerate=False)
                if not ok:
                    logger.error("AudioPlugin: failed to reopen audio stream after device refresh")
            else:
                # Without a codec (audio disabled), still enumerate so the settings page can display devices
                refresh_portaudio_devices(reinitialize=True)
                result = list_audio_devices(include_virtual=True)
            logger.info(
                "AudioPlugin: device refresh complete "
                f"in={len(result.get('input', []))} out={len(result.get('output', []))}"
            )
        except Exception as e:
            logger.error(f"AudioPlugin: device refresh failed: {e}", exc_info=True)
        finally:
            if future is not None and not future.done():
                future.set_result(result)

    async def on_device_state_changed(self, state):
        """
        Handle device state changes.
        """
        if not self.codec:
            return

        from src.constants.constants import DeviceState

        if state == DeviceState.LISTENING:
            self._in_silence_period = True
            try:
                await asyncio.sleep(0.2)
            finally:
                self._in_silence_period = False

    async def on_incoming_json(self, message) -> None:
        """
        Handle TTS events.
        """
        if not isinstance(message, dict):
            return

        try:
            if message.get("type") == "tts":
                state = message.get("state")
                if state == "start":
                    if self._music_parallel_enabled():
                        logger.debug("TTS started (parallel mode): music keeps playing with ducking")
                    else:
                        await self._pause_music_for_tts()
                elif state == "stop":
                    # In parallel mode the resume is also sent as a fallback, in case a song started during TTS was paused
                    await self._resume_music_after_tts()
        except Exception as e:
            logger.error(f"Failed to handle TTS event: {e}", exc_info=True)

    def _music_parallel_enabled(self) -> bool:
        """Parallel playback decision: when the AEC engine is present and the configuration allows it, TTS does not pause music.

        When the engine is bypassed (missing library / self-disabled after repeated failures),
        it automatically falls back to the pause strategy to avoid raw parallel playback without echo cancellation polluting recognition.
        """
        try:
            config = self._ctx.get_config()
            if not bool(config.get_config("AEC_OPTIONS.MUSIC_PARALLEL", True)):
                return False
            return bool(self.codec and self.codec.aec_active)
        except Exception:
            return False

    async def on_incoming_audio(self, data: bytes) -> None:
        """
        Receive and play audio data.
        """
        if self.codec:
            try:
                await self.codec.write_audio(data)
            except Exception as e:
                logger.debug(f"Failed to write audio data: {e}")

    async def _pause_music_for_tts(self):
        """Pause music when TTS starts (TTS and music are mixed from separate queues, so no frames are lost; the music tail fades out naturally)."""
        try:
            from src.core.event_bus import Events
            from src.mcp.tools.music.events import MusicControlRequest

            logger.info("TTS started; sending music pause request")
            await self._ctx.event_bus.emit(
                Events.MUSIC_PAUSE_REQUEST, MusicControlRequest(source="tts")
            )
        except Exception as e:
            logger.warning(f"Failed to send music pause request: {e}", exc_info=True)

    async def _resume_music_after_tts(self):
        """Resume music after TTS ends (in parallel mode this is only a fallback; most of the time nothing was actually paused)."""
        try:
            from src.core.event_bus import Events
            from src.mcp.tools.music.events import MusicControlRequest

            log = logger.debug if self._music_parallel_enabled() else logger.info
            log("TTS playback finished; sending music resume request")
            await self._ctx.event_bus.emit(
                Events.MUSIC_RESUME_REQUEST, MusicControlRequest(source="tts")
            )
        except Exception as e:
            logger.error(f"Failed to send music resume request: {e}", exc_info=True)

    def register_resources(self, pool) -> None:
        codec = self.codec
        if codec:

            async def _cleanup():
                """Full cleanup of the audio codec: first notify subscribers to clear the codec, then close it."""
                import gc

                try:
                    # Music stops itself when it receives None; the full detach is handled by the mcp/container
                    await self._publish_audio_codec(None)
                except Exception as e:
                    logger.debug(f"Failed to publish codec clear: {e}", exc_info=True)
                gc.collect()
                await codec.close()

            pool.register("audio.codec", _cleanup)

    def _on_encoded_audio(self, encoded_data: bytes) -> None:
        """
        Audio encoding callback (called from the audio thread).
        """
        try:
            if not self._cmd:
                return
            self._cmd.schedule_command_nowait(self._send_audio_async, encoded_data)
        except Exception as e:
            logger.error(f"Failed to schedule audio send: {e}", exc_info=True)

    async def _send_audio_async(self, encoded_data: bytes) -> None:
        """
        Send audio data asynchronously.
        """
        async with self._send_sem:
            try:
                if not self._ctx.is_audio_channel_opened():
                    return
                if self._should_send_microphone_audio():
                    await self._cmd.send_audio(encoded_data)
            except Exception as e:
                logger.error(f"Failed to send audio data: {e}", exc_info=True)

    def _should_send_microphone_audio(self) -> bool:
        """
        Decide whether the microphone audio should be sent.
        """
        try:
            if self._in_silence_period:
                return False
            return self._ctx.should_capture_audio()
        except Exception as e:
            logger.warning(
                f"Failed to decide whether to send mic audio; defaulting to not sending: {e}", exc_info=True
            )
            return False
