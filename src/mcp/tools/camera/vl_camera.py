"""
VL camera implementation using Zhipu AI.
"""

import base64
import json

import httpx
from openai import OpenAI

from src.logging import get_logger
from src.utils.config_manager import get_config

from .base_camera import BaseCamera

logger = get_logger()


class VLCamera(BaseCamera):
    """
    Zhipu AI camera implementation.
    """

    def __init__(self):
        """
        Initialize the Zhipu AI camera.
        """
        super().__init__()
        config = get_config()

        # Initialize the OpenAI client (set a timeout so the thread pool does not hang when the API does not respond)
        self.client = OpenAI(
            api_key=config.get_config("CAMERA.VLapi_key"),
            base_url=config.get_config(
                "CAMERA.Local_VL_url",
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            ),
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self.model = config.get_config("CAMERA.models", "glm-4v-plus")
        logger.info(f"VL Camera initialized with model: {self.model}")

    def capture(self) -> bool:
        """
        Capture an image (OpenCV/V4L2 or picamera2, see capture_backend).
        """
        return self.capture_frame()

    def analyze(self, question: str, image_data: bytes | None = None) -> str:
        try:
            buf = image_data if image_data is not None else self.jpeg_data["buf"]
            if not buf:
                return json.dumps(
                    {"success": False, "message": "Camera buffer is empty"}
                )

            # Convert the image to Base64
            image_base64 = base64.b64encode(buf).decode("utf-8")

            # Prepare the messages
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                question
                                if question
                                else "Describe the scene depicted in the image in detail."
                            ),
                        },
                    ],
                },
            ]

            # Send the request
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                modalities=["text"],
                stream=True,
                stream_options={"include_usage": True},
            )

            # Collect the response
            result = ""
            for chunk in completion:
                if chunk.choices:
                    result += chunk.choices[0].delta.content or ""

            # Log the response
            logger.info(f"VL analysis completed, question={question}")
            return json.dumps({"success": True, "text": result}, ensure_ascii=False)

        except Exception as e:
            error_msg = f"Failed to analyze image with VL: {e}"
            logger.error(error_msg, exc_info=True)
            return json.dumps({"success": False, "message": error_msg})
