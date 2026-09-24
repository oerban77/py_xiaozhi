"""Activation verification code side effects: clipboard and speech announcement."""

from __future__ import annotations

from typing import Optional

from src.logging import get_logger

logger = get_logger()


def apply_code_side_effects(code: str, message: Optional[str] = None) -> None:
    """Logging + clipboard + announcement; not responsible for CLI/GUI copy."""
    if not code:
        return
    msg = message or "Please enter the verification code in the control panel"
    logger.info(f"Activation prompt: {msg}")
    logger.info(f"Activation code: {code}")

    text = f".Please log in to the control panel to add the device and enter the verification code: {' '.join(code)}..."
    try:
        from src.utils.common_utils import handle_verification_code

        handle_verification_code(text)
    except Exception as e:
        logger.debug(f"Failed to copy activation code: {e}")

    try:
        from src.utils.activation_announcer import announce_activation_code

        announce_activation_code(code, locale="zh-CN")
    except Exception as e:
        logger.debug(f"Failed to announce activation code: {e}")


def announce_code(code: str) -> None:
    """Announcement only (used on poll retries)."""
    if not code:
        return
    from src.utils.activation_announcer import announce_activation_code

    announce_activation_code(code, locale="zh-CN")
