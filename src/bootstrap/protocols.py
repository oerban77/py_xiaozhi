"""Interface protocol definitions.

Defines the contracts between plugins, windows, and core services to achieve loose coupling.
"""

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

if TYPE_CHECKING:
    from src.constants.constants import DeviceState, ListeningMode


class PluginContext(Protocol):
    """The read-only context accessible to plugins.

    Plugins use this interface to obtain application state but cannot modify the state directly.
    """

    def get_device_state(self) -> "DeviceState":
        """
        Get the current device state.
        """
        ...

    def get_listening_mode(self) -> "ListeningMode":
        """
        Get the current listening mode.
        """
        ...

    def is_listening(self) -> bool:
        """
        Whether it is currently listening.
        """
        ...

    def is_speaking(self) -> bool:
        """
        Whether it is currently speaking.
        """
        ...

    def is_idle(self) -> bool:
        """
        Whether it is currently idle.
        """
        ...

    def is_audio_channel_opened(self) -> bool:
        """
        Whether the audio channel is open.
        """
        ...

    def should_capture_audio(self) -> bool:
        """
        Whether audio should be captured.
        """
        ...

    def is_keep_listening(self) -> bool:
        """
        Whether continuous listening is kept on.
        """
        ...

    def get_config(self) -> Any:
        """
        Get the config manager.
        """
        ...


class PluginCommands(Protocol):
    """The commands plugins can execute.

    Plugins use this interface to perform actions; the core services implement the actual logic.
    """

    async def start_listening(self, mode: "ListeningMode") -> None:
        """
        Start listening.
        """
        ...

    async def stop_listening(self) -> None:
        """
        Stop listening.
        """
        ...

    async def abort_speaking(self, reason: str) -> None:
        """
        Abort speech output.
        """
        ...

    async def send_audio(self, data: bytes) -> None:
        """
        Send audio data.
        """
        ...

    async def send_text(self, text: str) -> None:
        """
        Send a text message.
        """
        ...

    async def send_wake_word_detected(self, text: str) -> None:
        """
        Send the detected text (wake word or user input).
        """
        ...

    async def send_mcp_message(self, payload: str) -> None:
        """
        Send an MCP message (the format is wrapped automatically).
        """
        ...

    async def connect_protocol(self) -> bool:
        """
        Connect the protocol channel.
        """
        ...

    def spawn(self, coro: Awaitable[Any], name: str) -> Any:
        """
        Create an async task.
        """
        ...

    def schedule_command_nowait(self, fn: Callable, *args, **kwargs) -> None:
        """
        Schedule a command (non-blocking).
        """
        ...

    def request_shutdown(self) -> None:
        """
        Request the application to shut down.
        """
        ...


class EventHandler(Protocol):
    """
    Event handler protocol.
    """

    async def __call__(self, data: Any = None) -> None:
        """
        Handle the event.
        """
        ...


class EventBusProtocol(Protocol):
    """Event bus protocol.

    Used for decoupled communication between components.
    """

    def on(self, event: str, handler: Callable[..., Awaitable[None]]) -> None:
        """
        Register an event handler.
        """
        ...

    def off(self, event: str, handler: Callable[..., Awaitable[None]]) -> None:
        """
        Remove an event handler.
        """
        ...

    async def emit(self, event: str, data: Any = None) -> None:
        """
        Emit an event.
        """
        ...
