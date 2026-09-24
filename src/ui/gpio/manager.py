# -*- coding: utf-8 -*-
"""GPIO key interface (logs state)."""

import asyncio
from typing import TYPE_CHECKING, Optional

from src.core.event_bus import EventBus, Events
from src.logging import get_logger

from .input import GPIOInput

if TYPE_CHECKING:
    from src.core.task_manager import TaskManager

logger = get_logger()


class GpioViewManager:
    """GPIO interface (ViewPort: the same set_* methods as CLI/GUI)."""

    def __init__(
        self,
        event_bus: EventBus,
        task_manager: Optional["TaskManager"] = None,
    ):
        self._event_bus = event_bus
        self._task_manager = task_manager
        self._gpio_input = GPIOInput()
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # state
        self._auto_mode = False
        self._status = "Idle"
        self._connected = False
        self._chat_text = ""
        self._music_line = ""

    async def start(self, mode: str = "gpio"):
        """Start the GPIO view.

        Args:
            mode: the runtime mode (ignored in GPIO mode)
        """
        logger.info("GpioViewManager: starting GPIO interface...")
        self._running = True
        self._loop = asyncio.get_running_loop()

        # Settings GPIO key callback
        if self._gpio_input.available:
            self._gpio_input.setup(
                on_key1_pressed=self._on_key1,
                on_key2_pressed=self._on_key2,
                on_key3_pressed=self._on_key3,
                on_key4_pressed=self._on_key4,
            )
            logger.info("GPIO buttons ready")
            logger.info("KEY1: start/stop conversation")
            logger.info("KEY2: interrupt speech")
            logger.info("KEY3: switch mode")
            logger.info("KEY4: quit application")
        else:
            logger.warning("GPIO unavailable; button feature disabled")

        # Keep running (event-driven)
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("GpioViewManager: task cancelled")

    async def close(self):
        """Close the GPIO view."""
        logger.info("GpioViewManager: shutting down...")
        self._running = False
        self._gpio_input.close()
        logger.info("GpioViewManager: closed")

    # ========== Key callbacks ============

    def _on_key1(self):
        """KEY1: start/stop conversation."""
        if self._auto_mode:
            self._safe_emit(Events.UI_AUTO_START)
            logger.info("[KEY1] Auto mode: toggle start/stop conversation")
        else:
            self._safe_emit(Events.UI_MANUAL_TOGGLE)
            logger.info("[KEY1] Manual mode: toggle recording")

    def _on_key2(self):
        """KEY2: interrupt."""
        self._safe_emit(Events.UI_ABORT_REQUEST)
        logger.info("[KEY2] Interrupt speech")

    def _on_key3(self):
        """KEY3: switch auto/manual."""
        self._safe_emit(Events.UI_AUTO_TOGGLE)
        logger.info("[KEY3] Requesting conversation mode switch")

    def _on_key4(self):
        """KEY4: exit the program."""
        self._safe_emit(Events.UI_QUIT_REQUEST)
        logger.info("[KEY4] Quit application")

    def _safe_emit(self, event: str, data=None):
        """Safely emit an event.

        Prefer TaskManager.schedule_nowait (thread-safe + traceable);
        otherwise fall back to run_coroutine_threadsafe.
        """
        def _start_emit():
            if data is None:
                return self._event_bus.emit(event)
            return self._event_bus.emit(event, data)

        if self._task_manager is not None:
            try:
                self._task_manager.schedule_nowait(_start_emit)
                return
            except Exception as e:
                logger.error(
                    f"GpioViewManager failed to dispatch event {event} via TaskManager: {e}",
                    exc_info=True,
                )

        if not self._loop or not self._loop.is_running():
            return
        coro = _start_emit()
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)

            def _done(f):
                try:
                    exc = f.exception()
                except Exception:
                    return
                if exc:
                    logger.error(
                        f"GpioViewManager failed to emit event {event}: {exc}",
                        exc_info=exc,
                    )

            fut.add_done_callback(_done)
        except Exception as e:
            logger.error(f"GpioViewManager failed to dispatch event {event} failed: {e}", exc_info=True)
            if asyncio.iscoroutine(coro):
                coro.close()

    # ========== Public API ============

    @property
    def is_running(self) -> bool:
        """Whether it is running."""
        return self._running

    def set_status(self, status: str, connected: bool = True):
        """settingsstate."""
        self._status = status
        self._connected = connected
        logger.info(f"[Status] {status}")

    def set_chat_text(self, text: str):
        self._chat_text = text
        logger.info(f"[Chat] {text}")

    def set_music_line(self, text: str):
        self._music_line = text
        logger.info(f"[Music] {text}")

    def set_emotion(self, emotion: str):
        logger.debug(f"[Emotion] {emotion}")

    def set_auto_mode(self, auto_mode: bool):
        # The KEY1 branch checks this
        self._auto_mode = auto_mode
        mode_text = "Auto" if auto_mode else "Manual"
        logger.info(f"[Mode] {mode_text}")

    def set_button_text(self, text: str):
        logger.debug(f"GPIO set_button_text: {text}")

    def is_auto_mode(self) -> bool:
        return bool(self._auto_mode)
