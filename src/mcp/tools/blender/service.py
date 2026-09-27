"""Blender 3D control MCP tools.

Ported from the reference Xiaozhi desktop app (``mcp/mcp_blender.py``), which
talks to the blender-mcp addon (https://github.com/ahujasid/blender-mcp) running
inside Blender and listening on TCP 9876.

Protocol: send one JSON object terminated by ``\\n``, read the newline (or
complete JSON) reply. Every call opens a fresh socket, so no state is kept
between tool invocations.

Configuration (``config.yaml``)::

    blender:
      enabled: true
      host: "127.0.0.1"
      port: 9876
      timeout: 60
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 9876
_DEFAULT_TIMEOUT = 60.0
_MAX_CODE_BYTES = 64 * 1024
_MAX_OBJECTS_DISPLAYED = 40
_CHUNK = 65536

# Primitive types accepted by blender_create, in the order the reference used.
PRIMITIVE_TYPES = (
    "cube",
    "sphere",
    "cylinder",
    "cone",
    "plane",
    "torus",
    "monkey",
    "empty",
    "camera",
    "light",
    "sun",
    "text",
)


def _blender_config() -> dict[str, Any]:
    """Read the BLENDER config section (empty dict when unset)."""
    try:
        cfg = get_config()
    except Exception:  # pragma: no cover - config not initialised
        return {}
    return cfg.get_config("BLENDER", {}) or {}


def _client_settings() -> tuple[str, int, float, bool]:
    """Return (host, port, timeout, enabled) from config with defaults."""
    section = _blender_config()
    try:
        host = str(section.get("host") or _DEFAULT_HOST).strip() or _DEFAULT_HOST
        port = int(section.get("port") or _DEFAULT_PORT)
        timeout = float(section.get("timeout") or _DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        host, port, timeout = _DEFAULT_HOST, _DEFAULT_PORT, _DEFAULT_TIMEOUT
    enabled = bool(section.get("enabled", True))
    return host, port, timeout, enabled


# ─── Blender socket client ─────────────────────────────────────────────────


def _delete_code(name: str) -> str:
    return (
        "import bpy\n"
        f"obj = bpy.data.objects.get({name!r})\n"
        "if obj is None:\n"
        f"    raise ValueError('Object ' + {name!r})\n"
        "bpy.data.objects.remove(obj, do_unlink=True)\n"
        f"print('Deleted: ' + {name!r})\n"
    )


def _clear_code() -> str:
    return (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete(use_global=False)\n"
        "print('Scene cleared')\n"
    )


def _render_code(output_path: str, resolution_x: int, resolution_y: int) -> str:
    return (
        "import bpy\n"
        "scene = bpy.context.scene\n"
        f"scene.render.resolution_x = {int(resolution_x)}\n"
        f"scene.render.resolution_y = {int(resolution_y)}\n"
        f"scene.render.filepath = {output_path!r}\n"
        "scene.render.image_settings.file_format = 'PNG'\n"
        "bpy.ops.render.render(write_still=True)\n"
        f"print('Rendered to: ' + {output_path!r})\n"
    )


class BlenderClient:
    """Minimal TCP client for the blender-mcp addon."""

    def __init__(
        self, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT, timeout: float = _DEFAULT_TIMEOUT
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def _send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send one JSON command and read back one JSON reply."""
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))

            payload = json.dumps(command, ensure_ascii=False) + "\n"
            sock.sendall(payload.encode("utf-8"))

            buffer = b""
            while True:
                try:
                    chunk = sock.recv(_CHUNK)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk
                if b"\n" in buffer:
                    break
                try:
                    json.loads(buffer.decode("utf-8"))
                    break
                except json.JSONDecodeError:
                    continue

            if not buffer:
                return {"status": "error", "message": "No response from Blender"}

            # A reply may carry several lines; the JSON document is the last one.
            for line in reversed(buffer.decode("utf-8").strip().split("\n")):
                line = line.strip()
                if line:
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
            return {"status": "error", "message": f"Unparseable reply: {buffer[:200]!r}"}

        except ConnectionRefusedError:
            return {
                "status": "error",
                "message": (
                    f"Cannot connect to Blender at {self.host}:{self.port}. "
                    "Blender must be open, the blender-mcp addon enabled and its "
                    "server started (click Start in the MCP panel)."
                ),
            }
        except socket.timeout:
            return {
                "status": "error",
                "message": f"Timed out after {self.timeout:g}s waiting for Blender",
            }
        except OSError as exc:
            return {"status": "error", "message": f"Blender connection error: {exc}"}
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    # ── high level commands ────────────────────────────────────────────────

    def check_connection(self) -> dict[str, Any]:
        return self._send_command({"type": "get_scene_info"})

    def get_scene_info(self) -> dict[str, Any]:
        return self._send_command({"type": "get_scene_info"})

    def get_object_info(self, name: str) -> dict[str, Any]:
        return self._send_command({"type": "get_object_info", "params": {"name": name}})

    def execute_code(self, code: str) -> dict[str, Any]:
        return self._send_command({"type": "execute_code", "params": {"code": code}})

    def delete_object(self, name: str) -> dict[str, Any]:
        return self.execute_code(_delete_code(name))

    def clear_scene(self) -> dict[str, Any]:
        return self.execute_code(_clear_code())

    def render_scene(
        self, output_path: str = "", resolution_x: int = 1920, resolution_y: int = 1080
    ) -> dict[str, Any]:
        if not output_path:
            output_path = os.path.join(
                os.path.expanduser("~"), "Desktop", "blender_render.png"
            ).replace("\\", "/")
        return self.execute_code(_render_code(output_path, resolution_x, resolution_y))


# ─── Code generation for blender_create / blender_modify ───────────────────


def _py_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _py_tuple3(values: tuple[Any, Any, Any], default: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        _py_float(values[0], default[0]),
        _py_float(values[1], default[1]),
        _py_float(values[2], default[2]),
    )


_CREATE_CODE = {
    "cube": "bpy.ops.mesh.primitive_cube_add(size={size}, location={loc})",
    "sphere": "bpy.ops.mesh.primitive_uv_sphere_add(radius={size}, location={loc})",
    "cylinder": "bpy.ops.mesh.primitive_cylinder_add(radius={size}, depth={size}, location={loc})",
    "cone": "bpy.ops.mesh.primitive_cone_add(radius1={size}, depth={size}, location={loc})",
    "plane": "bpy.ops.mesh.primitive_plane_add(size={size}, location={loc})",
    "torus": (
        "bpy.ops.mesh.primitive_torus_add(major_radius={size}, "
        "minor_radius={size!s}, location={loc})"
    ),
    "monkey": "bpy.ops.mesh.primitive_monkey_add(size={size}, location={loc})",
    "empty": "bpy.ops.object.empty_add(location={loc})",
    "camera": "bpy.ops.object.camera_add(location={loc})",
    "light": "bpy.ops.object.light_add(type='POINT', radius={size}, location={loc})",
    "sun": "bpy.ops.object.light_add(type='SUN', location={loc})",
    "text": "bpy.ops.object.text_add(location={loc})",
}


def _create_code(obj_type: str, name: str, location: tuple[float, float, float], size: float) -> str:
    """Build the bpy snippet that creates one primitive and renames it."""
    template = _CREATE_CODE[obj_type]
    body = template.format(size=size, loc=tuple(location))
    lines = ["import bpy", body]
    if name:
        lines.append(f"bpy.context.active_object.name = {name!r}")
    if obj_type == "text" and name:
        lines.append(f"bpy.context.active_object.data.body = {name!r}")
    lines.append("print('Created: ' + bpy.context.active_object.name)")
    return "\n".join(lines) + "\n"


def _modify_code(
    name: str,
    location: tuple[float, float, float] | None,
    rotation: tuple[float, float, float] | None,
    scale: tuple[float, float, float] | None,
    color: tuple[float, float, float] | None,
    visible: bool | None,
) -> str:
    """Build the bpy snippet that modifies an existing object."""
    lines = [
        "import bpy",
        "import math",
        f"obj = bpy.data.objects.get({name!r})",
        "if obj is None:",
        f"    raise ValueError('Object ' + {name!r})",
    ]
    if location is not None:
        lines.append(f"obj.location = {tuple(location)}")
    if rotation is not None:
        lines.append(
            "obj.rotation_euler = (math.radians({0!r}), math.radians({1!r}), "
            "math.radians({2!r}))".format(rotation[0], rotation[1], rotation[2])
        )
    if scale is not None:
        lines.append(f"obj.scale = {tuple(scale)}")
    if color is not None:
        lines.extend(
            (
                "mat = bpy.data.materials.new(name='MCP_Material')",
                "mat.use_nodes = True",
                "bsdf = mat.node_tree.nodes.get('Principled BSDF')",
                "if bsdf:",
                f"    bsdf.inputs['Base Color'].default_value = {tuple(color)}",
                "if obj.data.materials:",
                "    obj.data.materials[0] = mat",
                "else:",
                "    obj.data.materials.append(mat)",
            )
        )
    if visible is not None:
        lines.append(f"obj.hide_viewport = {not visible}")
        lines.append(f"obj.hide_render = {not visible}")
    lines.append(f"print('Modified: ' + {name!r})")
    return "\n".join(lines) + "\n"


# ─── Response formatting ───────────────────────────────────────────────────


def _format_location(loc: Any) -> str:
    try:
        return f"({float(loc[0]):.2f}, {float(loc[1]):.2f}, {float(loc[2]):.2f})"
    except (TypeError, ValueError, IndexError):
        return str(loc)


def _format_scene(data: Any) -> list[str]:
    lines = ["Blender scene info:"]
    if isinstance(data, dict):
        if data.get("scene_name"):
            lines.append(f"  Scene: {data['scene_name']}")
        objects = data.get("objects") or []
        if objects:
            lines.append(f"  Objects ({len(objects)}):")
            for obj in objects[:_MAX_OBJECTS_DISPLAYED]:
                if isinstance(obj, dict):
                    lines.append(
                        f"    - {obj.get('name', '?')} ({obj.get('type', '?')}) "
                        f"pos={_format_location(obj.get('location', [0, 0, 0]))}"
                    )
                else:
                    lines.append(f"    - {obj}")
            if len(objects) > _MAX_OBJECTS_DISPLAYED:
                lines.append(f"    ... and {len(objects) - _MAX_OBJECTS_DISPLAYED} more")
        else:
            lines.append("  (empty scene)")
        return lines
    return [f"  {data}"]


def _format_object(data: Any) -> list[str]:
    if isinstance(data, dict):
        lines = [f"Object: {data.get('name', '?')}"]
        for key, value in data.items():
            if key == "name":
                continue
            if key == "location":
                value = _format_location(value)
            lines.append(f"  {key}: {value}")
        return lines
    return [f"  {data}"]


def _format_response(result: dict[str, Any]) -> str:
    """Render one addon reply as readable text."""
    if result.get("status") == "error":
        return f"Blender error: {result.get('message', 'unknown error')}"

    data = result.get("result", result)
    if isinstance(data, str):
        return data.strip() or "OK"
    if isinstance(data, dict):
        if "objects" in data or "scene_name" in data:
            return "\n".join(_format_scene(data))
        if "name" in data and "type" in data:
            return "\n".join(_format_object(data))
        return json.dumps(data, indent=2, ensure_ascii=False)
    if isinstance(data, list):
        return json.dumps(data, indent=2, ensure_ascii=False)
    return str(data)


# ─── Tool handlers (sync, run in a thread by the async wrappers) ───────────


def _disabled() -> str:
    return "Blender MCP is disabled (blender.enabled: false)"


def _require_enabled() -> tuple[BlenderClient, str] | tuple[None, str]:
    """Return (client, "") when enabled, else (None, reason)."""
    host, port, timeout, enabled = _client_settings()
    if not enabled:
        return None, _disabled()
    return BlenderClient(host=host, port=port, timeout=timeout), ""


def blender_execute(args: dict[str, Any]) -> str:
    code = str(args.get("code", "") or "").strip()
    if not code:
        return "Field 'code' is required"
    if len(code.encode("utf-8")) > _MAX_CODE_BYTES:
        return f"Code is larger than {_MAX_CODE_BYTES // 1024}KB"
    client, reason = _require_enabled()
    if client is None:
        return reason
    return _format_response(client.execute_code(code))


def blender_status(args: dict[str, Any]) -> str:
    client, reason = _require_enabled()
    if client is None:
        return reason
    result = client.check_connection()
    if result.get("status") == "error":
        return _format_response(result)
    return f"Blender connected at {client.host}:{client.port}\n" + _format_response(result)


def blender_scene_info(args: dict[str, Any]) -> str:
    client, reason = _require_enabled()
    if client is None:
        return reason
    return _format_response(client.get_scene_info())


def blender_object_info(args: dict[str, Any]) -> str:
    name = str(args.get("name", "") or "").strip()
    if not name:
        return "Field 'name' is required"
    client, reason = _require_enabled()
    if client is None:
        return reason
    return _format_response(client.get_object_info(name))


def blender_create(args: dict[str, Any]) -> str:
    obj_type = str(args.get("type", "") or "").strip().lower()
    if not obj_type:
        return "Field 'type' is required"
    if obj_type not in _CREATE_CODE:
        # Allow a partial match so "ball" -> sphere style typos still work.
        for key in PRIMITIVE_TYPES:
            if key in obj_type or obj_type in key:
                obj_type = key
                break
        else:
            return (
                f"Unknown object type '{obj_type}'. "
                f"Available: {', '.join(PRIMITIVE_TYPES)}"
            )

    name = str(args.get("name", "") or "").strip()
    location = _py_tuple3(
        (args.get("location_x"), args.get("location_y"), args.get("location_z")),
        (0.0, 0.0, 0.0),
    )
    size = _py_float(args.get("size"), 1.0)
    if size <= 0:
        size = 1.0

    client, reason = _require_enabled()
    if client is None:
        return reason
    return _format_response(client.execute_code(_create_code(obj_type, name, location, size)))


def blender_modify(args: dict[str, Any]) -> str:
    name = str(args.get("name", "") or "").strip()
    if not name:
        return "Field 'name' is required"

    location = rotation = scale = color = None
    visible: bool | None = None

    if any(args.get(f"location_{axis}") is not None for axis in "xyz"):
        location = _py_tuple3(
            (args.get("location_x"), args.get("location_y"), args.get("location_z")),
            (0.0, 0.0, 0.0),
        )
    if any(args.get(f"rotation_{axis}") is not None for axis in "xyz"):
        rotation = _py_tuple3(
            (args.get("rotation_x"), args.get("rotation_y"), args.get("rotation_z")),
            (0.0, 0.0, 0.0),
        )
    if any(args.get(f"scale_{axis}") is not None for axis in "xyz"):
        scale = _py_tuple3(
            (args.get("scale_x"), args.get("scale_y"), args.get("scale_z")),
            (1.0, 1.0, 1.0),
        )
    if any(args.get(f"color_{channel}") is not None for channel in "rgb"):
        color = (
            _py_float(args.get("color_r"), 1.0),
            _py_float(args.get("color_g"), 1.0),
            _py_float(args.get("color_b"), 1.0),
        )
    if "visible" in args and args["visible"] is not None:
        visible = bool(args["visible"])

    if location is rotation is scale is color is None and visible is None:
        return "No property to change was given"

    client, reason = _require_enabled()
    if client is None:
        return reason
    return _format_response(
        client.execute_code(_modify_code(name, location, rotation, scale, color, visible))
    )


def blender_delete(args: dict[str, Any]) -> str:
    name = str(args.get("name", "") or "").strip()
    if not name:
        return "Field 'name' is required"
    client, reason = _require_enabled()
    if client is None:
        return reason
    if name.upper() == "ALL":
        return _format_response(client.clear_scene())
    return _format_response(client.delete_object(name))


def blender_render(args: dict[str, Any]) -> str:
    output_path = str(args.get("output_path", "") or "").strip()
    resolution_x = int(_py_float(args.get("resolution_x"), 1920))
    resolution_y = int(_py_float(args.get("resolution_y"), 1080))
    client, reason = _require_enabled()
    if client is None:
        return reason
    return _format_response(client.render_scene(output_path, resolution_x, resolution_y))


# ─── Async wrappers (the MCP framework awaits coroutines) ──────────────────


async def _async(fn: Any, args: dict[str, Any]) -> str:
    """Run a blocking handler in a worker thread."""
    return await asyncio.to_thread(fn, args)


async def a_blender_execute(args: dict[str, Any]) -> str:
    return await _async(blender_execute, args)


async def a_blender_status(args: dict[str, Any]) -> str:
    return await _async(blender_status, args)


async def a_blender_scene_info(args: dict[str, Any]) -> str:
    return await _async(blender_scene_info, args)


async def a_blender_object_info(args: dict[str, Any]) -> str:
    return await _async(blender_object_info, args)


async def a_blender_create(args: dict[str, Any]) -> str:
    return await _async(blender_create, args)


async def a_blender_modify(args: dict[str, Any]) -> str:
    return await _async(blender_modify, args)


async def a_blender_delete(args: dict[str, Any]) -> str:
    return await _async(blender_delete, args)


async def a_blender_render(args: dict[str, Any]) -> str:
    return await _async(blender_render, args)
