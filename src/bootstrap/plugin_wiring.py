"""Plugin and shared service assembly.

Centralized: creates the McpServer/MusicPlayer, registers the plugin manifest,
resource pool cleanup, and the direct audio connection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.logging import get_logger

if TYPE_CHECKING:
    from src.bootstrap.container import ServiceContainer
    from src.bootstrap.protocols import PluginCommands, PluginContext

logger = get_logger()


def bind_shared_services(container: "ServiceContainer") -> None:
    """Create the cross-plugin shared services (McpServer / MusicPlayer), owned by the container only.

    No module-level singletons are written anymore; plugins and MCP tools obtain the same
    instance through constructor injection / closures.
    """
    from src.mcp.mcp_server import McpServer
    from src.mcp.tools.music.music_player import MusicPlayer

    if container.mcp_server is None:
        container.mcp_server = McpServer()

    if container.music_player is None:
        container.music_player = MusicPlayer()

    logger.debug("Shared services created: McpServer, MusicPlayer")


def unbind_shared_services(container: "ServiceContainer") -> None:
    """Release the shared service references (called at the final stage of the resource pool)."""
    if container.music_player is not None:
        try:
            container.music_player.detach()
        except Exception as e:
            logger.debug(f"Failed to detach MusicPlayer: {e}", exc_info=True)
    if container.mcp_server is not None:
        try:
            container.mcp_server.detach()
        except Exception as e:
            logger.debug(f"Failed to detach McpServer: {e}", exc_info=True)
    container.music_player = None
    container.mcp_server = None


async def setup_plugins(
    container: "ServiceContainer",
    mode: str,
    ctx: "PluginContext",
    cmd: "PluginCommands",
) -> None:
    """Bind the shared services, register and initialize the plugins, and hook up resource cleanup and the direct audio connection."""
    from src.plugins.audio import AudioPlugin
    from src.plugins.mcp import McpPlugin
    from src.plugins.shortcuts import ShortcutsPlugin
    from src.plugins.ui import UIPlugin
    from src.plugins.wake_word import WakeWordPlugin

    bind_shared_services(container)

    # Create the plugin instances (Audio publishes the codec through events; MusicPlayer is not injected)
    audio_plugin = AudioPlugin()
    wake_word_plugin = WakeWordPlugin()
    ui_plugin = UIPlugin(
        mode=mode,
        task_manager=container.tasks,
        image_analyzer=container.mcp_server.analyze_image_file,
        pending_image_setter=container.mcp_server.set_pending_image,
        pending_document_setter=container.mcp_server.set_pending_document,
    )
    shortcuts_plugin = ShortcutsPlugin()
    mcp_plugin = McpPlugin(
        server=container.mcp_server,
        music_player=container.music_player,
    )

    container.plugins.register(
        mcp_plugin,
        audio_plugin,
        wake_word_plugin,
        ui_plugin,
        shortcuts_plugin,
    )

    await container.plugins.setup_all(ctx, cmd)

    register_cleanup_resources(container)

    # Direct settings audio channel (TTS audio does not go through the EventBus, reducing latency)
    if not audio_plugin.failed:
        container.protocol.set_audio_handler(audio_plugin.on_incoming_audio)


def register_cleanup_resources(container: "ServiceContainer") -> None:
    """Register the cleanup functions of all modules into the resource pool (registered first, released last)."""
    pool = container.resource_pool

    # Registered first = released last: unbinding the shared services happens after plugin cleanup
    pool.register(
        "shared_services", lambda: unbind_shared_services(container)
    )

    # Release the event bus last (registered earlier)
    pool.register("event_bus", container.event_bus.clear)

    # Each plugin registers its own resources
    for plugin in container.plugins._plugins:
        plugin.register_resources(pool)

    # Network connection
    pool.register("protocol", container.protocol.disconnect)

    # Async task
    pool.register("tasks", container.tasks.cancel_all)
