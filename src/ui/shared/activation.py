"""Activation UI base class.

Shares the "get code -> present -> service.activate -> result" flow; subclasses only handle presentation.
"""


class BaseActivation:
    """Activation UI base class (not an ABC, to avoid conflicts with the PySide6 QObject metaclass).

    Args:
        activation_service: the ActivationService instance (obtained from create(), not a singleton)
        init_result: the initialize() result passed in by handle_activation, to avoid repeated initialization
    """

    def __init__(self, activation_service=None, init_result=None):
        # The parameters are optional: with PySide6 multiple inheritance, the super chain of QObject.__init__ may call this with no arguments
        self._service = activation_service
        self._init_result = init_result

    def needs_activation(self) -> bool:
        """Whether the activation UI flow is needed."""
        if self._init_result is None:
            return False
        return bool(self._init_result.get("need_activation_ui", False))

    async def _core_activate(self) -> bool:
        """Core activation: get the code -> present in the UI -> service.activate (includes clipboard/announcement side effects)."""
        if self._service is None:
            self._show_error("The activation service is not initialized")
            return False

        data = self._service.get_activation_data()
        if not data:
            self._show_error("Failed to retrieve the activation data")
            return False

        self._show_code(data)
        success = await self._service.activate(data)
        self._show_result(success)
        return success

    async def run(self) -> bool:
        """Run the activation flow."""
        raise NotImplementedError

    def _show_code(self, data: dict) -> None:
        """Present the activation verification code (CLI prints / GUI writes to the Model)."""
        raise NotImplementedError

    def _show_result(self, success: bool) -> None:
        """Present the activation result."""
        raise NotImplementedError

    def _show_error(self, msg: str) -> None:
        """Present an error (optional override)."""
        pass
