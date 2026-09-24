# -*- coding: utf-8 -*-
"""Emotion service - manages emotion resources."""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl

from src.logging import get_logger
from src.utils.resource_finder import get_assets_dir

logger = get_logger()


class EmotionService(QObject):
    """Emotion service - handles finding emotion files and converting them to URLs."""

    EXTENSIONS = (".gif", ".png", ".jpg", ".jpeg", ".webp")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: dict[str, str] = {}
        self._emotion_dir = get_assets_dir() / "emojis"

        if not self._emotion_dir.exists():
            logger.warning(f"Emotion directory does not exist: {self._emotion_dir}")

    def get_emotion_url(self, emotion_name: str) -> str:
        """Get the QML-usable URL for an emotion.

        Args:
            emotion_name: the emotion name

        Returns:
            A file:// URL or an emoji character
        """
        # Check the cache
        if emotion_name in self._cache:
            return self._cache[emotion_name]

        # Look up the file
        path = self._find_emotion_file(emotion_name)
        if not path:
            # Fall back to neutral
            path = self._find_emotion_file("neutral")

        # Convert to a URL
        if path:
            url = QUrl.fromLocalFile(str(path)).toString()
        else:
            url = "😊"  # Final fallback
            logger.warning(f"Emotion {emotion_name} not found; using emoji")

        self._cache[emotion_name] = url
        return url

    def _find_emotion_file(self, name: str) -> Optional[Path]:
        """Find the emotion file."""
        for ext in self.EXTENSIONS:
            file_path = self._emotion_dir / f"{name}{ext}"
            if file_path.exists():
                return file_path
        return None

    def clear_cache(self):
        """Clear the cache."""
        self._cache.clear()

    def preload(self, names: list[str]):
        """Preload emotions."""
        for name in names:
            self.get_emotion_url(name)
        logger.debug(f"Preloaded {len(names)} emotions")
