"""EventBus bridge - bidirectional conversion between Python signals and QML signals."""

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from src.core.event_bus import EventBus, Events
from src.logging import get_logger

logger = get_logger()


class EventBridge(QObject):
    """Bidirectional bridge between the EventBus and QML.

    QML -> Python: QML calls a slot, which emits an EventBus event
    Python -> QML: an EventBus event triggers a Python Signal that QML connects to

    Device activation is handled by a separate GuiActivation window; the main interface bridge no longer carries activation signals.
    """

    # ========== Python -> QML signals ==========

    # Window control
    showWindow = Signal()
    hideWindow = Signal()
    showSettingsWindow = Signal()  # Show the settings window
    attachmentStatusChanged = Signal(str)

    # ========== Construction ==========

    def __init__(self, event_bus: EventBus, task_manager=None, parent: QObject | None = None):
        super().__init__(parent)
        self._event_bus = event_bus
        self._task_manager = task_manager
        self._event_bus.on(Events.UI_ATTACHMENT_STATUS, self._on_attachment_status)

    def _emit_event(self, event: str, data=None):
        """Safely emit an EventBus event, scheduling it onto the asyncio loop from the Qt main thread."""
        if self._task_manager is None:
            logger.error("EventBridge: TaskManager not injected; cannot emit event")
            return

        def do_emit():
            try:
                task_name = f"bridge:{event.split('.')[-1]}" if '.' in event else f"bridge:{event}"
                self._task_manager.spawn(self._event_bus.emit(event, data), name=task_name)
            except Exception as e:
                logger.warning(
                    f"EventBridge: failed to emit event {event}: {e}",
                    exc_info=True,
                )

        # Use QTimer.singleShot to make sure it runs in the Qt event loop
        QTimer.singleShot(0, do_emit)

    # ========== QML → Python (Slots) ==========

    @Slot()
    def onButtonPress(self):
        """Manual-mode button pressed."""
        logger.debug("EventBridge: button pressed")
        self._emit_event(Events.UI_BUTTON_PRESS)

    @Slot()
    def onButtonRelease(self):
        """Manual-mode button released."""
        logger.debug("EventBridge: button released")
        self._emit_event(Events.UI_BUTTON_RELEASE)

    @Slot()
    def onManualToggle(self):
        """Manual-mode recording toggle (click to start/stop)."""
        logger.debug("EventBridge: manual recording toggle")
        self._emit_event(Events.UI_MANUAL_TOGGLE)

    @Slot()
    def onAutoToggle(self):
        """Auto-mode toggle."""
        logger.debug("EventBridge: auto mode toggle")
        self._emit_event(Events.UI_AUTO_TOGGLE)

    @Slot()
    def onAutoStart(self):
        """Auto mode: start or stop the conversation."""
        logger.debug("EventBridge: auto mode start/stop conversation")
        self._emit_event(Events.UI_AUTO_START)

    @Slot()
    def onAbort(self):
        """Interrupt request."""
        logger.debug("EventBridge: interrupt request")
        self._emit_event(Events.UI_ABORT_REQUEST)

    @Slot(str)
    def onSendText(self, text: str):
        """Send text."""
        if text.strip():
            logger.debug(f"EventBridge: Sending text: {text[:20]}...")
            from src.ui.shared.events import UISendTextRequest
            self._emit_event(Events.UI_SEND_TEXT, UISendTextRequest(text=text))

    @Slot(str, str)
    def onSendAttachment(self, path: str, question: str):
        """Analyze a user-selected local file, then send its extracted content."""
        from PySide6.QtCore import QUrl

        file_url = QUrl(path)
        local_path = file_url.toLocalFile() if file_url.isLocalFile() else path
        if local_path.strip():
            from src.ui.shared.events import UISendAttachmentRequest

            self._emit_event(
                Events.UI_SEND_ATTACHMENT,
                UISendAttachmentRequest(path=local_path, question=question),
            )

    async def _on_attachment_status(self, status):
        self.attachmentStatusChanged.emit(str(status or ""))

    @Slot()
    def onQuitRequest(self):
        """Quit request."""
        logger.info("EventBridge: quit request")
        self._emit_event(Events.UI_QUIT_REQUEST)

    @Slot()
    def onOpenSettings(self):
        """Open the settings window - emits the signal directly to QML."""
        logger.debug("EventBridge: open settings window")
        self.showSettingsWindow.emit()

    @Slot()
    def onMuteToggle(self):
        """Toggle speaker mute.

        Runs on the Qt main thread; the blocking volume backend call is pushed onto the
        asyncio loop via _emit_event so the UI never freezes.
        """
        logger.debug("EventBridge: mute toggle")
        self._emit_event(Events.UI_MUTE_TOGGLE)
