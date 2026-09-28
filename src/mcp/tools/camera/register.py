"""Camera MCP tool registration and factory."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType
from src.utils.config_manager import get_config

from .normal_camera import NormalCamera
from .vl_camera import VLCamera

logger = get_logger()


def create_camera():
    """
    Create a camera implementation by config and return.
    """
    config = get_config()

    vl_key = config.get_config("CAMERA.VLapi_key")
    vl_url = config.get_config("CAMERA.Local_VL_url")

    if vl_key and vl_url:
        logger.info(f"Initializing VL Camera with URL: {vl_url}")
        return VLCamera()

    logger.info("VL configuration not found, using normal Camera implementation")
    camera = NormalCamera()
    # Fall back to the configured remote vision service (the server-provided
    # capability still takes precedence, applied later via set_explain_url).
    explain_url = config.get_config("CAMERA.explain_url", "") or ""
    explain_token = config.get_config("CAMERA.explain_token", "") or ""
    if explain_url:
        camera.set_explain_url(explain_url)
        if explain_token:
            camera.set_explain_token(explain_token)
        logger.info(f"Camera vision service configured from settings: {explain_url}")
    return camera


def register_camera_tools(
    add_tool: Callable[[McpTool], None], camera, pending_image_provider=None
) -> None:
    async def take_photo(arguments: dict) -> str:
        logger.info(f"Using camera implementation: {camera.__class__.__name__}")
        question = arguments.get("question", "")

        pending_image = (
            pending_image_provider() if pending_image_provider is not None else None
        )
        if pending_image is not None:
            image_path, attachment_question = pending_image
            image_data = await asyncio.to_thread(Path(image_path).read_bytes)
            user_question = attachment_question or question
            vision_question = (
                f"{user_question}\n\n"
                "Jawab dalam bahasa Indonesia. Jika isi gambar berbahasa Mandarin, "
                "terjemahkan atau ringkas dalam bahasa Indonesia."
            )
            logger.info(
                "Analyzing selected attachment through camera MCP: file=%s, bytes=%d",
                Path(image_path).name,
                len(image_data),
            )
            result = await asyncio.to_thread(
                camera.analyze, vision_question, image_data
            )
            try:
                payload = json.loads(result) if isinstance(result, str) else result
            except (TypeError, ValueError):
                return str(result).strip()
            if isinstance(payload, dict):
                if not payload.get("success", True):
                    raise RuntimeError(
                        str(payload.get("message") or "Vision service rejected the image")
                    )
                for key in ("text", "result", "response", "content"):
                    if payload.get(key):
                        return (
                            "Hasil analisis gambar: "
                            f"{str(payload[key]).strip()}\n\n"
                            "Sampaikan jawaban akhir kepada pengguna dalam bahasa Indonesia. "
                            "Terjemahkan isi Mandarin jika diperlukan."
                        )
            return str(result).strip()

        logger.info(f"Taking photo with question: {question}")

        success = await asyncio.to_thread(camera.capture)
        if not success:
            logger.error("Failed to capture photo")
            return json.dumps({"success": False, "message": "Failed to capture photo"})

        logger.info("Photo captured, starting analysis...")
        return await asyncio.to_thread(camera.analyze, question)

    add_tool(
        McpTool(
            "take_photo",
            (
                "[Photo Recognition] Call this tool when the user mentions: take a photo, "
                "snap a picture, take a picture, take a look, look, help me look, what is this, "
                "recognize, image recognition, look at the image, picture, photo, help me see.\n"
                "Function: take a photo and analyze its content, answering the user's questions about the image.\n"
                "If the desktop app has queued an attached image, analyze that selected file instead of capturing the camera. "
                "For requests like 'analisa gambar', 'gambar yang saya lampirkan', or questions about an uploaded image, "
                "you MUST call this tool. The app supplies the selected image and the user's exact question.\n"
                "Use cases:\n"
                "1. The user asks to take a photo to look at something (e.g., 'help me see what this is', "
                "'take a photo', 'look at what is in front')\n"
                "2. Object/scene recognition ('what is this thing', 'help me identify this', 'recognize this')\n"
                "3. Text recognition OCR ('read the text above', 'extract text', 'what is written here')\n"
                "4. Image Q&A ('how many people are in the picture', 'what color is this', "
                "'what content is shown above')\n\n"
                "Parameter description:\n"
                "- question: string type, the question the user wants to ask about the image\n\n"
                "Usage tip: when the user says 'look', 'take a look', 'what is this' or other vague "
                "expressions, prefer this tool to take a photo and recognize it.\n"
                "English: Take a photo and explain it. Use this tool after the user asks you to see something.\n"
                "Args: `question` - The question that you want to ask about the photo.\n"
                "Return: A JSON object that provides the photo information.\n"
                "Examples: 'help me see what this is', 'take a photo', 'look in front', "
                "'take a photo', 'what is this'."
            ),
            PropertyList([Property("question", PropertyType.STRING)]),
            take_photo,
        )
    )
    logger.info("Registered take_photo (camera injected by container)")
