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
        pending_document_setter=None,
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
        self._pending_document_setter = pending_document_setter

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

        if len(text) > 31:
            from src.ui.shared.clipboard_attachments import save_pasted_text
            from src.ui.shared.events import UISendAttachmentRequest

            try:
                attachment_path = save_pasted_text(text)
            except Exception as exc:
                logger.exception("Could not save long chat text as an attachment")
                await self._set_attachment_status(
                    f"Could not attach long message: {exc}"
                )
                return False

            logger.info(
                "Routing long chat message through temporary attachment: chars=%d",
                len(text),
            )
            await self.send_attachment_from_event(
                UISendAttachmentRequest(
                    path=str(attachment_path),
                    question=(
                        "Ini pesan/perintah pengguna. Baca isinya dan jalankan "
                        "permintaan yang tertulis."
                    ),
                    use_document_tool=True,
                )
            )
            return True

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

    @staticmethod
    def _should_use_document_tool_for_image(question: str, use_document_tool: bool) -> bool:
        """Prefer OCR/document flow for text-heavy images; use vision for scene/object photos."""
        if use_document_tool:
            return True

        q = (question or "").strip().lower()
        if not q:
            return False

        text_keywords = (
            "ocr",
            "read text",
            "baca teks",
            "baca tulisan",
            "baca kuitansi",
            "baca struk",
            "baca slip",
            "baca nota",
            "cek struk",
            "cek kuitansi",
            "cek nota",
            "extract text",
            "what is written",
            "what does it say",
            "struk",
            "receipt",
            "bank note",
            "invoice",
            "nota",
            "kuitansi",
            "slip",
            "transfer",
            "nomor rekening",
            "rekening",
            "kode",
            "read the text",
            "text on the image",
            "text in this image",
            "tulisan",
            "nomor",
            "transaksi",
            "dokumen",
            "total bayar",
            "jumlah pembayaran",
            "jumlah transfer",
            "faktur",
            "bukti pembayaran",
            "bukti transfer",
            "saldo",
            "nominal",
            "norek",
            "no rekening",
            "no. rekening",
        )
        return any(keyword in q for keyword in text_keywords)

    async def send_attachment_from_event(self, data) -> None:
        use_document_tool = False
        if hasattr(data, "path"):
            path_value = data.path
            question = data.question
            use_document_tool = bool(getattr(data, "use_document_tool", False))
        elif isinstance(data, dict):
            path_value = data.get("path", "")
            question = data.get("question", "")
            use_document_tool = bool(data.get("use_document_tool", False))
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
                BINARY_EXTENSIONS,
                IMAGE_EXTENSIONS,
                TEXT_EXTENSIONS,
                document_manage,
                image_read,
            )

            extension = path.suffix.lower()
            if extension in IMAGE_EXTENSIONS:
                kind = "image"
                image_question = (question or "").strip() or "analisa"
                prefer_document = self._should_use_document_tool_for_image(
                    image_question, use_document_tool
                )
                if prefer_document:
                    kind = "document"
                    if self._pending_document_setter is not None:
                        self._pending_document_setter(str(path), image_question)
                        prompt = "baca lampiran"
                        if not await self.send_text(prompt):
                            self._pending_document_setter("", "")
                            await self._set_attachment_status(
                                "Gagal mengirim permintaan baca dokumen"
                            )
                            return
                        await self._set_attachment_status(
                            "Dokumen dikirim ke asisten untuk dibaca"
                        )
                        return
                    extracted = await document_manage(
                        {"action": "read", "path": str(path), "query": image_question}
                    )
                else:
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
            elif (
                extension in TEXT_EXTENSIONS
                or extension in BINARY_EXTENSIONS
                or extension in IMAGE_EXTENSIONS
            ):
                kind = "document"
                if self._pending_document_setter is not None:
                    self._pending_document_setter(str(path), question or "")
                    # The detect channel rejects texts >= 32 chars ("Detect is
                    # only for wake words, do not send long texts"), so the
                    # prompt must stay short. The attached file is delivered to
                    # document_manage through the pending-document queue, and
                    # the tool description tells the LLM to use document_manage
                    # for any attached document.
                    #
                    # Keep the detect text as a short command. The question is
                    # already included in the pending-document tool context.
                    prompt = "baca lampiran"
                    if not await self.send_text(prompt):
                        self._pending_document_setter("", "")
                        await self._set_attachment_status(
                            "Gagal mengirim permintaan baca dokumen"
                        )
                        return
                    await self._set_attachment_status(
                        "Dokumen dikirim ke asisten untuk dibaca"
                    )
                    return
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
