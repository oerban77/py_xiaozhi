"""Resource pool.

A unified resource registration and release mechanism. Every resource that needs
cleanup (C extensions, audio streams, network connections, etc.) is registered in
the pool, and on shutdown they are all released in the reverse order of
registration, avoiding duplicate releases and omissions.
"""

import asyncio
from typing import Awaitable, Callable, Union

from src.logging import get_logger

logger = get_logger()

CleanupFunc = Callable[[], Union[None, Awaitable[None]]]


class ResourcePool:
    """Resource pool — register cleanup functions and release them all in reverse order.

    Usage:
        pool = ResourcePool()
        pool.register("opus_codec", opus_codec.close)
        pool.register("audio_stream", stream_manager.stop)
        await pool.shutdown()  # Run all cleanup functions in reverse order
    """

    def __init__(self):
        self._resources: list[tuple[str, CleanupFunc]] = []
        self._shutting_down = False

    def register(self, name: str, cleanup: CleanupFunc) -> None:
        """Register a cleanup function.

        Args:
            name: resource name (used for logging and diagnosis)
            cleanup: the cleanup function; either a plain function or an async function
        """
        if self._shutting_down:
            logger.warning(f"Resource pool is shutting down; skipping registration: {name}")
            return
        self._resources.append((name, cleanup))

    async def shutdown(self) -> None:
        """Release all registered resources, in the reverse order of registration."""
        if self._shutting_down:
            return
        self._shutting_down = True

        for name, cleanup in reversed(self._resources):
            try:
                result = cleanup()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Failed to release resource [{name}]: {e}", exc_info=True)

        self._resources.clear()
        logger.debug("Resource pool cleared")
