"""Blender MCP tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import (
    PRIMITIVE_TYPES,
    a_blender_create,
    a_blender_delete,
    a_blender_execute,
    a_blender_modify,
    a_blender_object_info,
    a_blender_render,
    a_blender_scene_info,
    a_blender_status,
)

logger = get_logger()

_AXIS_DOC = "0.0 by default"


def register_blender_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the Blender 3D control tools with McpServer."""

    tools: list[McpTool] = [
        McpTool(
            "blender_execute",
            (
                "Run arbitrary Python/bpy code inside Blender. This is the most "
                "powerful tool - it can create objects, modify the scene, set "
                "materials, animate and anything else bpy supports.\n"
                "Always start the code with 'import bpy'.\n"
                "Example: 'import bpy\\nbpy.ops.mesh.primitive_cube_add(size=2)'"
            ),
            PropertyList([Property("code", PropertyType.STRING)]),
            a_blender_execute,
        ),
        McpTool(
            "blender_scene_info",
            (
                "Get information about the current Blender scene: the object list, "
                "positions, types and materials. Use it to see what is in the scene "
                "before creating or modifying objects."
            ),
            PropertyList([]),
            a_blender_scene_info,
        ),
        McpTool(
            "blender_object_info",
            (
                "Get detailed information about one Blender object: position, "
                "rotation, scale, material and more."
            ),
            PropertyList([Property("name", PropertyType.STRING)]),
            a_blender_object_info,
        ),
        McpTool(
            "blender_create",
            (
                "Create a new 3D object in Blender.\n"
                f"Types: {', '.join(PRIMITIVE_TYPES)}.\n"
                "Optionally set the name, location (x,y,z) and size."
            ),
            PropertyList(
                [
                    Property("type", PropertyType.STRING),
                    Property("name", PropertyType.STRING, default_value=""),
                    Property("location_x", PropertyType.STRING, default_value=""),
                    Property("location_y", PropertyType.STRING, default_value=""),
                    Property("location_z", PropertyType.STRING, default_value=""),
                    Property("size", PropertyType.STRING, default_value=""),
                ]
            ),
            a_blender_create,
        ),
        McpTool(
            "blender_modify",
            (
                "Modify an existing Blender object: position, rotation (degrees), "
                "scale, material colour and visibility. Only the fields you pass "
                "are changed."
            ),
            PropertyList(
                [
                    Property("name", PropertyType.STRING),
                    Property("location_x", PropertyType.STRING, default_value=""),
                    Property("location_y", PropertyType.STRING, default_value=""),
                    Property("location_z", PropertyType.STRING, default_value=""),
                    Property("rotation_x", PropertyType.STRING, default_value=""),
                    Property("rotation_y", PropertyType.STRING, default_value=""),
                    Property("rotation_z", PropertyType.STRING, default_value=""),
                    Property("scale_x", PropertyType.STRING, default_value=""),
                    Property("scale_y", PropertyType.STRING, default_value=""),
                    Property("scale_z", PropertyType.STRING, default_value=""),
                    Property("color_r", PropertyType.STRING, default_value=""),
                    Property("color_g", PropertyType.STRING, default_value=""),
                    Property("color_b", PropertyType.STRING, default_value=""),
                    Property("visible", PropertyType.BOOLEAN, default_value=False),
                ]
            ),
            a_blender_modify,
        ),
        McpTool(
            "blender_delete",
            (
                "Delete an object from the Blender scene. "
                "Use the name 'ALL' to delete every object."
            ),
            PropertyList([Property("name", PropertyType.STRING)]),
            a_blender_delete,
        ),
        McpTool(
            "blender_render",
            (
                "Render the Blender scene to a PNG image, with a configurable "
                "resolution and output path (default Desktop/blender_render.png)."
            ),
            PropertyList(
                [
                    Property("output_path", PropertyType.STRING, default_value=""),
                    Property("resolution_x", PropertyType.INTEGER, default_value=1920),
                    Property("resolution_y", PropertyType.INTEGER, default_value=1080),
                ]
            ),
            a_blender_render,
        ),
        McpTool(
            "blender_status",
            (
                "Check the connection to Blender: whether Blender is open and the "
                "blender-mcp addon server is running."
            ),
            PropertyList([]),
            a_blender_status,
        ),
    ]

    for tool in tools:
        add_tool(tool)
