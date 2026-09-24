"""
Base camera implementation.
"""

from abc import ABC, abstractmethod
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

from .capture_backend import capture_jpeg, load_capture_config

logger = get_logger()


class BaseCamera(ABC):
    """
    Base camera class defining the interface.
    """

    def __init__(self):
        """
        Initialize the base camera.
        """
        self.jpeg_data = {"buf": b"", "len": 0}  # JPEG bytes of the image  # byte length

        # Read camera parameters from the configuration (capture details are read centrally by capture_backend)
        config = get_config()
        self.camera_index = config.get_config("CAMERA.camera_index", 0)
        self.frame_width = config.get_config("CAMERA.frame_width", 640)
        self.frame_height = config.get_config("CAMERA.frame_height", 480)

    def capture_frame(self) -> bool:
        """Capture one JPEG frame using a pluggable backend (OpenCV/V4L2 or picamera2).

        Desktop USB / Pi USB use OpenCV; Pi CSI falls back to picamera2 after OpenCV fails in auto mode.
        Protect with a timeout to avoid a dead driver hanging the thread.
        """
        cfg = load_capture_config()
        # Align with the instance fields (if index was changed externally, the configuration wins; it is authoritative)
        self.camera_index = cfg.camera_index
        self.frame_width = cfg.frame_width
        self.frame_height = cfg.frame_height

        jpeg = capture_jpeg(cfg)
        if not jpeg:
            logger.error(
                "Camera capture failed "
                f"(backend={cfg.backend}, device={cfg.device!r}, index={cfg.camera_index})"
            )
            return False

        self.set_jpeg_data(jpeg)
        logger.info(
            f"Image captured successfully (size: {self.jpeg_data['len']} bytes)"
        )
        return True

    # Compatible old name
    def capture_with_cv2(self) -> bool:
        return self.capture_frame()

    def set_explain_url(self, url: str):  # noqa: B027
        """Set the visual service URL (override in subclasses as needed)."""

    def set_explain_token(self, token: str):  # noqa: B027
        """Set the visual service token (override in subclasses as needed)."""

    @abstractmethod
    def capture(self) -> bool:
        """
        Capture image.
        """

    @abstractmethod
    def analyze(self, question: str, image_data: bytes | None = None) -> str:
        """Analyze image.

        Args:
            question: user question
            image_data: optional external image data; when None, self.jpeg_data is used
        """

    def get_jpeg_data(self) -> dict[str, Any]:
        """
        Get JPEG data.
        """
        return self.jpeg_data

    def set_jpeg_data(self, data_bytes: bytes):
        """
        Settings JPEG data.
        """
        self.jpeg_data["buf"] = data_bytes
        self.jpeg_data["len"] = len(data_bytes)
