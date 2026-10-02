"""Shortcut configuration properties."""


class SettingsShortcutsMixin:
    # ========== Shortcut settings ==========

    def _get_shortcutsEnabled(self) -> bool:
        return self._get_value("SHORTCUTS.ENABLED", True)

    def _set_shortcutsEnabled(self, value: bool):
        self._set_value("SHORTCUTS.ENABLED", value)

    # Shortcut: manual mode
    def _get_shortcutManualModifier(self) -> str:
        return self._get_value("SHORTCUTS.MANUAL_PRESS.modifier", "ctrl")

    def _set_shortcutManualModifier(self, value: str):
        self._set_value("SHORTCUTS.MANUAL_PRESS.modifier", value)

    def _get_shortcutManualKey(self) -> str:
        return self._get_value("SHORTCUTS.MANUAL_PRESS.key", "j")

    def _set_shortcutManualKey(self, value: str):
        self._set_value("SHORTCUTS.MANUAL_PRESS.key", value)

    # Shortcut: automatic mode
    def _get_shortcutAutoModifier(self) -> str:
        return self._get_value("SHORTCUTS.AUTO_TOGGLE.modifier", "ctrl")

    def _set_shortcutAutoModifier(self, value: str):
        self._set_value("SHORTCUTS.AUTO_TOGGLE.modifier", value)

    def _get_shortcutAutoKey(self) -> str:
        return self._get_value("SHORTCUTS.AUTO_TOGGLE.key", "k")

    def _set_shortcutAutoKey(self, value: str):
        self._set_value("SHORTCUTS.AUTO_TOGGLE.key", value)

    # Shortcut: interrupt
    def _get_shortcutAbortModifier(self) -> str:
        return self._get_value("SHORTCUTS.ABORT.modifier", "ctrl")

    def _set_shortcutAbortModifier(self, value: str):
        self._set_value("SHORTCUTS.ABORT.modifier", value)

    def _get_shortcutAbortKey(self) -> str:
        return self._get_value("SHORTCUTS.ABORT.key", "q")

    def _set_shortcutAbortKey(self, value: str):
        self._set_value("SHORTCUTS.ABORT.key", value)

    # Shortcut: mode toggle
    def _get_shortcutModeModifier(self) -> str:
        return self._get_value("SHORTCUTS.MODE_TOGGLE.modifier", "ctrl")

    def _set_shortcutModeModifier(self, value: str):
        self._set_value("SHORTCUTS.MODE_TOGGLE.modifier", value)

    def _get_shortcutModeKey(self) -> str:
        return self._get_value("SHORTCUTS.MODE_TOGGLE.key", "m")

    def _set_shortcutModeKey(self, value: str):
        self._set_value("SHORTCUTS.MODE_TOGGLE.key", value)

    # Shortcut: show/hide the window
    def _get_shortcutWindowModifier(self) -> str:
        return self._get_value("SHORTCUTS.WINDOW_TOGGLE.modifier", "ctrl")

    def _set_shortcutWindowModifier(self, value: str):
        self._set_value("SHORTCUTS.WINDOW_TOGGLE.modifier", value)

    def _get_shortcutWindowKey(self) -> str:
        return self._get_value("SHORTCUTS.WINDOW_TOGGLE.key", "w")

    def _set_shortcutWindowKey(self, value: str):
        self._set_value("SHORTCUTS.WINDOW_TOGGLE.key", value)

    # TUI startup (Windows only)
    def _get_tuiStartWithWindows(self) -> bool:
        return bool(self._get_value("SHORTCUTS.TUI_START_WITH_WINDOWS", False))

    def _set_tuiStartWithWindows(self, value: bool):
        self._set_value("SHORTCUTS.TUI_START_WITH_WINDOWS", bool(value))

    def _get_tuiStartWithWindowsSupported(self) -> bool:
        from src.utils.windows_startup import is_windows_startup_supported

        return is_windows_startup_supported()

