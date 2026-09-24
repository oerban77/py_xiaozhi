"""macOS shortcut backend.

Registers global hotkeys with the Carbon API's RegisterEventHotKey.
This is the same API that Electron's globalShortcut uses under the hood.
"""

import asyncio
import sys
from typing import Callable, Dict, Optional

from src.logging import get_logger

from .base import ShortcutBackend, ShortcutConfig

logger = get_logger()

# Check whether it is on macOS
if sys.platform != "darwin":
    raise ImportError("macOS backend only works on macOS")

try:
    import Quartz
except ImportError as e:
    raise ImportError(
        "PyObjC is required for macOS hotkey support. "
        "Install with: pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa"
    ) from e


# Carbon virtual key code mapping
# https://developer.apple.com/library/archive/technotes/tn2450/_index.html
KEYCODE_MAP = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04,
    "g": 0x05, "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09,
    "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F,
    "y": 0x10, "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14,
    "4": 0x15, "6": 0x16, "5": 0x17, "=": 0x18, "9": 0x19,
    "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D, "]": 0x1E,
    "o": 0x1F, "u": 0x20, "[": 0x21, "i": 0x22, "p": 0x23,
    "l": 0x25, "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29,
    "\\": 0x2A, ",": 0x2B, "/": 0x2C, "n": 0x2D, "m": 0x2E,
    ".": 0x2F, "`": 0x32, " ": 0x31, "space": 0x31,
    "return": 0x24, "enter": 0x24, "tab": 0x30, "escape": 0x35, "esc": 0x35,
    "delete": 0x33, "backspace": 0x33,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76,
    "f5": 0x60, "f6": 0x61, "f7": 0x62, "f8": 0x64,
    "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "up": 0x7E, "down": 0x7D, "left": 0x7B, "right": 0x7C,
    "home": 0x73, "end": 0x77, "pageup": 0x74, "pagedown": 0x79,
}

# Modifier key mask
MODIFIER_MAP = {
    "cmd": Quartz.kCGEventFlagMaskCommand,
    "command": Quartz.kCGEventFlagMaskCommand,
    "ctrl": Quartz.kCGEventFlagMaskControl,
    "control": Quartz.kCGEventFlagMaskControl,
    "alt": Quartz.kCGEventFlagMaskAlternate,
    "option": Quartz.kCGEventFlagMaskAlternate,
    "shift": Quartz.kCGEventFlagMaskShift,
}


class MacOSShortcutBackend(ShortcutBackend):
    """macOS shortcut backend.

    Listens for global hotkey events with a Quartz Event Tap.
    """

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        super().__init__(loop)
        self._tap = None
        self._run_loop_source = None
        self._hotkey_ids: Dict[str, int] = {}  # name -> hotkey_id
        self._registered_hotkeys: Dict[int, str] = {}  # hotkey_id -> name
        self._next_hotkey_id = 1
        self._monitor_thread = None
        self._pressed_modifiers = 0
        self._check_interval = 5.0  # Health check interval (seconds)
        self._health_check_task = None

    async def start(self) -> bool:
        """Start the shortcut key listener."""
        if self._running:
            return True

        try:
            # Create an event listener
            self._create_event_tap()
            self._running = True

            # Start the health check
            self._start_health_check()

            logger.info("macOS global shortcut listening started (Quartz Event Tap)")
            return True
        except Exception as e:
            logger.error(f"Failed to start macOS shortcut listening: {e}", exc_info=True)
            return False

    def _create_event_tap(self):
        """Create a Quartz Event Tap."""
        # Listen for key press events
        event_mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) |
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        )

        # ListenOnly: does not intercept the event stream, so Ctrl+C / system shortcuts still reach the terminal
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            event_mask,
            self._event_callback,
            None,
        )

        if self._tap is None:
            raise RuntimeError(
                "Failed to create an Event Tap. Make sure Accessibility permission has been granted:\n"
                "System Preferences -> Security & Privacy -> Privacy -> Accessibility"
            )

        # Add the tap to the current run loop
        self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(
            None, self._tap, 0
        )
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(),
            self._run_loop_source,
            Quartz.kCFRunLoopCommonModes,
        )
        Quartz.CGEventTapEnable(self._tap, True)

    def _event_callback(self, proxy, event_type, event, refcon):
        """Event callback function."""
        try:
            if event_type == Quartz.kCGEventKeyDown:
                # Get key code and modifier keys
                keycode = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode
                )
                flags = Quartz.CGEventGetFlags(event)

                # Check whether it matches a registered hotkey
                self._check_hotkey(keycode, flags)

            elif event_type == Quartz.kCGEventFlagsChanged:
                # Update modifier key state
                self._pressed_modifiers = Quartz.CGEventGetFlags(event)

        except Exception as e:
            logger.error(f"Event callback error: {e}", exc_info=True)

        return event

    def _check_hotkey(self, keycode: int, flags: int) -> None:
        """Check whether it matches a registered hotkey."""
        for name, config in self._shortcuts.items():
            # Get the expected key code
            expected_keycode = KEYCODE_MAP.get(config.key.lower())
            if expected_keycode is None:
                continue

            if keycode != expected_keycode:
                continue

            # Check the modifier keys
            expected_modifier = MODIFIER_MAP.get(config.modifier.lower(), 0)
            if expected_modifier == 0:
                continue

            # Check whether the modifier keys match (only checks whether the expected modifier key is pressed)
            if flags & expected_modifier:
                logger.debug(f"Shortcut triggered: {name}")
                self._run_callback(name)

    async def stop(self) -> None:
        """Stop the shortcut key listener."""
        self._running = False

        # Stop the health check
        if self._health_check_task:
            self._health_check_task.cancel()
            # A concurrent.futures.Future cannot be awaited directly; handle it specially
            try:
                # Wait for the Future to complete (ignoring cancellation exceptions)
                self._health_check_task.result(timeout=1.0)
            except Exception as e:
                logger.debug(f"macOS health check timed out: {e}")
            self._health_check_task = None

        # Remove event tap
        if self._tap:
            Quartz.CGEventTapEnable(self._tap, False)
            if self._run_loop_source:
                Quartz.CFRunLoopRemoveSource(
                    Quartz.CFRunLoopGetCurrent(),
                    self._run_loop_source,
                    Quartz.kCFRunLoopCommonModes,
                )
            self._tap = None
            self._run_loop_source = None

        logger.info("macOS global shortcut listening stopped")

    def register(self, name: str, config: ShortcutConfig, callback: Callable) -> bool:
        """Register shortcut."""
        # Validate key code
        if config.key.lower() not in KEYCODE_MAP:
            logger.warning(f"Unsupported key: {config.key}")
            return False

        # Validate modifier keys
        if config.modifier.lower() not in MODIFIER_MAP:
            logger.warning(f"Unsupported modifier: {config.modifier}")
            return False

        self._shortcuts[name] = config
        self._callbacks[name] = callback

        hotkey_id = self._next_hotkey_id
        self._next_hotkey_id += 1
        self._hotkey_ids[name] = hotkey_id
        self._registered_hotkeys[hotkey_id] = name

        logger.info(f"Shortcut registered: {name} -> {config.modifier}+{config.key}")
        return True

    def unregister(self, name: str) -> bool:
        """Unregister a shortcut key."""
        if name not in self._shortcuts:
            return False

        del self._shortcuts[name]
        del self._callbacks[name]

        if name in self._hotkey_ids:
            hotkey_id = self._hotkey_ids[name]
            del self._hotkey_ids[name]
            if hotkey_id in self._registered_hotkeys:
                del self._registered_hotkeys[hotkey_id]

        logger.info(f"Shortcut unregistered: {name}")
        return True

    def _start_health_check(self):
        """Start the health check task."""
        if self._loop:
            self._health_check_task = asyncio.run_coroutine_threadsafe(
                self._health_check_loop(), self._loop
            )

    async def _health_check_loop(self):
        """Health check: try to re-enable when disabled; failures only log a warning to avoid a rebuild storm blocking shutdown."""
        consecutive_fail = 0
        max_warn_streak = 3  # After this many consecutive failures, the log frequency is lowered

        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            if not self._tap:
                continue

            try:
                if Quartz.CGEventTapIsEnabled(self._tap):
                    consecutive_fail = 0
                    continue
            except Exception:
                # tap is invalid
                consecutive_fail += 1
            else:
                # Only re-enable; do not stop/start the whole tree (rebuilding would fight with CFRunLoop/signal handling)
                try:
                    Quartz.CGEventTapEnable(self._tap, True)
                    if Quartz.CGEventTapIsEnabled(self._tap):
                        if consecutive_fail > 0:
                            logger.info("Event Tap re-enabled successfully")
                        consecutive_fail = 0
                        continue
                except Exception as e:
                    logger.debug(f"CGEventTapEnable error: {e}")

                consecutive_fail += 1

            if consecutive_fail <= max_warn_streak:
                logger.warning(
                    "Event Tap still unavailable (%s checks). "
                    "Please check: System Settings -> Privacy & Security -> Accessibility. "
                    "Shortcuts may be temporarily unavailable; Ctrl+C should still work.",
                    consecutive_fail,
                )
            elif consecutive_fail % 12 == 0:
                # About once per minute to avoid screen spam
                logger.warning(
                    "Event Tap persistently unavailable (%s checks); skipping forced rebuild",
                    consecutive_fail,
                )
