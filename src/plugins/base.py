"""Plugin base class.

Interacts with core services through the PluginContext and PluginCommands interfaces.
"""

import asyncio
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext
    from src.core.resource_pool import ResourcePool


class Plugin:
    """Plugin base class.

    Plugins get state through PluginContext and perform actions through PluginCommands.

    Attributes:
        name: the plugin name, used for dependency declarations and logging
        priority: the priority; the smaller the value, the higher the priority (range: 1-100)
        requires: the list of dependency plugin names, injected automatically by PluginManager

    Usage:
        class MyPlugin(Plugin):
            name = "my_plugin"
            priority = 50
            requires = ["audio"]  # Declares a dependency on AudioPlugin

            async def setup(self, ctx, cmd):
                await super().setup(ctx, cmd)
                # self.deps["audio"] returns the AudioPlugin instance
    """

    name: str = "plugin"
    priority: int = 50  # Priority; the smaller the value, the higher the priority (range: 1-100)
    requires: List[str] = []  # List of dependency plugin names

    def __init__(self) -> None:
        self._started = False
        self._failed = False
        self._ctx: "PluginContext" = None
        self._cmd: "PluginCommands" = None
        self._deps: dict[str, "Plugin"] = {}  # Injected dependency plugin instances

    @property
    def ctx(self) -> "PluginContext":
        """
        Get the plugin context.
        """
        return self._ctx

    @property
    def cmd(self) -> "PluginCommands":
        """
        Get the plugin command interface.
        """
        return self._cmd

    @property
    def deps(self) -> dict[str, "Plugin"]:
        """Get the dependency plugin instances."""
        return self._deps

    @property
    def failed(self) -> bool:
        """Whether the plugin has been marked as failed (setup/start failure or dependency failure)."""
        return self._failed

    def mark_failed(self) -> None:
        """Mark the plugin as failed; subsequent start/notify calls will be skipped."""
        self._failed = True

    def get_dep(self, name: str) -> Optional["Plugin"]:
        """Get the dependency plugin with the given name."""
        return self._deps.get(name)

    def _inject_dependency(self, name: str, plugin: "Plugin") -> None:
        """Inject a dependency plugin (called by PluginManager)."""
        self._deps[name] = plugin

    async def setup(self, ctx: "PluginContext", cmd: "PluginCommands") -> None:
        """Plugin preparation phase.

        Args:
            ctx: the plugin context (read-only state access)
            cmd: the plugin command interface (performing actions)
        """
        self._ctx = ctx
        self._cmd = cmd
        await asyncio.sleep(0)

    async def start(self) -> None:
        """
        Plugin start (usually called after the protocol connection is established).
        """
        self._started = True
        await asyncio.sleep(0)

    async def on_protocol_connected(self, protocol: Any) -> None:
        """
        Notification after the protocol channel is established.
        """
        await asyncio.sleep(0)

    async def on_incoming_json(self, message: Any) -> None:
        """
        Notification when a JSON message is received.
        """
        await asyncio.sleep(0)

    async def on_incoming_audio(self, data: bytes) -> None:
        """
        Notification when audio data is received.
        """
        await asyncio.sleep(0)

    async def on_device_state_changed(self, state: Any) -> None:
        """
        Device state change notification.
        """
        await asyncio.sleep(0)

    async def stop(self) -> None:
        """
        Plugin stop.
        """
        self._started = False
        await asyncio.sleep(0)

    def register_resources(self, pool: "ResourcePool") -> None:
        """
        Register cleanup functions with the resource pool. Subclasses override this method to register resources that need to be released.
        Resources are released in the reverse order of registration: first registered, last released.

        Args:
            pool: the resource pool instance
        """
        pass
