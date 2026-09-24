"""Wake word plugin.

Detects the wake word and triggers a conversation.
"""

from typing import TYPE_CHECKING, Optional

from src.constants.constants import AbortReason
from src.logging import get_logger
from src.plugins.base import Plugin

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext

logger = get_logger()


class WakeWordPlugin(Plugin):
    name = "wake_word"
    priority = 30
    requires = ["audio"]

    def __init__(self) -> None:
        super().__init__()
        self.detector = None

    @property
    def _audio_plugin(self):
        """Get the AudioPlugin through dependency injection."""
        return self.get_dep("audio")

    async def setup(self, ctx: "PluginContext", cmd: "PluginCommands") -> None:
        await super().setup(ctx, cmd)
        # Subscribe to config change events (lightweight; does not load the model)
        from src.core.event_bus import Events
        ctx.event_bus.on(Events.CONFIG_CHANGED, self._on_config_changed)

    async def _on_config_changed(self, data=None):
        """Reload the wake word model when the configuration changes."""
        logger.info("WakeWordPlugin: config change event received; reloading wake word model")
        await self.reload_model()

    async def start(self) -> None:
        try:
            # Defer model loading to the start() phase to avoid conflicting with the PortAudio DLL during setup()
            if self.detector is None:
                from src.audio_processing.wake_word_detect import WakeWordDetector

                self.detector = WakeWordDetector()
                if not await self.detector.initialize():
                    logger.info("Wake word detector is not enabled or failed to initialize")
                    self.detector = None
                    return
                self.detector.on_detected(self._on_detected)
                self.detector.on_error = self._on_error

            if not self._audio_plugin or not self._audio_plugin.codec:
                logger.warning("audio_codec not found; cannot start wake word detection")
                return
            await self.detector.start(self._audio_plugin.codec)
        except ImportError as e:
            logger.error(f"Failed to import wake word detector: {e}", exc_info=True)
            self.detector = None
        except Exception as e:
            logger.error(f"Failed to start wake word detector: {e}", exc_info=True)

    async def stop(self) -> None:
        if self.detector:
            try:
                await self.detector.stop()
            except Exception as e:
                logger.warning(f"Failed to stop wake word detector: {e}", exc_info=True)

    def register_resources(self, pool) -> None:
        detector = self.detector
        if detector:
            pool.register("wake_word.detector", detector.shutdown)

    async def reload_model(self, model_path: Optional[str] = None) -> bool:
        """Hot-reload the wake word model.

        Args:
            model_path: the new model path (e.g., "models/en"). If None, it is read from the configuration.

        Returns:
            Whether the reload succeeded
        """
        if not self.detector:
            logger.warning("Detector not initialized; cannot hot-reload")
            return False

        try:
            return await self.detector.reload(model_path)
        except Exception as e:
            logger.error(f"Failed to hot-reload wake word model: {e}", exc_info=True)
            return False

    async def _on_detected(self, wake_word, full_text):
        """
        Wake word detection callback.
        """
        try:
            if self._ctx.is_speaking():
                await self._cmd.abort_speaking(AbortReason.WAKE_WORD_DETECTED)
                if self._audio_plugin and self._audio_plugin.codec:
                    await self._audio_plugin.codec.clear_audio_queue()
            else:
                # Start an automatic conversation
                await self._cmd.connect_protocol()
                from src.constants.constants import ListeningMode

                mode = (
                    ListeningMode.REALTIME
                    if self._ctx.get_config().get_config("AEC_OPTIONS.ENABLED", True)
                    else ListeningMode.AUTO_STOP
                )
                await self._cmd.start_listening(mode)
        except Exception as e:
            logger.error(f"Failed to handle wake word detection: {e}", exc_info=True)

    async def _on_error(self, error):
        """
        Wake word detection error callback.
        """
        logger.error(f"Wake word detection error: {error}", exc_info=True)
