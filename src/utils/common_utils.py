"""
General-purpose utility functions module.

Contains general-purpose utility functions such as browser operations, clipboard access, and verification code extraction.
"""

import re
import webbrowser
from typing import Optional

from src.logging import get_logger

logger = get_logger()


def open_url(url: str) -> bool:
    """Open a web page link."""
    try:
        success = webbrowser.open(url)
        if success:
            logger.info(f"Web page opened successfully: {url}")
        else:
            logger.warning(f"Could not open web page: {url}")
        return success
    except Exception as e:
        logger.error(f"Error opening web page: {e}", exc_info=True)
        return False


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the clipboard."""
    try:
        import pyperclip

        pyperclip.copy(text)
        logger.info(f'Text "{text}" copied to clipboard')
        return True
    except ImportError:
        logger.warning("pyperclip module not installed; cannot copy to clipboard")
        return False
    except Exception as e:
        logger.error(f"Error copying to clipboard: {e}", exc_info=True)
        return False


def extract_verification_code(text: str) -> Optional[str]:
    """Extract a verification code from text."""
    try:
        # Activation-related keyword list (matches Chinese activation emails/messages)
        activation_keywords = [
            "登录",
            "控制面板",
            "激活",
            "验证码",
            "绑定设备",
            "添加设备",
            "输入验证码",
            "输入",
            "面板",
            "xiaozhi.me",
            "激活码",
        ]

        # Check whether the text contains an activation-related keyword
        has_activation_keyword = any(keyword in text for keyword in activation_keywords)

        if not has_activation_keyword:
            logger.debug(f"Text does not contain activation keyword; skipping code extraction: {text}")
            return None

        # More precise verification code matching patterns
        patterns = [
            r"验证码[：:]\s*(\d{6})",
            r"输入验证码[：:]\s*(\d{6})",
            r"输入\s*(\d{6})",
            r"验证码\s*(\d{6})",
            r"激活码[：:]\s*(\d{6})",
            r"(\d{6})[，,。.]",
            r"[，,。.]\s*(\d{6})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                code = match.group(1)
                logger.info(f"Activation code extracted from text: {code}")
                return code

        # Generic pattern matching
        match = re.search(r"((?:\d\s*){6,})", text)
        if match:
            code = "".join(match.group(1).split())
            if len(code) == 6 and code.isdigit():
                logger.info(f"Activation code extracted from text (generic pattern): {code}")
                return code

        logger.warning(f"Could not find activation code in text: {text}")
        return None
    except Exception as e:
        logger.error(f"Error extracting activation code: {e}", exc_info=True)
        return None


def handle_verification_code(text: str) -> None:
    """Handle the verification code: extract it and copy it to the clipboard."""
    code = extract_verification_code(text)
    if code:
        copy_to_clipboard(code)
