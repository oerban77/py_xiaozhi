"""Creates the activation UI based on the run mode (symmetric with create_viewport)."""

from typing import Any


def create_activation_ui(mode: str, activation_service, init_result: dict) -> Any:
    """gui -> GuiActivation; tui/cli/gpio -> CliActivation.

    Args:
        mode: run mode
        activation_service: ActivationService instance
        init_result: result of initialize() (avoids re-initializing)
    """
    normalized = (mode or "cli").lower()
    if normalized == "gui":
        from src.ui.gui import GuiActivation

        return GuiActivation(activation_service, init_result)

    # tui / cli / gpio: use simple terminal interaction during activation
    from src.ui.cli import CliActivation

    return CliActivation(activation_service, init_result)
