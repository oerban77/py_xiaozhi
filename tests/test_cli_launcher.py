import pytest

import main


@pytest.mark.parametrize(
    ("argv", "expected_mode"),
    [
        (["cli"], "cli"),
        (["--mode", "gui"], "gui"),
        (["--mode", "tui"], "tui"),
        (["--cli"], "cli"),
        (["--gui"], "gui"),
        (["--gpio"], "gpio"),
    ],
)
def test_parse_args_supports_mode_selection(argv, expected_mode):
    args = main.parse_args(argv)
    assert args.mode == expected_mode


@pytest.mark.parametrize(
    ("argv", "expected_mode"),
    [([], "tui"), (["--cli"], "cli"), (["--mode", "cli"], "cli")],
)
def test_terminal_default_mode_can_be_overridden(argv, expected_mode):
    args = main.parse_args(argv, default_mode="tui")
    assert args.mode == expected_mode


def test_default_mode_remains_gui():
    assert main.parse_args([]).mode == "gui"


def test_main_callable_for_console_entrypoint():
    assert callable(main.main)


@pytest.mark.asyncio
async def test_tui_chat_input_navigates_history_and_restores_draft():
    pytest.importorskip("textual")
    from textual.app import App, ComposeResult

    from src.ui.tui.app import ClipboardAttachmentInput

    class InputHarness(App[None]):
        def compose(self) -> ComposeResult:
            yield ClipboardAttachmentInput(lambda _text: False, id="chat-input")

    app = InputHarness()
    async with app.run_test() as pilot:
        chat_input = app.query_one("#chat-input", ClipboardAttachmentInput)
        chat_input.add_history("first message")
        chat_input.add_history("second message")
        chat_input.value = "unsent draft"
        chat_input.focus()

        await pilot.press("up")
        assert chat_input.value == "second message"
        await pilot.press("up")
        assert chat_input.value == "first message"
        await pilot.press("down")
        assert chat_input.value == "second message"
        await pilot.press("down")
        assert chat_input.value == "unsent draft"


@pytest.mark.asyncio
@pytest.mark.parametrize("clipboard_text", ["right-click paste", ""])
async def test_tui_chat_input_right_click_pastes_clipboard(monkeypatch, clipboard_text):
    pytest.importorskip("textual")
    import pyperclip
    from textual.app import App, ComposeResult

    from src.ui.tui.app import ClipboardAttachmentInput

    attachments = []

    def handle_paste(text):
        attachments.append(text)
        return not text

    class InputHarness(App[None]):
        def compose(self) -> ComposeResult:
            yield ClipboardAttachmentInput(handle_paste, id="chat-input")

    monkeypatch.setattr(pyperclip, "paste", lambda: clipboard_text)
    app = InputHarness()
    async with app.run_test() as pilot:
        chat_input = app.query_one("#chat-input", ClipboardAttachmentInput)
        await pilot.click("#chat-input", button=3)

        assert chat_input.value == clipboard_text
        assert attachments == [clipboard_text]


def test_tui_pasted_text_stays_in_chat_input():
    from src.ui.tui.app import XiaozhiTuiApp

    app = XiaozhiTuiApp.__new__(XiaozhiTuiApp)
    app._dispatch_command = lambda _command: pytest.fail(
        "Pasted text should not be dispatched as an attachment"
    )

    assert app._handle_clipboard_paste("pasted text from another source") is False


def test_parse_args_supports_starting_minimized():
    args = main.parse_args(["--start-minimized"])
    assert args.mode == "gui"
    assert args.start_minimized is True


def test_gui_mode_hides_console_on_windows(monkeypatch):
    called = {}

    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main, "_free_console", lambda: called.setdefault("free", True))

    main._configure_gui_console_visibility("gui")

    assert called == {"free": True}


def test_tui_bootstrap_logging_does_not_write_to_terminal():
    import subprocess
    import sys

    script = "\n".join(
        [
            "import io, logging",
            "from main import _silence_tui_bootstrap_console",
            "logger = logging.getLogger('test.tui.bootstrap')",
            "stream = io.StringIO()",
            "handler = logging.StreamHandler(stream)",
            "handler._xiaozhi_bootstrap_handler = True",
            "logger.addHandler(handler)",
            "_silence_tui_bootstrap_console()",
            "logger.info('startup debug')",
            "assert not stream.getvalue()",
            "print('suppressed')",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "suppressed"
    assert result.stderr == ""


def test_tui_ui_starts_before_wake_word_model_loading():
    from src.plugins.ui import UIPlugin
    from src.plugins.wake_word import WakeWordPlugin

    assert UIPlugin.priority < WakeWordPlugin.priority


def test_tui_workspace_setting_persists_new_directory(monkeypatch, tmp_path):
    from src.ui.tui import settings_data

    workspace = tmp_path / "coding-project"
    workspace.mkdir()

    class ConfigStub:
        updates = None

        def update_configs(self, updates):
            self.updates = updates
            return True

    config = ConfigStub()
    monkeypatch.setattr(settings_data, "get_config", lambda: config)

    workspace_fields = [
        field
        for _, fields in settings_data.SETTING_SECTIONS
        for field in fields
        if field.path == "CODING.WORKSPACE"
    ]
    ok, message = settings_data.save_settings(
        {"CODING.WORKSPACE": str(workspace)}
    )

    assert len(workspace_fields) == 1
    assert ok, message
    assert config.updates == {"CODING.WORKSPACE": str(workspace.resolve())}


def test_mode_shortcut_entrypoints_exist():
    assert callable(main.main_cli)
    assert callable(main.main_gui)
    assert callable(main.main_tui)
    assert callable(main.main_gpio)


def test_cli_dashboard_builds_modern_status_block():
    from src.ui.cli.display import CLIDisplay

    display = CLIDisplay()
    display._dash_status = "Speaking..."
    display._dash_connected = True
    display._dash_auto_mode = False
    display._dash_emotion = "happy"
    display._dash_text = "Greeting"
    display._dash_music = "Lo-fi"

    lines = display.build_dashboard_lines()

    assert any("Status:" in line for line in lines)
    assert any("Mode:" in line for line in lines)
    assert any("Emotion:" in line for line in lines)
    assert any("Music:" in line for line in lines)
    assert any(line.startswith("╭") or line.startswith("┌") for line in lines)
