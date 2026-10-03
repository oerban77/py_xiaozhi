# -*- coding: utf-8 -*-
"""Tests for RTL (Arabic/Hebrew) rendering support in the TUI."""

import pytest

from rich.text import Text

from src.utils.bidi_text import (
    bidi_visual,
    contains_rtl,
    reshape_arabic,
    to_visual,
)


def test_latin_text_is_unchanged():
    assert to_visual("hello world") == "hello world"
    assert to_visual("") == ""
    assert to_visual("123 abc") == "123 abc"


def test_contains_rtl_detects_arabic():
    assert contains_rtl("مرحبا")
    assert not contains_rtl("hello")


def test_arabic_visual_order_is_reversed():
    # Logical order (as typed) is left-to-right in memory; visual order must
    # read right-to-left, so the reshaped glyph sequence is reversed.
    logical = "مرحبا"
    visual = to_visual(logical)
    assert visual != logical
    # The reshaped string should contain Arabic presentation forms.
    assert any(
        (0xFB50 <= ord(ch) <= 0xFDFF) or (0xFE70 <= ord(ch) <= 0xFEFF)
        for ch in visual
    )


def test_arabic_reshaping_connects_letters():
    # "ب" followed by "ب" should produce a medial + final pair, i.e. the
    # presentation forms differ from the isolated forms.
    reshaped = reshape_arabic("بب")
    assert len(reshaped) == 2
    assert ord(reshaped[0]) != 0x0628  # not isolated
    assert ord(reshaped[1]) != 0x0628  # not isolated


def test_mixed_rtl_ltr_keeps_latin_in_order():
    # Latin letters inside an RTL line keep their natural order.
    visual = to_visual("abc مرحبا")
    assert "abc" in visual


def test_bidi_log_message_preserves_markup():
    from src.ui.tui.app import XiaozhiTuiApp

    message = "[bold cyan]Title[/] مرحبا"
    result = XiaozhiTuiApp._bidi_log_message(message)
    assert result.startswith("[bold cyan]Title[/]")
    assert "مرحبا" not in result  # Arabic was reordered


async def test_input_widget_renders_visual_rtl_value():
    from src.ui.tui.app import ClipboardAttachmentInput, XiaozhiTuiApp

    app = XiaozhiTuiApp()
    async with app.run_test() as pilot:
        field = ClipboardAttachmentInput(lambda _: False, value="مرحبا")
        await pilot.app.mount(field)
        assert field._value.plain == to_visual("مرحبا")


def test_chat_render_uses_right_justified_rtl_text():
    from src.ui.tui.app import XiaozhiTuiApp

    rendered = XiaozhiTuiApp._render_chat_line("مرحبا بالعالم")
    assert isinstance(rendered, Text)
    assert rendered.justify == "right"
    assert rendered.plain.startswith("Conversation: ")
    assert any(0xFB50 <= ord(ch) <= 0xFEFF for ch in rendered.plain)
