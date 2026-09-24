# -*- coding: utf-8 -*-
"""System tray service."""

import os
from typing import Callable, Optional

from PySide6.QtCore import QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from src.logging import get_logger
from src.utils.resource_finder import get_assets_dir

logger = get_logger()


class TrayService(QObject):
    """System tray service."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray: Optional[QSystemTrayIcon] = None
        self._menu: Optional[QMenu] = None
        self._enabled = os.getenv("XIAOZHI_DISABLE_TRAY") != "1"

        if not self._enabled:
            logger.warning("System tray disabled via environment variable")

    def setup(
        self,
        on_show: Callable,
        on_quit: Callable,
    ) -> bool:
        """Set up the system tray.

        Args:
            on_show: the show-window callback
            on_quit: the quit callback

        Returns:
            Whether it succeeded
        """
        if not self._enabled:
            return False

        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray unavailable")
            return False

        try:
            # Create the tray icon
            self._tray = QSystemTrayIcon(self.parent())

            # Load the icon
            icon_path = get_assets_dir() / "icon.png"
            if icon_path.exists():
                self._tray.setIcon(QIcon(str(icon_path)))
            else:
                # Use the application icon
                app = QApplication.instance()
                if app:
                    self._tray.setIcon(app.windowIcon())

            # Create the menu
            self._menu = QMenu()
            self._menu.addAction("Show Window", on_show)
            self._menu.addSeparator()
            self._menu.addAction("Quit", on_quit)

            self._tray.setContextMenu(self._menu)

            # Activate on double click
            self._tray.activated.connect(
                lambda reason: on_show() if reason == QSystemTrayIcon.DoubleClick else None
            )

            self._tray.show()
            logger.info("System tray initialized")
            return True

        except Exception as e:
            logger.error(f"System tray init failed: {e}", exc_info=True)
            return False

    def update_tooltip(self, text: str):
        """Update the tray tooltip."""
        if self._tray:
            self._tray.setToolTip(text)

    def show_message(self, title: str, message: str):
        """Show a tray notification."""
        if self._tray:
            self._tray.showMessage(title, message)

    def hide(self):
        """Hide the tray."""
        if self._tray:
            self._tray.hide()

    def is_available(self) -> bool:
        """Whether the tray is available."""
        return self._tray is not None and self._tray.isVisible()
