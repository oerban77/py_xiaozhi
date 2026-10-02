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


def test_main_callable_for_console_entrypoint():
    assert callable(main.main)


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
