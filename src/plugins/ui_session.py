"""Converts UI actions such as key presses, text sends and mode switches into protocol calls."""

from pathlib import Path
from typing import TYPE_CHECKING

from src.constants.constants import AbortReason, DeviceState, ListeningMode
from src.core.event_bus import Events
from src.logging import get_logger

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext
    from src.plugins.ui_presenter import UiPresenter

logger = get_logger()
_MAX_ATTACHMENT_TEXT_CHARS = 24_000


class SessionActions:
    """Session-related operations."""

    def __init__(
        self,
        ctx: "PluginContext",
        cmd: "PluginCommands",
        presenter: "UiPresenter",
        image_analyzer=None,
        pending_image_setter=None,
    ) -> None:
        self._ctx = ctx
        self._cmd = cmd
        self._ui = presenter
        self._manual_recording = False
        self._auto_mode = False
        # Whether a conversation has already started in auto mode (the button shows "Stop Chat")
        self._auto_session_active = False
        self._bus = None
        self._image_analyzer = image_analyzer
        self._pending_image_setter = pending_image_setter

    @property
    def auto_mode(self) -> bool:
        return self._auto_mode

    @property
    def auto_session_active(self) -> bool:
        return self._auto_session_active

    @property
    def manual_recording(self) -> bool:
        return self._manual_recording

    def subscribe(self, bus) -> None:
        self._bus = bus
        bus.on(Events.UI_BUTTON_PRESS, self.press)
        bus.on(Events.UI_BUTTON_RELEASE, self.release)
        bus.on(Events.UI_MANUAL_TOGGLE, self.manual_toggle)
        bus.on(Events.UI_AUTO_TOGGLE, self.auto_toggle)
        bus.on(Events.UI_AUTO_START, self.auto_session_toggle)
        bus.on(Events.UI_ABORT_REQUEST, self.abort)
        bus.on(Events.UI_SEND_TEXT, self.send_text_from_event)
        bus.on(Events.UI_SEND_ATTACHMENT, self.send_attachment_from_event)
        bus.on(Events.UI_QUIT_REQUEST, self.request_shutdown)
        logger.info("SessionActions subscribed to UI user action events")

    def on_device_state_changed(self, state) -> None:
        # Pulled out of listening mid manual recording; reset the button
        if state != DeviceState.LISTENING and self._manual_recording:
            self._manual_recording = False
            if not self._auto_mode:
                self._ui.set_button_text("Hold to Talk")

        if self._auto_mode and state == DeviceState.IDLE and self._auto_session_active:
            self._auto_session_active = False
            self._ui.set_button_text("Start Chat")
        elif self._auto_mode and state in (DeviceState.LISTENING, DeviceState.SPEAKING):
            if not self._auto_session_active:
                self._auto_session_active = True
            self._ui.set_button_text("Stop Chat")

    async def request_shutdown(self, _data=None) -> None:
        self._cmd.request_shutdown()

    def _listen_mode(self) -> ListeningMode:
        if not self._auto_mode:
            return ListeningMode.MANUAL
        aec = self._ctx.get_config().get_config("AEC_OPTIONS.ENABLED", True)
        return ListeningMode.REALTIME if aec else ListeningMode.AUTO_STOP

    async def _ensure_listen_session(self) -> bool:
        # When idle, a direct detect is often dropped by the server; listen first, then send
        if self._ctx.is_listening() or self._ctx.is_speaking():
            return True
        if not await self._cmd.connect_protocol():
            logger.warning("Could not establish protocol connection; cancelling session action")
            return False
        mode = self._listen_mode()
        await self._cmd.start_listening(mode)
        if self._auto_mode:
            self._auto_session_active = True
            self._ui.set_button_text("Stop Chat")
        logger.debug(f"Listen session started: mode={mode}")
        return True

    async def send_text_from_event(self, data) -> None:
        if hasattr(data, "text"):
            text = data.text
        elif isinstance(data, dict):
            text = data.get("text", "")
        elif isinstance(data, str):
            text = data
        else:
            logger.warning(f"Invalid send-text data: {type(data)}")
            return
        await self.send_text(text)

    async def send_text(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False

        logger.info(f"Sending text: {text[:40]}{'...' if len(text) > 40 else ''}")

        if self._ctx.is_speaking():
            await self._cmd.abort_speaking(AbortReason.USER_INTERRUPTION)

        if not await self._ensure_listen_session():
            return False

        sent = await self._cmd.send_wake_word_detected(text)
        if sent is False:
            logger.warning("Audio channel closed while sending text; reopening it")
            await self._cmd.start_listening(self._listen_mode())
            sent = await self._cmd.send_wake_word_detected(text)
        return sent is not False

    async def send_attachment_from_event(self, data) -> None:
        if hasattr(data, "path"):
            path_value = data.path
            question = data.question
        elif isinstance(data, dict):
            path_value = data.get("path", "")
            question = data.get("question", "")
        else:
            logger.warning("Invalid send-attachment data: %s", type(data))
            await self._set_attachment_status("Invalid attachment")
            return

        try:
            path = Path(path_value).expanduser().resolve(strict=True)
            if not path.is_file():
                await self._set_attachment_status("Selected file is unavailable")
                return

            from src.mcp.tools.documents.service import (
                IMAGE_EXTENSIONS,
                TEXT_EXTENSIONS,
                document_manage,
                image_read,
            )

            extension = path.suffix.lower()
            if extension in IMAGE_EXTENSIONS:
                kind = "image"
                image_question = (question or "").strip() or "analisa"
                if self._pending_image_setter is not None:
                    self._pending_image_setter(str(path), image_question)
                    if not await self.send_text("analisa gambar"):
                        self._pending_image_setter("", "")
                        await self._set_attachment_status(
                            "Gagal mengirim permintaan analisis gambar"
                        )
                        return
                    await self._set_attachment_status(
                        "Gambar dikirim ke asisten untuk dianalisis"
                    )
                    return
                if self._image_analyzer is not None:
                    if not await self._ensure_listen_session():
                        await self._set_attachment_status(
                            "Could not connect to the vision service"
                        )
                        return
                    extracted = await self._image_analyzer(
                        str(path), image_question
                    )
                else:
                    extracted = await image_read(
                        {"path": str(path), "question": image_question}
                    )
                    if extracted.startswith("Image: "):
                        extracted = extracted.partition("\n")[2]
            elif extension in TEXT_EXTENSIONS or extension in {
                ".docx",
                ".xlsx",
                ".pdf",
            }:
                kind = "document"
                extracted = await document_manage(
                    {"action": "read", "path": str(path)}
                )
            else:
                await self._set_attachment_status("Unsupported file type")
                return

            extracted = (extracted or "").strip()
            if not extracted:
                await self._set_attachment_status("No readable content found")
                return
            if len(extracted) > _MAX_ATTACHMENT_TEXT_CHARS:
                extracted = (
                    extracted[:_MAX_ATTACHMENT_TEXT_CHARS]
                    + "\n[Attachment content truncated]"
                )

            question = (question or "").strip()
            heading = "Image analysis" if kind == "image" else "Document content"
            display_text = f"{heading}: {path.name}\n"
            if question:
                display_text += f"\n{question}\n"
            result_text = f"{display_text}\n{extracted}"
            logger.info(
                "Attachment result displayed: type=%s, file=%s, chars=%d",
                kind,
                path.name,
                len(extracted),
            )
            self._ui.set_chat_text(result_text)
            await self._set_attachment_status("Attachment processed")
        except Exception as exc:
            logger.exception("Failed to analyze a chat attachment")
            await self._set_attachment_status(f"Image analysis failed: {exc}")

    async def _set_attachment_status(self, status: str) -> None:
        if self._bus is not None:
            await self._bus.emit(Events.UI_ATTACHMENT_STATUS, status)

    async def press(self, _data=None) -> None:
        await self._cmd.connect_protocol()
        await self._cmd.start_listening(ListeningMode.MANUAL)

    async def release(self, _data=None) -> None:
        await self._cmd.stop_listening()

    async def manual_toggle(self, _data=None) -> None:
        if not self._manual_recording:
            self._manual_recording = True
            logger.debug("Manual mode: starting recording")
            self._ui.set_button_text("Send")
            await self._cmd.connect_protocol()
            await self._cmd.start_listening(ListeningMode.MANUAL)
        else:
            self._manual_recording = False
            logger.debug("Manual mode: stopping recording and sending")
            self._ui.set_button_text("Hold to Talk")
            await self._cmd.stop_listening()

    async def auto_toggle(self, _data=None) -> None:
        # Only switch auto/manual; do not automatically start listening
        self._auto_mode = not self._auto_mode
        if not self._auto_mode:
            self._auto_session_active = False
        if self._auto_mode and self._manual_recording:
            self._manual_recording = False
        self._ui.set_auto_mode(self._auto_mode)
        logger.debug(f"Mode switch: {'auto' if self._auto_mode else 'manual'}")

    async def auto_session_toggle(self, _data=None) -> None:
        # Main button: start / stop chat
        if self._auto_session_active or self._ctx.is_listening() or self._ctx.is_speaking():
            await self._stop_auto_session()
            return

        if not await self._ensure_listen_session():
            return
        logger.debug("Auto mode: starting conversation")

    async def _stop_auto_session(self) -> None:
        # Stop first to clear keep_listening, then abort, so the interruption is not resumed by the listen continuation
        try:
            if self._ctx.is_speaking():
                await self._cmd.stop_listening()
                await self._cmd.abort_speaking(AbortReason.USER_INTERRUPTION)
            else:
                await self._cmd.stop_listening()
        finally:
            self._auto_session_active = False
            self._ui.set_button_text("Start Chat")
            logger.debug("Auto mode: stopping conversation")

    async def abort(self, _data=None) -> None:
        await self._cmd.abort_speaking(AbortReason.USER_INTERRUPTION)
