import argparse
import asyncio
import faulthandler
import locale
import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path

from src.bootstrap.container import ServiceContainer
from src.constants.system import SystemConstants
from src.logging import get_logger

logger = get_logger()

# --- Crash diagnostics -------------------------------------------------------
# A windowed (console-less) build dies silently on a fatal error, so install the
# faulthandler and an excepthook that always leaves a traceback on disk. The log
# directory resolution mirrors src.utils.resource_finder but degrades gracefully
# (this runs before the config/logging stack is available).
_CRASH_FILE_NAME = "crash.log"


def _fallback_log_dir() -> Path:
    env = os.environ.get("XIAOZHI_LOG_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    try:
        import platformdirs

        return Path(platformdirs.user_data_dir("py-xiaozhi")) / "logs"
    except Exception:
        try:
            import tempfile

            return Path(tempfile.gettempdir())
        except Exception:
            return Path(".")


def _write_crash_log(exc_type, exc_value, exc_tb) -> None:
    try:
        log_dir = _fallback_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        crash_path = log_dir / _CRASH_FILE_NAME
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        with open(crash_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {stamp} uncaught exception =====\n{tb_text}\n")
    except Exception:
        # Never let the crash handler itself raise
        pass


def _install_crash_handlers() -> None:
    # faulthandler catches native segfaults / deadlocks and prints the C-level
    # traceback to stderr (visible in a console build, and captured by PyInstaller
    # in a windowed build when disable_windowed_traceback is False).
    try:
        faulthandler.enable()
    except Exception:
        pass

    _previous_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        _write_crash_log(exc_type, exc_value, exc_tb)
        if _previous_excepthook is not None:
            try:
                _previous_excepthook(exc_type, exc_value, exc_tb)
            except Exception:
                pass

    sys.excepthook = _excepthook


_install_crash_handlers()

# Windows: force the C/C++ runtime to use UTF-8 so sherpa-onnx can read tone-pinyin files without garbled characters
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        locale.setlocale(locale.LC_ALL, ".UTF-8")
    except locale.Error:
        pass

os.environ["QSG_RHI_BACKEND"] = "opengl"
# Force qasync to use PySide6
os.environ["QT_API"] = "pyside6"
# Use the Basic style to support custom controls
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"


def _free_console() -> None:
    """Detach from the current console on Windows so GUI mode can run hidden."""
    if sys.platform != "win32":
        return

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if hasattr(kernel32, "FreeConsole"):
            kernel32.FreeConsole()
    except Exception:
        pass


def _configure_gui_console_visibility(mode: str) -> None:
    """Detach an existing console only when standard output is unavailable."""
    if mode == "gui" and sys.platform == "win32" and sys.stdout is None:
        _free_console()


def _silence_tui_bootstrap_console() -> None:
    """Prevent temporary pre-configuration loggers from writing over the TUI."""
    root_logger = logging.getLogger()
    for logger_obj in logging.Logger.manager.loggerDict.values():
        if not isinstance(logger_obj, logging.Logger):
            continue
        for handler in list(logger_obj.handlers):
            if getattr(handler, "_xiaozhi_bootstrap_handler", False):
                logger_obj.removeHandler(handler)
                handler.close()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_xiaozhi_bootstrap_handler", False):
            root_logger.removeHandler(handler)
            handler.close()
    if not root_logger.handlers:
        root_logger.addHandler(logging.NullHandler())


def parse_args(argv=None, *, default_mode="gui"):
    """Parse the command-line arguments."""
    from src.constants.system import SystemConstants

    parser = argparse.ArgumentParser(description=SystemConstants.APP_DISPLAY_NAME)
    # Run mode selection
    # - gui: graphical interface mode, using PySide6 + QML
    # - cli: command-line mode, terminal interaction (lightweight, good for headless/SSH)
    # - tui: full-screen TUI (Textual) with config editing; requires uv sync --extra tui
    # - gpio: GPIO button mode, Linux only (Raspberry Pi), controlled by physical buttons
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--gui",
        dest="gui_flag",
        action="store_true",
        help="Launch in graphical GUI mode",
    )
    mode_group.add_argument(
        "--cli",
        dest="cli_flag",
        action="store_true",
        help="Launch in command-line mode without GUI",
    )
    mode_group.add_argument(
        "--tui",
        dest="tui_flag",
        action="store_true",
        help="Launch in full-screen terminal mode",
    )
    mode_group.add_argument(
        "--gpio",
        dest="gpio_flag",
        action="store_true",
        help="Launch in GPIO button mode (Linux only)",
    )
    parser.add_argument(
        "mode_positional",
        nargs="?",
        choices=["gui", "cli", "tui", "gpio"],
        help="Shortcut mode: gui | cli | tui | gpio",
    )
    parser.add_argument(
        "--mode",
        dest="mode_flag",
        choices=["gui", "cli", "tui", "gpio"],
        default=None,
        help=f"Run mode (default {default_mode}): gui / cli / tui (full-screen terminal) / gpio (Linux only)",
    )
    parser.add_argument(
        "--protocol",
        choices=["mqtt", "websocket"],
        default="websocket",
        metavar="PROTOCOL",
        help="Transport protocol: mqtt or websocket (default websocket; must be written as --protocol mqtt)",
    )
    parser.add_argument(
        "--skip-activation",
        action="store_true",
        help="Skip the activation flow and start the app directly (debug only)",
    )
    parser.add_argument(
        "--start-minimized",
        action="store_true",
        help="Start the GUI minimized to the system tray",
    )

    args = parser.parse_args(argv)

    selected_flags = [
        mode_name
        for mode_name, is_set in (
            ("gui", args.gui_flag),
            ("cli", args.cli_flag),
            ("tui", args.tui_flag),
            ("gpio", args.gpio_flag),
        )
        if is_set
    ]

    if selected_flags and args.mode_flag:
        parser.error("Use either --gui/--cli/--tui/--gpio or --mode, not both.")
    if selected_flags and args.mode_positional:
        parser.error("Use either the positional MODE argument or the mode flags, not both.")
    if args.mode_flag and args.mode_positional:
        parser.error("Use either the positional MODE argument or --mode, not both.")

    if selected_flags:
        args.mode = selected_flags[0]
    else:
        args.mode = args.mode_flag or args.mode_positional or default_mode
    return args


async def handle_activation(mode: str) -> bool:
    """Handle the device activation flow.

    Args:
        mode: the run mode: "gui", "cli", "tui", or "gpio"

    Returns:
        bool: whether the activation succeeded
    """
    try:
        from src.activation import ActivationService, create_activation_ui

        logger.info("Checking the device activation flow...")
        activation_service = await ActivationService.create()
        init_result = await activation_service.initialize()

        if not init_result.get("success", False):
            logger.error(f"Initialization failed: {init_result.get('error', 'unknown error')}")
            return False

        if not init_result.get("need_activation_ui", False):
            logger.info("Device is already activated; no activation flow needed")
            return True

        ui = create_activation_ui(mode, activation_service, init_result)
        return await ui.run()

    except Exception as e:
        logger.error(f"Activation flow error: {e}", exc_info=True)
        return False


async def start_app(mode: str, protocol: str, skip_activation: bool) -> int:
    """Unified entry point for starting the application."""
    global _container  # used for SIGINT handling
    logger.info(f"Starting {SystemConstants.APP_DISPLAY_NAME}")

    # Handle the activation flow
    if not skip_activation:
        activation_success = await handle_activation(mode)
        if not activation_success:
            logger.error("Device activation failed; exiting")
            return 1
    else:
        logger.warning("Skipping the activation flow (debug mode)")

    # Create and start the application
    _container = ServiceContainer()
    return await _container.run(mode=mode, protocol=protocol)


# Global container reference, used for SIGINT handling
_container = None


def _mode_override_argv(argv=None, *, forced_mode: str | None = None):
    """Create a command-line argument list that forces the selected mode."""
    if forced_mode is None:
        return list(argv) if argv is not None else None

    normalized = str(forced_mode).lower()
    if normalized not in {"gui", "cli", "tui", "gpio"}:
        raise ValueError(f"Unsupported forced mode: {forced_mode!r}")

    base = [] if argv is None else list(argv)
    return [f"--{normalized}", *base]


def main(argv=None, *, default_mode="gui") -> int:
    """Application entry point for installed console scripts and direct execution."""
    if isinstance(argv, argparse.Namespace):
        args = argv
    else:
        args = parse_args(argv, default_mode=default_mode)

    _configure_gui_console_visibility(args.mode)

    os.environ["XIAOZHI_START_MINIMIZED"] = "1" if args.start_minimized else "0"

    if args.mode == "tui":
        _silence_tui_bootstrap_console()

    from src.utils.config_manager import initialize_config  # noqa: E402

    initialize_config()

    from src.logging import load_logging_config, setup_logging  # noqa: E402

    # CLI/TUI mode disables console log output (the interface takes over)
    setup_logging(
        enable_console=(args.mode not in ("cli", "tui")),
        config=load_logging_config(),
    )

    global logger
    logger = get_logger()

    exit_code = 1
    try:
        # Detect a Wayland environment and configure the Qt platform plugin
        is_wayland = (
            os.environ.get("WAYLAND_DISPLAY")
            or os.environ.get("XDG_SESSION_TYPE") == "wayland"
        )

        if args.mode == "gui" and is_wayland:
            if "QT_QPA_PLATFORM" not in os.environ:
                os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
                logger.info("Wayland detected: setting QT_QPA_PLATFORM=wayland;xcb")
            os.environ.setdefault("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
            logger.info("Wayland environment detected; compatibility config applied")

        # Signal handling
        try:
            if hasattr(signal, "SIGTRAP"):
                signal.signal(signal.SIGTRAP, signal.SIG_IGN)
        except Exception:
            pass

        if args.mode == "gui":
            # GUI mode: PySide6 + qasync
            try:
                import qasync
                from PySide6.QtWidgets import QApplication
            except ImportError as e:
                logger.error(
                    "GUI mode requires PySide6 + qasync, which are not installed in this environment.\n"
                    "Install the GUI dependencies with the project venv and retry:\n"
                    "  uv sync --extra gui\n"
                    "  # or: pip install '.[gui]'\n"
                    "Then:\n"
                    "  uv run python main.py\n"
                    "  # or: .venv/bin/python main.py\n"
                    "If you do not need the GUI, use:\n"
                    "  python main.py --mode cli\n"
                    "  python main.py --mode tui   # requires uv sync --extra tui\n"
                    f"(original error: {e})"
                )
                return 1

            qt_app = QApplication.instance() or QApplication(sys.argv)
            qt_app.setQuitOnLastWindowClosed(False)

            loop = qasync.QEventLoop(qt_app)
            asyncio.set_event_loop(loop)
            logger.info("Created the PySide6 + qasync event loop")

            shutdown_state = {"requested": False}

            def handle_sigint(*_):
                if shutdown_state["requested"]:
                    return
                shutdown_state["requested"] = True
                logger.info("SIGINT received, shutting down...")

                try:
                    if _container and _container.tasks:
                        _container.tasks.request_shutdown()
                    else:
                        if loop.is_running():
                            loop.call_soon_threadsafe(qt_app.quit)
                except Exception:
                    qt_app.quit()

            signal.signal(signal.SIGINT, handle_sigint)

            try:
                with loop:
                    exit_code = loop.run_until_complete(
                        start_app(args.mode, args.protocol, args.skip_activation)
                    )
            except RuntimeError as e:
                if "Event loop stopped before Future completed" in str(e):
                    logger.debug("The event loop terminated normally")
                    exit_code = 0
                else:
                    raise
        else:
            if args.mode == "tui":
                try:
                    import textual  # noqa: F401
                except ImportError as e:
                    logger.error(
                        "TUI mode requires textual. Please run:\n"
                        "  uv sync --extra tui\n"
                        "  pip install '.[tui]'\n"
                        "On a headless machine / over SSH, keep using: python main.py --mode cli\n"
                        f"(original error: {e})"
                    )
                    return 1

            shutdown_state = {"requested": False}

            def handle_sigint_cli(*_):
                if shutdown_state["requested"]:
                    logger.warning("SIGINT received again; forcing exit")
                    os._exit(130)
                shutdown_state["requested"] = True
                logger.info("SIGINT received, shutting down...")
                try:
                    if _container and _container.tasks:
                        _container.tasks.request_shutdown()
                except Exception:
                    pass

            signal.signal(signal.SIGINT, handle_sigint_cli)
            exit_code = asyncio.run(
                start_app(args.mode, args.protocol, args.skip_activation)
            )

    except KeyboardInterrupt:
        logger.info("Interrupted by the user")
        exit_code = 0
    except Exception as e:
        logger.error(f"The application exited with an error: {e}", exc_info=True)
        _write_crash_log(type(e), e, e.__traceback__)
        exit_code = 1
    finally:
        return exit_code


def main_cli(argv=None) -> int:
    """Console entry point for CLI/headless mode."""
    return main(_mode_override_argv(argv, forced_mode="cli"))


def main_gui(argv=None) -> int:
    """Console entry point for GUI mode."""
    return main(_mode_override_argv(argv, forced_mode="gui"))


def main_tui(argv=None) -> int:
    """Console entry point for TUI mode."""
    return main(_mode_override_argv(argv, forced_mode="tui"))


def main_gpio(argv=None) -> int:
    """Console entry point for GPIO mode."""
    return main(_mode_override_argv(argv, forced_mode="gpio"))


if __name__ == "__main__":
    raise SystemExit(main())
