"""State manager.

Centralizes device state management and broadcasts state changes through the event bus.
"""

import asyncio
from typing import TYPE_CHECKING

from src.constants.constants import DeviceState, ListeningMode
from src.core.event_bus import EventBus, Events
from src.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger()


class StateManager:
    """Device state manager.

    Responsibilities:
    - manage device state (IDLE, LISTENING, SPEAKING)
    - manage listening mode (REALTIME, AUTO_STOP, MANUAL)
    - manage session state (keep_listening, aec_enabled)
    - broadcast state changes via the event bus

    Usage:
        state = StateManager(event_bus)

        # Set state (automatically broadcasts)
        await state.set_device_state(DeviceState.LISTENING)

        # Read state
        if state.is_listening():
            ...
    """

    def __init__(self, event_bus: EventBus, aec_enabled: bool = True):
        self._event_bus = event_bus
        self._lock = asyncio.Lock()

        # Device state
        self._device_state: DeviceState = DeviceState.IDLE

        # AEC config
        self._aec_enabled: bool = aec_enabled

        # Listening mode: determine the default based on the AEC configuration
        self._listening_mode: ListeningMode = (
            ListeningMode.REALTIME if aec_enabled else ListeningMode.AUTO_STOP
        )

        # Session state
        self._keep_listening: bool = False

        # Abort flag
        self._aborted: bool = False

    # -------------------------
    # Device state
    # -------------------------
    @property
    def device_state(self) -> DeviceState:
        """
        Get the current device state.
        """
        return self._device_state

    async def set_device_state(self, state: DeviceState) -> None:
        """Set the device state.

        Args:
            state: the new device state

        If the state changes, it is broadcast via the event bus.
        """
        async with self._lock:
            if self._device_state == state:
                return

            old_state = self._device_state
            self._device_state = state
            logger.info(f"Device state changed: {old_state} -> {state}")

            # Reset the abort flag
            if state == DeviceState.LISTENING:
                self._aborted = False

        # Broadcast outside the lock to avoid deadlock
        await self._event_bus.emit(
            Events.DEVICE_STATE_CHANGED,
            {"old_state": old_state, "new_state": state},
        )

    def is_idle(self) -> bool:
        """
        Whether the device is idle.
        """
        return self._device_state == DeviceState.IDLE

    def is_listening(self) -> bool:
        """
        Whether the device is currently listening.
        """
        return self._device_state == DeviceState.LISTENING

    def is_speaking(self) -> bool:
        """
        Whether the device is currently speaking.
        """
        return self._device_state == DeviceState.SPEAKING

    # -------------------------
    # Listening mode
    # -------------------------
    @property
    def listening_mode(self) -> ListeningMode:
        """
        Get the current listening mode.
        """
        return self._listening_mode

    def set_listening_mode(self, mode: ListeningMode) -> None:
        """
        Set the listening mode.
        """
        self._listening_mode = mode
        logger.debug(f"Listen mode set to: {mode}")

    # -------------------------
    # Session state
    # -------------------------
    @property
    def keep_listening(self) -> bool:
        """
        Whether continuous listening is enabled.
        """
        return self._keep_listening

    def set_keep_listening(self, value: bool) -> None:
        """
        Set the continuous listening state.
        """
        self._keep_listening = value
        logger.debug(f"Continuous listening: {value}")

    @property
    def aec_enabled(self) -> bool:
        """
        Whether AEC is enabled.
        """
        return self._aec_enabled

    # -------------------------
    # Abort state
    # -------------------------
    @property
    def aborted(self) -> bool:
        """
        Whether the operation has been aborted.
        """
        return self._aborted

    def set_aborted(self, value: bool) -> None:
        """
        Set the abort state.
        """
        self._aborted = value

    # -------------------------
    # Combined state queries
    # -------------------------
    def should_capture_audio(self) -> bool:
        """Whether audio should be captured.

        Audio should be captured when:
        1. the device is listening and not aborted
        2. the device is speaking, AEC is enabled, and continuous listening is active in realtime mode
        """
        if self._device_state == DeviceState.LISTENING and not self._aborted:
            return True

        return (
            self._device_state == DeviceState.SPEAKING
            and self._aec_enabled
            and self._keep_listening
            and self._listening_mode == ListeningMode.REALTIME
        )

    def get_snapshot(self) -> dict:
        """Get the state snapshot.

        Returns a dictionary with all current states for debugging and logging.
        """
        return {
            "device_state": self._device_state,
            "listening_mode": self._listening_mode,
            "keep_listening": self._keep_listening,
            "aec_enabled": self._aec_enabled,
            "aborted": self._aborted,
        }
