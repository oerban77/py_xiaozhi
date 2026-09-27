"""
MCP Server Implementation for Python
Reference: https://modelcontextprotocol.io/specification/2024-11-05
"""

import json
from collections.abc import Callable
from typing import Any

from src.constants.system import SystemConstants
from src.logging import get_logger
from src.mcp.tooling import McpTool, PropertyList

logger = get_logger()


class McpServer:
    """
    MCP server implementation.

    Created by ServiceContainer and injected through McpPlugin.
    """

    def __init__(self):
        self.tools: list[McpTool] = []
        self._send_callback: Callable | None = None
        self._camera = None
        # external plugin tool_name -> plugin_id
        self._plugin_tool_owner: dict[str, str] = {}

    def set_send_callback(self, callback: Callable | None):
        """
        Set callback function for sending messages; pass None to unbind.
        """
        self._send_callback = callback

    def set_camera(self, camera) -> None:
        """Inject camera (shared by vision config / take_photo)."""
        self._camera = camera

    def get_camera(self):
        return self._camera

    def detach(self) -> None:
        """Unbind runtime dependencies when the container closes, keeping the tool list for reuse in the same process."""
        self._send_callback = None
        self._camera = None

    def add_tool(
        self, tool: McpTool | tuple[str, str, PropertyList, Callable]
    ):
        """
        Add tool.
        """
        if isinstance(tool, tuple):
            # Create McpTool from parameters
            name, description, properties, callback = tool
            tool = McpTool(name, description, properties, callback)

        # Check if already exists
        if any(t.name == tool.name for t in self.tools):
            logger.warning(f"Tool {tool.name} already added(duplicate registration rejected)")
            return

        logger.info(f"Add tool: {tool.name}")
        self.tools.append(tool)

    def remove_tools_by_names(self, names: set[str] | list[str]) -> int:
        """Remove tools by name and return the number removed."""
        name_set = set(names)
        if not name_set:
            return 0
        before = len(self.tools)
        self.tools = [t for t in self.tools if t.name not in name_set]
        return before - len(self.tools)

    def unload_plugin(self, plugin_id: str) -> int:
        """Unregister tools registered by external plugin."""
        from src.mcp.plugins.registry import PluginRegistry

        reg = PluginRegistry(tool_owner=self._plugin_tool_owner)
        return reg.unload_plugin(self, plugin_id)

    def reload_external_plugins(
        self, music_player=None, volume_controller=None
    ) -> list:
        """Reload external plugins only: remove known external tools first, then scan and load according to config.

        Note: built-in tools are not re-registered; built-ins should already be present in tools.
        """
        # Remove all currently registered external tools
        if self._plugin_tool_owner:
            names = set(self._plugin_tool_owner.keys())
            self.remove_tools_by_names(names)
            self._plugin_tool_owner.clear()

        from src.mcp.plugins.loader import load_mcp_plugins_from_config

        capabilities = {}
        if music_player is not None:
            capabilities["music_player"] = music_player
        try:
            from src.utils.config_manager import get_config

            capabilities["config_readonly"] = get_config()
        except Exception:
            pass

        return load_mcp_plugins_from_config(
            self.add_tool,
            capabilities=capabilities,
            tool_owner=self._plugin_tool_owner,
        )

    def add_common_tools(self, music_player=None, volume_controller=None):
        """
        Add common tools (all explicitly mounted via register_*).

        music_player: MusicPlayer injected by the container; registers music tools when provided.
        volume_controller: optional injected VolumeController; created internally when not provided.
        camera / screenshot are separately registered by McpPlugin during setup.
        """
        # Back up the original tool list
        original_tools = self.tools.copy()
        self.tools.clear()

        if music_player is not None:
            from src.mcp.tools.music import register_music_tools

            register_music_tools(self.add_tool, music_player)

        from src.mcp.tools.app import register_app_tools
        from src.mcp.tools.prayer import register_prayer_tools
        from src.mcp.tools.reminder import register_reminder_tools
        from src.mcp.tools.smarthome import register_smarthome_tools
        from src.mcp.tools.volume import register_volume_tools
        from src.mcp.tools.weather import register_weather_tools
        from src.mcp.tools.websearch import register_websearch_tools

        register_volume_tools(self.add_tool, volume_controller)
        register_app_tools(self.add_tool)
        register_weather_tools(self.add_tool)
        register_websearch_tools(self.add_tool)
        register_prayer_tools(self.add_tool)
        register_reminder_tools(self.add_tool)
        register_smarthome_tools(self.add_tool)

        # External: user-directory plugin package (includes lib/), failure isolated
        try:
            from src.mcp.plugins.loader import load_mcp_plugins_from_config

            capabilities = {}
            if music_player is not None:
                capabilities["music_player"] = music_player
            try:
                from src.utils.config_manager import get_config

                capabilities["config_readonly"] = get_config()
            except Exception:
                pass

            load_mcp_plugins_from_config(
                self.add_tool,
                capabilities=capabilities,
                tool_owner=self._plugin_tool_owner,
            )
        except Exception as e:
            logger.error(f"Failed to load external MCP plugin: {e}", exc_info=True)

        # Restore the original tools
        self.tools.extend(original_tools)

    async def parse_message(self, message: str | dict[str, Any]):
        """
        Parse MCP message.
        """
        request_id = None
        try:
            if isinstance(message, str):
                data = json.loads(message)
            else:
                data = message

            logger.info(
                f"[MCP] Parsed message: {json.dumps(data, ensure_ascii=False, indent=2)}"
            )

            # Check JSON-RPC version
            if data.get("jsonrpc") != "2.0":
                logger.error(f"Invalid JSONRPC version: {data.get('jsonrpc')}")
                return

            method = data.get("method")
            if not method:
                logger.error("Missing method")
                return

            # Ignore notifications
            if method.startswith("notifications"):
                logger.info(f"[MCP] Ignoring notification: {method}")
                return

            params = data.get("params", {})
            request_id = data.get("id")

            if request_id is None:
                logger.error(f"Invalid id for method: {method}")
                return

            logger.info(
                f"[MCP] Handling method: {method}, ID: {request_id}, params: {params}"
            )

            # Handle different methods
            if method == "initialize":
                await self._handle_initialize(request_id, params)
            elif method == "tools/list":
                await self._handle_tools_list(request_id, params)
            elif method == "tools/call":
                await self._handle_tool_call(request_id, params)
            else:
                logger.error(f"Method not implemented: {method}")
                await self._reply_error(
                    request_id, f"Method not implemented: {method}"
                )

        except Exception as e:
            logger.error(f"Error parsing MCP message: {e}", exc_info=True)
            if request_id is not None:
                await self._reply_error(request_id, str(e))

    async def _handle_initialize(
        self, request_id: int, params: dict[str, Any]
    ):
        """
        Handle initialization request.
        """
        # Parse capabilities
        capabilities = params.get("capabilities", {})
        await self._parse_capabilities(capabilities)

        # Return server information
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": SystemConstants.APP_NAME,
                "version": SystemConstants.APP_VERSION,
            },
        }

        await self._reply_result(request_id, result)

    def _disabled_tool_names(self) -> set[str]:
        """Read MCP_TOOLS.DISABLED (the blocklist) from the configuration."""
        try:
            from src.mcp.tool_catalog import normalize_disabled
            from src.utils.config_manager import get_config

            raw = get_config().get_config("MCP_TOOLS.DISABLED", []) or []
            return set(normalize_disabled(raw))
        except Exception:
            return set()

    def _iter_enabled_tools(self):
        disabled = self._disabled_tool_names()
        for tool in self.tools:
            if tool.name not in disabled:
                yield tool

    async def _handle_tools_list(
        self, request_id: int, params: dict[str, Any]
    ):
        """
        Handle the tool list request (already filtered by MCP_TOOLS.DISABLED).
        """
        cursor = params.get("cursor", "")
        max_payload_size = 8000

        tools_json = []
        total_size = 0
        found_cursor = not cursor
        next_cursor = ""

        for tool in self._iter_enabled_tools():
            # If the start position has not been found yet, keep searching
            if not found_cursor:
                if tool.name == cursor:
                    found_cursor = True
                else:
                    continue

            # Check the size
            tool_json = tool.to_json()
            tool_size = len(json.dumps(tool_json))

            if total_size + tool_size + 100 > max_payload_size:
                next_cursor = tool.name
                break

            tools_json.append(tool_json)
            total_size += tool_size

        result = {"tools": tools_json}
        if next_cursor:
            result["nextCursor"] = next_cursor

        await self._reply_result(request_id, result)

    async def _handle_tool_call(
        self, request_id: int, params: dict[str, Any]
    ):
        """
        Handle the tool call request.
        """
        logger.info(
            f"[MCP] Tool call request received! ID={request_id}, params={params}"
        )

        tool_name = params.get("name")
        if not tool_name:
            await self._reply_error(request_id, "Missing tool name")
            return

        logger.info(f"[MCP] Attempting to call tool: {tool_name}")

        if tool_name in self._disabled_tool_names():
            await self._reply_error(
                request_id, f"Tool disabled: {tool_name}"
            )
            return

        # Look up the tool
        tool = None
        for t in self.tools:
            if t.name == tool_name:
                tool = t
                break

        if not tool:
            await self._reply_error(
                request_id, f"Unknown tool: {tool_name}"
            )
            return

        # Get the arguments
        arguments = params.get("arguments", {})

        logger.info(f"[MCP] Executing tool {tool_name}, args: {arguments}")

        # Call the tool asynchronously
        try:
            result = await tool.call(arguments)
            logger.info(f"[MCP] Tool {tool_name} executed successfully, result: {result}")
            await self._reply_result(request_id, json.loads(result))
        except Exception as e:
            logger.error(
                f"[MCP] Tool {tool_name} execution failed: {e}", exc_info=True
            )
            await self._reply_error(request_id, str(e))

    async def _parse_capabilities(self, capabilities):
        """
        Parse capabilities.
        """
        vision = capabilities.get("vision", {})
        if vision and isinstance(vision, dict):
            url = vision.get("url")
            token = vision.get("token")
            if url:
                camera = self._camera
                if camera is None:
                    from src.mcp.tools.camera import create_camera

                    camera = create_camera()
                    self._camera = camera
                camera.set_explain_url(url)
                if token:
                    camera.set_explain_token(token)
                logger.info(f"Vision service configured with URL: {url}")

    async def _reply_result(self, request_id: int, result: Any):
        """
        Send a success response.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

        result_len = len(json.dumps(result))
        logger.info(
            f"[MCP] Sent success response: ID={request_id}, result length={result_len}"
        )

        if self._send_callback:
            await self._send_callback(json.dumps(payload))
        else:
            logger.error("[MCP] Send callback not set!")

    async def _reply_error(self, request_id: int, message: str):
        """
        Send an error response.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32603, "message": message},
        }

        logger.error(
            f"[MCP] Sent error response: ID={request_id}, error={message}"
        )

        if self._send_callback:
            await self._send_callback(json.dumps(payload))
