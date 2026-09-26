import argparse
import asyncio
import faulthandler
import locale
import os
import signal
import sys
import time
import traceback
from pathlib import Path

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


def parse_args():
    """Parse the command-line arguments."""
    from src.constants.system import SystemConstants

    parser = argparse.ArgumentParser(description=SystemConstants.APP_DISPLAY_NAME)
    # Run mode selection
    # - gui: graphical interface mode, using PySide6 + QML
    # - cli: command-line mode, terminal interaction (lightweight, good for headless/SSH)
    # - tui: full-screen TUI (Textual) with config editing; requires uv sync --extra tui
    # - gpio: GPIO button mode, Linux only (Raspberry Pi), controlled by physical buttons
    parser.add_argument(
        "--mode",
        choices=["gui", "cli", "tui", "gpio"],
        default="gui",
        help="Run mode (default gui): gui / cli / tui (full-screen terminal) / gpio (Linux only)",
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
    return parser.parse_args()


# Parse the arguments first, then initialize the config and logging (ConfigManager must not be a lazy singleton)
_args = parse_args()

from src.utils.config_manager import initialize_config  # noqa: E402

initialize_config()

from src.logging import load_logging_config, setup_logging  # noqa: E402

# CLI/TUI mode disables console log output (the interface takes over)
setup_logging(
    enable_console=(_args.mode not in ("cli", "tui")),
    config=load_logging_config(),
)

from src.bootstrap.container import ServiceContainer  # noqa: E402
from src.constants.system import SystemConstants  # noqa: E402
from src.logging import get_logger  # noqa: E402

logger = get_logger()


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


if __name__ == "__main__":
    exit_code = 1
    try:
        # Use the already parsed arguments
        args = _args

        # Detect a Wayland environment and configure the Qt platform plugin
        import os

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
                sys.exit(1)

            qt_app = QApplication.instance() or QApplication(sys.argv)
            qt_app.setQuitOnLastWindowClosed(False)

            loop = qasync.QEventLoop(qt_app)
            asyncio.set_event_loop(loop)
            logger.info("Created the PySide6 + qasync event loop")

            # Set up SIGINT handling - request shutdown via the TaskManager
            shutdown_state = {"requested": False}

            def handle_sigint(*_):
                if shutdown_state["requested"]:
                    return
                shutdown_state["requested"] = True
                logger.info("SIGINT received, shutting down...")

                # Request a graceful shutdown via the TaskManager
                try:
                    if _container and _container.tasks:
                        _container.tasks.request_shutdown()
                    else:
                        # The container is not ready yet; quit Qt directly
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
                # Catch qasync's "Event loop stopped before Future completed" error
                if "Event loop stopped before Future completed" in str(e):
                    logger.debug("The event loop terminated normally")
                    exit_code = 0
                else:
                    raise
        else:
            # CLI / TUI / GPIO: standard asyncio
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
                    sys.exit(1)

            # CLI / GPIO mode: standard asyncio; SIGINT asks the TaskManager to shut down
            shutdown_state = {"requested": False}

            def handle_sigint_cli(*_):
                if shutdown_state["requested"]:
                    # A second Ctrl+C: force quit
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
        # A windowed build can hide this; make sure the traceback reaches the crash log
        _write_crash_log(type(e), e, e.__traceback__)
        exit_code = 1
    finally:
        sys.exit(exit_code)
