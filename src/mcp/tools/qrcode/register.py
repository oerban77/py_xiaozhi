"""QR/barcode MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import qrcode_read_file

logger = get_logger()


def register_qrcode_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the QR/barcode tools with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "qrcode_read_file",
            (
                "Read and decode a QR code or barcode from a local image file "
                "(jpg/png/webp/bmp/gif/tiff).\n"
                "Returns the decoded text of every code found in the image.\n"
                "QR codes are decoded with OpenCV; 1D barcodes additionally "
                "require the optional pyzbar package.\n"
                "Parameters:\n"
                "- path: path of the image file (required)\n"
                "- prompt: extra instruction for interpreting the result"
            ),
            PropertyList(
                [
                    Property("path", PropertyType.STRING),
                    Property("prompt", PropertyType.STRING, default_value=""),
                ]
            ),
            qrcode_read_file,
        ),
    ]

    for tool in tools:
        add_tool(tool)
