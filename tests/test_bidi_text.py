"""Tests for RTL (Arabic/Hebrew) rendering support in the TUI."""

import unicodedata

from src.utils.bidi_text import (
    bidi_positions,
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


def test_arabic_reshaper_preserves_harakat_and_character_positions():
    logical = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
    reshaped = reshape_arabic(logical)

    assert len(reshaped) == len(logical)
    assert [char for char in reshaped if unicodedata.category(char).startswith("M")] == [
        char for char in logical if unicodedata.category(char).startswith("M")
    ]


def test_arabic_diacritics_stay_attached_when_reordered():
    logical = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
    visual = to_visual(logical)
    character_positions, _ = bidi_positions(logical)

    assert visual != logical
    for index, char in enumerate(logical):
        if not unicodedata.category(char).startswith("M"):
            continue
        base_index = index - 1
        while base_index >= 0 and unicodedata.category(logical[base_index]).startswith("M"):
            base_index -= 1
        assert base_index >= 0
        base_position = character_positions[base_index]
        assert character_positions[index] == base_position + index - base_index
        assert not unicodedata.category(visual[base_position]).startswith("M")


def test_mixed_rtl_ltr_keeps_latin_in_order():
    # Latin letters inside an RTL line keep their natural order.
    visual = to_visual("abc مرحبا")
    assert "abc" in visual


def test_rtl_to_ltr_boundary_keeps_separator_space():
    logical = "السلام عليكم Semoga membantu!"
    visual = to_visual(logical)

    assert visual.endswith(" Semoga membantu!")
    assert visual.count(" ") == logical.count(" ")


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


async def test_rtl_input_cursor_mouse_and_selection_follow_visual_order():
    from src.ui.tui.app import ClipboardAttachmentInput, XiaozhiTuiApp

    app = XiaozhiTuiApp()
    async with app.run_test() as pilot:
        field = ClipboardAttachmentInput(lambda _: False, value="مرحبا")
        field.styles.width = 20
        await pilot.app.mount(field)
        await pilot.pause()

        field.cursor_position = 0
        assert field._cursor_offset == len(field._value.plain) + 1
        assert field._cell_offset_to_index(0) == len(field.value)

        field.action_cursor_left()
        assert field.cursor_position == 1
        field.action_cursor_right()
        assert field.cursor_position == 0

        field.action_cursor_left(select=True)
        assert field.selection == (0, 1)
        assert field.selected_text == field.value[:1]
        field.action_cursor_right(select=True)
        assert field.selection == (0, 0)

        field.selection = type(field.selection)(1, 4)
        assert field.selected_text == field.value[1:4]
        assert field.render_line(0).cell_length == field.scrollable_content_region.width + 1


def test_chat_render_keeps_ltr_prefix_and_rtl_run():
    from src.ui.tui.app import XiaozhiTuiApp

    rendered = XiaozhiTuiApp._render_chat_line("مرحبا بالعالم")
    assert rendered.startswith("Conversation: ")
    assert any(0xFB50 <= ord(ch) <= 0xFEFF for ch in rendered)


def test_bidi_visual_keeps_ltr_prefix_left_and_rtl_segment_visual():
    visual = bidi_visual("Conversation: مرحبا بالعالم")
    assert visual.startswith("Conversation: ")
    assert "Conversation:" in visual
    assert any(0xFB50 <= ord(ch) <= 0xFEFF for ch in visual)
