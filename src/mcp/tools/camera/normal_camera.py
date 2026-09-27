"""
Normal camera implementation using remote API.
"""

import json
from urllib.parse import urlsplit, urlunsplit

import requests

from src.logging import get_logger
from src.utils.config_manager import get_config

from .base_camera import BaseCamera

logger = get_logger()


class NormalCamera(BaseCamera):
    """
    Normal camera implementation using a remote API for analysis.
    """

    def __init__(self):
        """
        Initialize the normal camera.
        """
        super().__init__()
        self.explain_url = ""
        self.explain_token = ""

    def set_explain_url(self, url: str):
        parts = urlsplit(url)
        if parts.scheme.lower() == "http" and parts.hostname == "api.xiaozhi.me":
            url = urlunsplit(("https", parts.netloc, parts.path, parts.query, parts.fragment))
        self.explain_url = url
        logger.info(f"Vision service URL set to: {url}")

    def set_explain_token(self, token: str):
        self.explain_token = token
        if token:
            logger.info("Vision service token has been set")

    def capture(self) -> bool:
        """
        Capture an image (OpenCV/V4L2 or picamera2, see capture_backend).
        """
        return self.capture_frame()

    def analyze(self, question: str, image_data: bytes | None = None) -> str:
        if not self.explain_url:
            return json.dumps(
                {"success": False, "message": "Image explain URL is not set"}
            )

        buf = image_data if image_data is not None else self.jpeg_data["buf"]
        if not buf:
            return json.dumps({"success": False, "message": "Camera buffer is empty"})

        # Prepare request headers
        headers = {
            "Device-Id": get_config().get_config("SYSTEM_OPTIONS.DEVICE_ID"),
            "Client-Id": get_config().get_config("SYSTEM_OPTIONS.CLIENT_ID"),
        }

        if self.explain_token:
            headers["Authorization"] = f"Bearer {self.explain_token}"

        # Prepare the file data
        files = {
            "question": (None, question),
            "file": ("camera.jpg", buf, "image/jpeg"),
        }

        try:
            logger.info(
                f"[Vision] POST {self.explain_url}, "
                f"question={question}, file_size={len(buf)} bytes"
            )
            response = requests.post(
                self.explain_url, headers=headers, files=files, timeout=10
            )

            # Check the response status
            if response.status_code != 200:
                error_msg = (
                    f"Failed to upload photo, status code: {response.status_code}"
                )
                logger.error(error_msg)
                return json.dumps({"success": False, "message": error_msg})

            # Log the response
            logger.info(
                f"Explain image size={self.jpeg_data['len']}, "
                f"question={question}\n{response.text}"
            )
            return response.text

        except requests.RequestException as e:
            error_msg = f"Failed to connect to explain URL: {e}"
            logger.error(error_msg)
            return json.dumps({"success": False, "message": error_msg})
