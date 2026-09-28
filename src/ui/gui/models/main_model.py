"""Main window ViewModel."""

from PySide6.QtCore import Property, Signal

from src.ui.gui.models.base_model import BaseModel


class MainModel(BaseModel):
    """Main window data model."""

    # Signals
    ttsTextChanged = Signal()
    musicLineChanged = Signal()
    emotionUrlChanged = Signal()
    statusTextChanged = Signal()
    connectedChanged = Signal()
    autoModeChanged = Signal()
    modeTextChanged = Signal()
    buttonTextChanged = Signal()
    mutedChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tts_text = ""
        self._music_line = ""
        self._emotion_url = ""
        self._status_text = ""
        self._connected = False
        self._auto_mode = False
        self._mode_text = "Manual"
        self._button_text = "Hold to Talk"
        self._muted = False

    # ========== Properties ==========

    @Property(str, notify=ttsTextChanged)
    def ttsText(self) -> str:
        # The legacy property name is still ttsText, but it actually holds the conversation content
        return self._tts_text

    @Property(str, notify=musicLineChanged)
    def musicLine(self) -> str:
        return self._music_line

    @Property(str, notify=emotionUrlChanged)
    def emotionUrl(self) -> str:
        return self._emotion_url

    @Property(str, notify=statusTextChanged)
    def statusText(self) -> str:
        return self._status_text

    @Property(bool, notify=connectedChanged)
    def connected(self) -> bool:
        return self._connected

    @Property(bool, notify=autoModeChanged)
    def autoMode(self) -> bool:
        return self._auto_mode

    @Property(str, notify=modeTextChanged)
    def modeText(self) -> str:
        return self._mode_text

    @Property(str, notify=buttonTextChanged)
    def buttonText(self) -> str:
        return self._button_text

    @Property(bool, notify=mutedChanged)
    def muted(self) -> bool:
        return self._muted

    # ========== Setters ==========

    def set_chat_text(self, text: str):
        if self._tts_text != text:
            self._tts_text = text
            self.ttsTextChanged.emit()

    def set_music_line(self, text: str):
        if self._music_line != text:
            self._music_line = text
            self.musicLineChanged.emit()

    def set_emotion_url(self, url: str):
        if self._emotion_url != url:
            self._emotion_url = url
            self.emotionUrlChanged.emit()

    def set_status(self, status: str, connected: bool):
        status_changed = self._status_text != status
        connected_changed = self._connected != connected

        if status_changed:
            self._status_text = status
            self.statusTextChanged.emit()

        if connected_changed:
            self._connected = connected
            self.connectedChanged.emit()

    def set_auto_mode(self, auto: bool):
        # Default button label; during a conversation Session changes it to "Stop Chat"
        if self._auto_mode != auto:
            self._auto_mode = auto
            self._mode_text = "Auto" if auto else "Manual"
            self._button_text = "Start Chat" if auto else "Hold to Talk"
            self.autoModeChanged.emit()
            self.modeTextChanged.emit()
            self.buttonTextChanged.emit()

    def set_button_text(self, text: str):
        if self._button_text != text:
            self._button_text = text
            self.buttonTextChanged.emit()

    def set_muted(self, muted: bool):
        if self._muted != bool(muted):
            self._muted = bool(muted)
            self.mutedChanged.emit()
