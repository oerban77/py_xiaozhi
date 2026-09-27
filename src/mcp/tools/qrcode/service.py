"""QR/barcode reader MCP tools.

Ported from the reference Xiaozhi desktop app (mcp/mcp_qrcode.py):
- ``qrcode_read_file`` — read and decode a QR code / barcode from an image file

Decoding uses OpenCV's ``QRCodeDetector``. If ``pyzbar`` is installed it is
used as well to read 1D barcodes (it is an optional dependency — when it is
missing, only QR codes are decoded).
"""

from __future__ import annotations

import os
from typing import Any

from src.logging import get_logger

logger = get_logger()

_SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".jpe",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
}


def _resolve_image_path(path_value: str) -> str:
    """Resolve a user-supplied path (absolute or relative to the cwd)."""
    raw = str(path_value or "").strip()
    if not raw:
        raise ValueError("An image path is required")

    if os.path.isabs(raw) and os.path.exists(raw):
        return raw

    candidates = [raw, os.path.abspath(raw)]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    return os.path.abspath(raw)


def _read_codes_from_image(image_path: str) -> list[dict[str, Any]]:
    """Decode QR/barcodes from an image file."""
    import cv2

    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Could not read the image: {image_path}")

    results: list[dict[str, Any]] = []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.QRCodeDetector()
    try:
        data, _points, _qrcode = detector.detectAndDecode(gray)
    except Exception as e:  # cv2 can raise on malformed images
        logger.debug("QRCodeDetector failed: %s", e)
        data = ""

    if data:
        results.append({"type": "qr", "data": data})

    # pyzbar adds 1D barcode support; it is optional.
    try:
        from pyzbar.pyzbar import decode

        pil_image = None
        try:
            from PIL import Image

            pil_image = Image.open(image_path)
        except Exception as e:
            logger.debug("PIL unavailable: %s", e)

        if pil_image is not None:
            for barcode in decode(pil_image):
                if barcode.data:
                    text = barcode.data.decode("utf-8", errors="ignore")
                    results.append({"type": "barcode", "data": text})
    except ImportError:
        logger.debug("pyzbar not installed; only QR codes can be decoded")
    except Exception as e:
        logger.debug("pyzbar decode failed: %s", e)

    return results


async def qrcode_read_file(args: dict[str, Any]) -> str:
    """Read and decode a QR code or barcode from a local image file."""
    path_value = (
        args.get("path") or args.get("file") or args.get("image_path") or ""
    )
    prompt = (
        args.get("prompt")
        or args.get("question")
        or "Read and describe the contents of this QR/barcode."
    )

    try:
        resolved = _resolve_image_path(str(path_value))
    except ValueError as e:
        return str(e)

    if not os.path.exists(resolved):
        return f"File not found: {path_value}"
    if not os.path.isfile(resolved):
        return f"Not a file: {path_value}"

    ext = os.path.splitext(resolved)[1].lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        return f"Unsupported image format: {ext or '(no extension)'}"

    try:
        results = await _read_codes_from_image_async(resolved)
    except Exception as e:
        logger.error("qrcode_read_file error: %s", e)
        return f"Error: {e}"

    if not results:
        return (
            f"No QR code or barcode could be read from {resolved}.\n"
            "If pyzbar is not installed, only QR codes are supported."
        )

    lines = [f"QR/barcode read from {resolved}:", ""]
    for item in results:
        lines.append(f"- {str(item.get('type', '?')).upper()}: {item.get('data', '')}")
    lines.append("")
    lines.append(f"Prompt: {prompt}")
    return "\n".join(lines)


async def _read_codes_from_image_async(image_path: str) -> list[dict[str, Any]]:
    """Run the blocking OpenCV decode in a worker thread."""
    import asyncio

    return await asyncio.to_thread(_read_codes_from_image, image_path)
