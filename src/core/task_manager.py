"""Task Manager.

Unified management of async task creation, tracking, and cleanup.
"""

import asyncio
from typing import Any, Awaitable, Callable, Optional

from src.logging import get_logger

logger = get_logger()


class TaskManager:
    """Async task manager.

    Responsibilities:
    - create and track async tasks
    - cancel all tasks uniformly on shutdown
    - provide thread-safe task scheduling

    Usage:
        tm = TaskManager()
        tm.set_loop(asyncio.get_running_loop())

        # create tasks
        task = tm.spawn(some_coroutine(), "task_name")

        # thread-safe scheduling
        tm.schedule_nowait(some_function, arg1, arg2)

        # cleanup on shutdown
        await tm.cancel_all()
    """

    def __init__(self):
        self._tasks: set[asyncio.Task] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._running: bool = False

    def initialize(self, loop: asyncio.AbstractEventLoop = None) -> None:
        """Initialize the task manager.

        Args:
            loop: the event loop; the currently running loop is used when None
        """
        self._loop = loop or asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()
        self._running = True
        logger.debug("TaskManager initialized")

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """
        Get the event loop.
        """
        return self._loop

    @property
    def running(self) -> bool:
        """
        Whether it is running.
        """
        return self._running

    @property
    def shutdown_event(self) -> Optional[asyncio.Event]:
        """
        Get the shutdown event.
        """
        return self._shutdown_event

    def spawn(self, coro: Awaitable[Any], name: str) -> Optional[asyncio.Task]:
        """Create and track async tasks.

        Args:
            coro: coroutine object
            name: task name

        Returns:
            The created task object, or None if the application is shutting down
        """
        # Check whether it is shutting down
        if not self._running or (
            self._shutdown_event and self._shutdown_event.is_set()
        ):
            logger.debug(f"Skipping task creation (app is shutting down): {name}")
            return None

        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)

        def _on_done(t: asyncio.Task):
            self._tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc:
                    # done callbacks have no active exception context; pass exc itself
                    logger.error(f"Task {name} ended with error: {exc}", exc_info=exc)

        task.add_done_callback(_on_done)
        return task

    def schedule_nowait(self, fn: Callable, *args, **kwargs) -> None:
        """Safely schedule callable objects in a thread-safe manner.

        If the callable returns a coroutine, a task is created automatically.

        Args:
            fn: callable object
            *args: positional arguments
            **kwargs: keyword arguments
        """
        # Check whether it is shutting down - silently reject
        if not self._running or (
            self._shutdown_event and self._shutdown_event.is_set()
        ):
            return

        if not self._loop or self._loop.is_closed():
            # Silently skip on shutdown without printing warnings
            return

        def _runner():
            try:
                result = fn(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    task = self.spawn(
                        result, name=f"scheduled:{getattr(fn, '__name__', 'anon')}"
                    )
                    if task is None:
                        result.close()
            except Exception as e:
                logger.error(f"Scheduled callable execution failed: {e}", exc_info=True)

        self._loop.call_soon_threadsafe(_runner)

    async def wait_shutdown(self) -> None:
        """
        Wait for the shutdown signal.
        """
        if self._shutdown_event:
            await self._shutdown_event.wait()

    def request_shutdown(self) -> None:
        """
        Request shutdown.
        """
        if self._shutdown_event and not self._shutdown_event.is_set():
            self._shutdown_event.set()
            logger.info("Shutdown request received")

    async def cancel_all(self) -> None:
        """Cancel all tracked tasks.

        Wait for all tasks to finish or be canceled.
        """
        self._running = False

        if self._shutdown_event:
            self._shutdown_event.set()

        if not self._tasks:
            return

        logger.info(f"Cancelling {len(self._tasks)} task(s)...")

        # Cancel all tasks
        for task in list(self._tasks):
            if not task.done():
                task.cancel()

        # Wait for all tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        logger.info("All tasks cancelled")

    def task_count(self) -> int:
        """
        Get the current task count.
        """
        return len(self._tasks)

    def get_task_names(self) -> list[str]:
        """
        Get all task names.
        """
        return [t.get_name() for t in self._tasks if not t.done()]
