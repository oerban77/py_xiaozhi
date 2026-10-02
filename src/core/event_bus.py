"""Event bus.

Provide a decoupled communication mechanism between components.
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from src.logging import get_logger

logger = get_logger()


# Predefined event names
class Events:
    """
    Predefined event constants.
    """

    # devicestate
    DEVICE_STATE_CHANGED = "device_state_changed"

    # Protocol-related
    PROTOCOL_CONNECTED = "protocol_connected"
    PROTOCOL_DISCONNECTED = "protocol_disconnected"
    INCOMING_JSON = "incoming_json"
    INCOMING_AUDIO = "incoming_audio"

    # Network error
    NETWORK_ERROR = "network_error"

    # Audio channel
    AUDIO_CHANNEL_OPENED = "audio_channel_opened"
    AUDIO_CHANNEL_CLOSED = "audio_channel_closed"
    # AudioCodec lifecycle (AudioPlugin → MusicPlayer subscribers, avoiding a direct set)
    AUDIO_CODEC_CHANGED = "audio_codec_changed"
    # Request a re-enumeration of audio devices (settings page refresh; stop the stream first and reinitialize PortAudio)
    # payload: optional asyncio.Future; when complete, set_result(list|dict|None)
    AUDIO_DEVICES_REFRESH_REQUEST = "audio_devices_refresh_request"

    # Application lifecycle
    APP_SHUTDOWN = "app_shutdown"
    # System-level notice (degraded mode banner, etc.; payload: str)
    SYSTEM_NOTICE = "system_notice"

    # Music player events
    MUSIC_STATE_CHANGED = "music_state_changed"  # Playback state changes
    MUSIC_LYRICS_UPDATE = "music_lyrics_update"  # Lyrics update
    MUSIC_PROGRESS_UPDATE = "music_progress_update"  # Progress update

    # Music control commands (external control of MusicPlayer)
    MUSIC_STOP_REQUEST = "music_stop_request"  # Immediate stop request
    MUSIC_PAUSE_REQUEST = "music_pause_request"  # Request pause (such as TTS)
    MUSIC_RESUME_REQUEST = "music_resume_request"  # Request restore

    # UI actions (interface → plugin)
    UI_BUTTON_PRESS = "ui_button_press"  # Manual: press
    UI_BUTTON_RELEASE = "ui_button_release"  # Manual: release
    UI_MANUAL_TOGGLE = "ui_manual_toggle"  # Manual: tap once to start/end recording
    UI_AUTO_TOGGLE = "ui_auto_toggle"  # switch auto/manual
    UI_AUTO_START = "ui_auto_start"  # Auto: start/stop conversation
    UI_ABORT_REQUEST = "ui_abort_request"  # interrupt
    UI_SEND_TEXT = "ui_send_text"  # Send text
    UI_SEND_ATTACHMENT = "ui_send_attachment"  # Analyze and send an attachment
    UI_ATTACHMENT_STATUS = "ui_attachment_status"  # Attachment processing result
    UI_QUIT_REQUEST = "ui_quit_request"  # Quit
    UI_OPEN_SETTINGS = "ui_open_settings"  # Open settings
    UI_TOGGLE_WINDOW = "ui_toggle_window"  # show/hide main window (GUI)
    UI_MUTE_TOGGLE = "ui_mute_toggle"  # toggle speaker mute (GUI)

    # Configuration change events
    CONFIG_CHANGED = "config_changed"  # Configuration has changed (requires hot reload)
    # After MCP tool exposure changes: disconnect and reconnect the protocol so the server re-lists tools
    PROTOCOL_RECONNECT_REQUEST = "protocol_reconnect_request"


# Known event names: log a warning on on/emit typos for easier debugging
_KNOWN_EVENTS: frozenset[str] = frozenset(
    v for k, v in vars(Events).items() if not k.startswith("_") and isinstance(v, str)
)


class EventBus:
    """Event bus.

    Supports async event handlers to enable loosely coupled communication between components.

    Usage:     bus = EventBus()

    # register handler async def on_state_changed(state):     print(f"State: {state}")

    bus.on(Events.DEVICE_STATE_CHANGED, on_state_changed)

    # trigger event await bus.emit(Events.DEVICE_STATE_CHANGED, DeviceState.LISTENING)

    # remove handler bus.off(Events.DEVICE_STATE_CHANGED, on_state_changed)
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable[..., Awaitable[None]]]] = defaultdict(
            list
        )

    @staticmethod
    def _warn_if_unknown(event: str, action: str) -> None:
        if event not in _KNOWN_EVENTS:
            logger.warning(
                f"EventBus: {action} unknown event name {event!r} (possible typo; "
                "use the Events.* constants)"
            )

    def on(self, event: str, handler: Callable[..., Awaitable[None]]) -> None:
        """Register an event handler.

        Args:
            event: event name
            handler: async handler function
        """
        self._warn_if_unknown(event, "register")
        if handler not in self._handlers[event]:
            self._handlers[event].append(handler)
            logger.debug(f"EventBus: handler registered {handler.__name__} -> {event}")

    def off(self, event: str, handler: Callable[..., Awaitable[None]]) -> None:
        """Remove an event handler.

        Args:
            event: event name
            handler: handler function to remove
        """
        if handler in self._handlers[event]:
            self._handlers[event].remove(handler)
            logger.debug(f"EventBus: handler removed {handler.__name__} <- {event}")

    def clear(self, event: str = None) -> None:
        """Clear event handlers.

        Args:
            event: event name; if None, clear all
        """
        if event is None:
            self._handlers.clear()
            logger.debug("EventBus: all handlers cleared")
        elif event in self._handlers:
            self._handlers[event].clear()
            logger.debug(f"EventBus: cleared all handlers for event {event}")

    async def emit(self, event: str, data: Any = None) -> None:
        """Trigger an event.

        Invoke all registered handlers in parallel.

        Args:
            event: event name
            data: event data
        """
        handlers = list(self._handlers.get(event, []))
        if not handlers:
            # Still warn about unknown event names even with no subscribers to avoid silent typo losses
            self._warn_if_unknown(event, "trigger")
            return

        logger.debug(f"EventBus: emitting event {event}, {len(handlers)} handler(s)")

        # Execute all handlers in parallel
        tasks = []
        for handler in handlers:
            try:
                tasks.append(self._safe_call(handler, data))
            except Exception as e:
                logger.error(
                    f"EventBus: failed to create task {handler.__name__}: {e}",
                    exc_info=True,
                )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def emit_sequential(self, event: str, data: Any = None) -> None:
        """Trigger events in order.

        Invoke handlers in registration order.

        Args:
            event: event name
            data: event data
        """
        handlers = list(self._handlers.get(event, []))
        for handler in handlers:
            await self._safe_call(handler, data)

    async def _safe_call(
        self, handler: Callable[..., Awaitable[None]], data: Any
    ) -> None:
        """
        Safely call handlers and capture exceptions.
        """
        try:
            if data is None:
                await handler()
            else:
                await handler(data)
        except Exception as e:
            logger.error(
                f"EventBus: handler {handler.__name__} execution error: {e}",
                exc_info=True,
            )

    def has_handlers(self, event: str) -> bool:
        """
        Check whether the event has handlers.
        """
        return bool(self._handlers.get(event))

    def handler_count(self, event: str) -> int:
        """
        Get the number of handlers for the event.
        """
        return len(self._handlers.get(event, []))
