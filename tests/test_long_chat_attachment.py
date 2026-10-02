from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.plugins.ui_session import SessionActions


def _make_session_actions():
    actions = SessionActions.__new__(SessionActions)
    actions._ctx = SimpleNamespace(is_speaking=lambda: False)
    actions._cmd = SimpleNamespace(send_wake_word_detected=AsyncMock(return_value=True))
    actions._ensure_listen_session = AsyncMock(return_value=True)
    actions.send_attachment_from_event = AsyncMock()
    return actions


@pytest.mark.asyncio
async def test_chat_text_longer_than_31_characters_uses_temporary_attachment(
    monkeypatch, tmp_path
):
    from src.ui.shared import clipboard_attachments

    text = "x" * 32
    attachment = tmp_path / "message.txt"
    saved_text = []

    def save_text(content):
        saved_text.append(content)
        return attachment

    monkeypatch.setattr(clipboard_attachments, "save_pasted_text", save_text)
    actions = _make_session_actions()

    assert await actions.send_text(text)

    request = actions.send_attachment_from_event.await_args.args[0]
    assert saved_text == [text]
    assert request.path == str(attachment)
    assert "pesan/perintah pengguna" in request.question
    assert request.use_document_tool is True
    assert not actions._cmd.send_wake_word_detected.called


@pytest.mark.asyncio
async def test_chat_text_at_31_characters_stays_on_text_channel():
    actions = _make_session_actions()
    text = "x" * 31

    assert await actions.send_text(text)

    actions._cmd.send_wake_word_detected.assert_awaited_once_with(text)
    actions.send_attachment_from_event.assert_not_awaited()