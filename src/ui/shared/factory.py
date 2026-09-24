"""Create the interface implementation by mode (returns a ViewPort)."""

import sys
from typing import TYPE_CHECKING, Optional

from src.logging import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.task_manager import TaskManager
    from src.ui.shared.viewport import ViewPort

logger = get_logger()


def create_viewport(
    mode: str,
    event_bus: "EventBus",
    task_manager: Optional["TaskManager"] = None,
) -> "ViewPort":
    """gui / cli / tui / gpio; gpio is Linux only, other platforms fall back to cli."""
    normalized = (mode or "cli").lower()

    if normalized == "gui":
        from src.ui.gui import GuiViewManager

        logger.debug("create_viewport: gui")
        return GuiViewManager(event_bus=event_bus, task_manager=task_manager)

    if normalized == "tui":
        from src.ui.tui import TuiViewManager

        logger.info("create_viewport: tui")
        return TuiViewManager(event_bus=event_bus, task_manager=task_manager)

    if normalized == "gpio":
        if sys.platform != "linux":
            logger.warning(f"GPIO is only supported on Linux (current: {sys.platform}); falling back to CLI interface")
        else:
            from src.ui.gpio import GpioViewManager

            logger.info("create_viewport: gpio")
            return GpioViewManager(event_bus=event_bus, task_manager=task_manager)

    elif normalized != "cli":
        logger.warning(f"Unknown UI mode {mode!r}; falling back to CLI")

    from src.ui.cli import CliViewManager

    logger.info("create_viewport: cli")
    return CliViewManager(event_bus=event_bus, task_manager=task_manager)
