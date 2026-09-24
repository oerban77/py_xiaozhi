"""Abstract base class for shortcut backends."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from src.logging import get_logger

logger = get_logger()


@dataclass
class ShortcutConfig:
    """Shortcut key configuration."""

    modifier: str  # ctrl, alt, shift, cmd
    key: str  # The key
    description: str = ""


class ShortcutBackend(ABC):
    """Abstract base class for shortcut backends.

    Defines the interface that all shortcut backends must implement.
    """

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        self._loop = loop
        self._running = False
        self._shortcuts: Dict[str, ShortcutConfig] = {}
        self._callbacks: Dict[str, Callable] = {}

    @abstractmethod
    async def start(self) -> bool:
        """Start listening for shortcut keys.

        Returns:
            Whether the start succeeded
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening for shortcut keys."""
        pass

    @abstractmethod
    def register(self, name: str, config: ShortcutConfig, callback: Callable) -> bool:
        """Register a shortcut key.

        Args:
            name: the shortcut name
            config: the shortcut configuration
            callback: the callback function (takes no arguments)

        Returns:
            Whether the registration succeeded
        """
        pass

    @abstractmethod
    def unregister(self, name: str) -> bool:
        """Unregister a shortcut key.

        Args:
            name: the shortcut name

        Returns:
            Whether the unregistration succeeded
        """
        pass

    def unregister_all(self) -> None:
        """Unregister all shortcut keys."""
        for name in list(self._shortcuts.keys()):
            self.unregister(name)

    @property
    def is_running(self) -> bool:
        """Whether it is currently running."""
        return self._running

    def _run_callback(self, name: str) -> None:
        """Run the callback function (thread-safe).

        Args:
            name: the shortcut name
        """
        if name not in self._callbacks:
            return

        callback = self._callbacks[name]
        if self._loop and self._loop.is_running():
            if asyncio.iscoroutinefunction(callback):
                asyncio.run_coroutine_threadsafe(callback(), self._loop)
            else:
                self._loop.call_soon_threadsafe(callback)
        else:
            # No event loop; call directly
            if asyncio.iscoroutinefunction(callback):
                logger.warning(f"Cannot invoke async callback {name}; no event loop")
            else:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Shortcut callback {name} execution failed: {e}", exc_info=True)
