"""Temporary files created from clipboard content pasted into chat."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import uuid
from pathlib import Path

_attachment_dir: Path | None = None


def _get_attachment_dir() -> Path:
    global _attachment_dir
    if _attachment_dir is None:
        _attachment_dir = Path(tempfile.mkdtemp(prefix="py-xiaozhi-clipboard-"))
        atexit.register(shutil.rmtree, _attachment_dir, ignore_errors=True)
    return _attachment_dir


def save_pasted_text(text: str) -> Path:
    """Save pasted text as a temporary UTF-8 text attachment."""
    path = _get_attachment_dir() / f"pasted-{uuid.uuid4().hex}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def save_pasted_image(image) -> Path:
    """Save a Qt or Pillow clipboard image as a temporary PNG attachment."""
    path = _get_attachment_dir() / f"pasted-{uuid.uuid4().hex}.png"
    result = image.save(str(path), "PNG")
    if result is False or not path.is_file():
        path.unlink(missing_ok=True)
        raise OSError("Could not save clipboard image")
    return path