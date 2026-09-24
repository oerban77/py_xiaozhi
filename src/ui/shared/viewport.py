"""Unified interface contract: gui / cli / gpio all implement this set."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ViewPort(Protocol):
    """Collection of methods used to write to the interface."""

    async def start(self, mode: str = "cli") -> None:
        """Start."""
        ...

    async def close(self) -> None:
        """Close."""
        ...

    def set_status(self, status: str, connected: bool = True) -> None:
        """Status bar."""
        ...

    def set_emotion(self, emotion: str) -> None:
        """Emotion name; each platform resolves the actual resources on its own."""
        ...

    def set_chat_text(self, text: str) -> None:
        """Conversation content (TTS / STT)."""
        ...

    def set_music_line(self, text: str) -> None:
        """Music state or lyrics."""
        ...

    def set_button_text(self, text: str) -> None:
        """Main button label; implementations without a button may leave this empty."""
        ...

    def set_auto_mode(self, auto_mode: bool) -> None:
        """Refresh the auto/manual display (the real state lives in the Session)."""
        ...

    def is_auto_mode(self) -> bool:
        """The mode currently recorded in the interface (used, for example, by the GPIO key branch)."""
        ...
