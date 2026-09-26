import asyncio
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from src.constants.constants import AudioConfig
from src.logging import get_logger
from src.utils.config_manager import ConfigManager, get_config
from src.utils.resource_finder import get_app_root, get_user_keywords_path

logger = get_logger()

_STOP_SENTINEL = object()


class WakeWordDetector:

    def __init__(self):
        self.audio_codec = None
        self._running = False
        self._paused = False
        self._detection_task = None
        self._audio_queue: Optional[asyncio.Queue] = None

        self._last_detection_time = 0
        self._detection_cooldown = 1.5

        self.on_detected_callback: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

        self.enabled = False
        self._model_loaded = False
        self._model_dir: Optional[Path] = None
        self._keyword_spotter = None
        self._stream = None

        # Add a lock to protect access to the sherpa-onnx objects
        self._onnx_lock = threading.Lock()
        self._stopping = False  # Flag indicating whether stopping is in progress

        self._sample_rate = AudioConfig.INPUT_SAMPLE_RATE
        self._num_threads = 4
        self._provider = "cpu"
        self._max_active_paths = 2
        self._keywords_score = 1.8
        self._keywords_threshold = 0.2
        self._num_trailing_blanks = 1

    async def initialize(self, model_path: Optional[str] = None) -> bool:
        try:
            # 1. Check whether the feature is enabled in the config
            config = get_config()
            if not config.get_config("WAKE_WORD_OPTIONS.USE_WAKE_WORD", False):
                logger.info("Wake word feature disabled")
                self.enabled = False
                return False

            # 2. Load configuration parameters
            self._load_config(config)

            # 3. Determine the model path
            if model_path is None:
                model_path = config.get_config("WAKE_WORD_OPTIONS.MODEL_PATH", "models")

            self._model_dir = get_app_root() / model_path

            if not self._model_dir.exists():
                logger.error(f"Model directory does not exist: {self._model_dir}")
                self.enabled = False
                return False

            # 4. Stop the old detection loop and release the old model
            if self._running:
                await self.stop()
            self._release_model()

            # 5. Load the new model
            if not self._load_model():
                self.enabled = False
                return False

            self.enabled = True
            self._model_loaded = True
            logger.info(f"Wake word detector initialized: {self._model_dir}")
            return True

        except Exception as e:
            logger.error(f"Wake word detector init failed: {e}", exc_info=True)
            self.enabled = False
            return False

    def _load_config(self, config: ConfigManager):
        self._num_threads = config.get_config("WAKE_WORD_OPTIONS.NUM_THREADS", 4)
        self._provider = config.get_config("WAKE_WORD_OPTIONS.PROVIDER", "cpu")
        self._max_active_paths = config.get_config("WAKE_WORD_OPTIONS.MAX_ACTIVE_PATHS", 2)
        self._keywords_score = config.get_config("WAKE_WORD_OPTIONS.KEYWORDS_SCORE", 1.8)
        self._keywords_threshold = config.get_config("WAKE_WORD_OPTIONS.KEYWORDS_THRESHOLD", 0.2)
        self._num_trailing_blanks = config.get_config("WAKE_WORD_OPTIONS.NUM_TRAILING_BLANKS", 1)

        # Validate
        if not 0.1 <= self._keywords_threshold <= 1.0:
            logger.warning(f"Keyword threshold {self._keywords_threshold}  out of range; reset to 0.25")
            self._keywords_threshold = 0.25

        if not 0.1 <= self._keywords_score <= 10.0:
            logger.warning(f"Keyword score {self._keywords_score}  out of range; reset to 2.0")
            self._keywords_score = 2.0

        logger.debug(f"KWS config: threshold={self._keywords_threshold}, score={self._keywords_score}")

    def _load_model(self) -> bool:
        """Load sherpa-onnx KeywordSpotter model."""
        try:
            import sherpa_onnx

            encoder_path = self._model_dir / "encoder.onnx"
            decoder_path = self._model_dir / "decoder.onnx"
            joiner_path = self._model_dir / "joiner.onnx"
            tokens_path = self._model_dir / "tokens.txt"

            lang = get_config().get_config("WAKE_WORD_OPTIONS.WAKE_WORD_LANG", "en")
            keywords_path = get_user_keywords_path(lang)

            required_files = [encoder_path, decoder_path, joiner_path, tokens_path, keywords_path]
            for file_path in required_files:
                if not file_path.exists():
                    logger.error(f"Model file does not exist: {file_path}")
                    return False

            # Windows: sherpa-onnx C++ reads tokens.txt via std::ifstream(narrow char*).
            # When the path contains non-ASCII characters, the GBK code page swallows
            # backslashes and the open fails. Copy tokens.txt to an ASCII-safe user directory.
            tokens_path = self._ensure_ascii_path(tokens_path, lang)

            logger.info(f"Loading KeywordSpotter model: {self._model_dir}")

            with self._onnx_lock:
                self._keyword_spotter = sherpa_onnx.KeywordSpotter(
                    tokens=str(tokens_path),
                    encoder=str(encoder_path),
                    decoder=str(decoder_path),
                    joiner=str(joiner_path),
                    keywords_file=str(keywords_path),
                    num_threads=self._num_threads,
                    sample_rate=self._sample_rate,
                    feature_dim=80,
                    max_active_paths=self._max_active_paths,
                    keywords_score=self._keywords_score,
                    keywords_threshold=self._keywords_threshold,
                    num_trailing_blanks=self._num_trailing_blanks,
                    provider=self._provider,
                )

            logger.info("KeywordSpotter model loaded")
            return True

        except ImportError as e:
            logger.error(f"Failed to import sherpa_onnx: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Failed to load model: {e}", exc_info=True)
            return False

    @staticmethod
    def _ensure_ascii_path(file_path: Path, lang: str) -> Path:
        """On Windows, copy file to an ASCII-safe path if needed.

        sherpa-onnx reads tokens.txt via std::ifstream with narrow char paths.
        Under GBK code page, UTF-8 encoded non-ASCII directory names corrupt
        the path (certain trailing bytes consume the backslash separator).
        """
        import sys

        if sys.platform != "win32":
            return file_path

        if str(file_path).isascii():
            return file_path

        import shutil

        safe_dir = get_user_keywords_path(lang).parent
        safe_path = safe_dir / file_path.name
        shutil.copy2(file_path, safe_path)
        logger.debug(f"Copied {file_path.name} to an ASCII-safe path: {safe_path}")
        return safe_path

    def _release_model(self):
        if not self._model_loaded:
            return
        with self._onnx_lock:
            try:
                spotter = self._keyword_spotter
                stream = self._stream
                self._keyword_spotter = None
                self._stream = None

                # The spotter must be released before the stream.
                # KeywordSpotter owns the stream at the C++ level; releasing them in the
                # reverse order would double-free the stream when the spotter is destroyed.
                if spotter is not None:
                    del spotter

                if stream is not None:
                    del stream

                self._model_loaded = False
                logger.debug("Model resources released")

            except Exception as e:
                logger.debug(f"Error releasing model resources: {e}")

    def on_detected(self, callback: Callable):
        self.on_detected_callback = callback

    def on_audio_data(self, audio_data: np.ndarray):
        if not self.enabled or not self._running or self._paused:
            return

        if self._audio_queue is None:
            return

        try:
            self._audio_queue.put_nowait(audio_data.copy())
        except asyncio.QueueFull:
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(audio_data.copy())
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
        except Exception as e:
            logger.debug(f"Failed to enqueue audio data: {type(e).__name__}: {e}")

    async def start(self, audio_codec) -> bool:
        if not self.enabled:
            logger.warning("Wake word feature is not enabled")
            return False

        if not self._keyword_spotter:
            logger.error("Model not loaded; call initialize() first")
            return False

        try:
            self.audio_codec = audio_codec
            self._running = True
            self._paused = False

            # Create queue in event loop
            self._audio_queue = asyncio.Queue(maxsize=100)

            # Create detection stream
            self._stream = self._keyword_spotter.create_stream()

            # Register as audio listener
            self.audio_codec.add_audio_listener(self)

            # Start detection task
            self._detection_task = asyncio.create_task(self._detection_loop())

            logger.info("Wake word detector started")
            return True

        except Exception as e:
            logger.error(f"Failed to start detector: {e}", exc_info=True)
            return False

    async def stop(self):
        self._stopping = True
        self._running = False

        # Remove audio listener
        if self.audio_codec:
            self.audio_codec.remove_audio_listener(self)
            self.audio_codec = None

        # Use a sentinel to wake up the detection loop blocked on queue.get()
        if self._audio_queue:
            try:
                self._audio_queue.put_nowait(_STOP_SENTINEL)
            except asyncio.QueueFull:
                try:
                    self._audio_queue.get_nowait()
                    self._audio_queue.put_nowait(_STOP_SENTINEL)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

        # Wait for the detection loop to exit on its own (triggered by the sentinel)
        if self._detection_task:
            try:
                await asyncio.wait_for(self._detection_task, timeout=1.0)
            except asyncio.TimeoutError:
                self._detection_task.cancel()
                try:
                    await self._detection_task
                except asyncio.CancelledError:
                    pass
            self._detection_task = None

        # Clear queue
        if self._audio_queue:
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self._audio_queue = None

        self._stopping = False
        logger.info("Wake word detector stopped")

    async def reload(self, model_path: Optional[str] = None) -> bool:
        was_running = self._running
        codec = self.audio_codec

        logger.info(f"Hot-reloading wake word model: {model_path}")

        # Re-initialize with new model
        if not await self.initialize(model_path):
            return False

        # Restart if was running
        if was_running and codec:
            return await self.start(codec)

        return True

    async def shutdown(self):
        """Fully shutdown and release all resources."""
        await self.stop()
        self._release_model()
        self.enabled = False
        logger.info("Wake word detector closed")

    def pause(self):
        """Pause detection (keeps model loaded)."""
        self._paused = True

    def resume(self):
        """Resume detection."""
        self._paused = False

    async def _detection_loop(self):
        error_count = 0
        MAX_ERRORS = 5

        while self._running and not self._stopping:
            try:
                if self._paused or self._stopping:
                    await asyncio.sleep(0.1)
                    continue

                await self._process_audio()
                await asyncio.sleep(0.005)
                error_count = 0

            except asyncio.CancelledError:
                break
            except RuntimeError as e:
                if "no running event loop" in str(e) or "Event loop is closed" in str(e):
                    break
                error_count += 1
                logger.error(f"Detection loop error ({error_count}/{MAX_ERRORS}): {e}", exc_info=True)

                if error_count >= MAX_ERRORS:
                    logger.critical("Max error count reached; stopping detection")
                    break

                await asyncio.sleep(1)
            except Exception as e:
                error_count += 1
                logger.error(f"Detection loop error ({error_count}/{MAX_ERRORS}): {e}", exc_info=True)

                if self.on_error:
                    try:
                        if asyncio.iscoroutinefunction(self.on_error):
                            await self.on_error(e)
                        else:
                            self.on_error(e)
                    except Exception as cb_error:
                        logger.error(f"Error callback failed: {cb_error}")

                if error_count >= MAX_ERRORS:
                    logger.critical("Max error count reached; stopping detection")
                    break

                await asyncio.sleep(1)

    async def _process_audio(self):
        if self._stopping or not self._audio_queue:
            return

        try:
            audio_data = self._audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        if audio_data is _STOP_SENTINEL:
            return

        if audio_data is None or len(audio_data) == 0:
            return

        detected_result = None

        with self._onnx_lock:
            if self._stopping or self._stream is None or self._keyword_spotter is None:
                return

            try:
                self._stream.accept_waveform(
                    sample_rate=self._sample_rate, waveform=audio_data
                )

                if self._keyword_spotter.is_ready(self._stream):
                    self._keyword_spotter.decode_stream(self._stream)
                    result = self._keyword_spotter.get_result(self._stream)

                    if result:
                        detected_result = result
                        self._keyword_spotter.reset_stream(self._stream)
            except Exception as e:
                logger.debug(f"Error while processing audio: {e}")

        if detected_result is not None:
            await self._handle_detection(detected_result)

    async def _handle_detection(self, result):
        # Anti-repeat check
        current_time = time.time()
        if current_time - self._last_detection_time < self._detection_cooldown:
            return

        self._last_detection_time = current_time

        # Briefly pause detection so the interrupt flow can complete, avoiding stale
        # audio triggering a duplicate detection
        self._paused = True
        try:
            if self.on_detected_callback:
                try:
                    if asyncio.iscoroutinefunction(self.on_detected_callback):
                        await self.on_detected_callback(result, result)
                    else:
                        self.on_detected_callback(result, result)
                except Exception as e:
                    logger.error(f"Wake word callback execution failed: {e}", exc_info=True)
        finally:
            # Fast exit: skip the delay and queue cleanup while stopping
            if self._stopping:
                self._paused = False
                return
            # Resume detection after the delay (wait for abort + clear_audio_queue to finish)
            await asyncio.sleep(0.3)
            # Drain stale audio frames left in the queue
            if self._audio_queue:
                while True:
                    try:
                        self._audio_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            self._paused = False
