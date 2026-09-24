"""Plugin manager.

Manages plugin lifecycles and supports dependency declarations and topological sorting.
"""

from typing import TYPE_CHECKING, Any, List, Optional

from src.logging import get_logger

from .base import Plugin

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext

logger = get_logger()


class PluginManager:
    """Plugin manager.

    Responsibilities:
    - topologically sort by dependency order
    - automatically inject plugin dependencies
    - unified setup/start/stop broadcasting
    - error isolation so one plugin failure does not affect others
    - failure flag + skip downstream when dependencies fail
    """

    def __init__(self) -> None:
        self._plugins: List[Plugin] = []
        self._by_name: dict[str, Plugin] = {}
        self._sorted: bool = False

    def register(self, *plugins: Plugin) -> None:
        """Register plugin.

        Sort by priority first; later setup_all will apply topological sort by dependencies.
        """
        sorted_plugins = sorted(plugins, key=lambda p: getattr(p, "priority", 50))
        for p in sorted_plugins:
            if p not in self._plugins:
                self._plugins.append(p)
                try:
                    name = getattr(p, "name", None)
                    if isinstance(name, str) and name:
                        self._by_name[name] = p
                except Exception as e:
                    logger.error(f"Plugin registration failed: {e}", exc_info=True)
        self._sorted = False

    def get_plugin(self, name: str) -> Optional[Plugin]:
        """Get the plugin instance by plugin name."""
        return self._by_name.get(name)

    def is_failed(self, name: str) -> bool:
        """Return whether the plugin failed (not registered is treated as failure)."""
        plugin = self._by_name.get(name)
        return plugin is None or plugin.failed

    def failed_plugins(self) -> List[str]:
        """Return the names of all failed plugins."""
        return [
            p.name
            for p in self._plugins
            if getattr(p, "name", None) and p.failed
        ]

    def _dependencies_ok(self, plugin: Plugin) -> bool:
        """Check whether the dependencies declared by the plugin are all available (registered and not failed)."""
        requires = getattr(plugin, "requires", []) or []
        for dep_name in requires:
            dep = self._by_name.get(dep_name)
            if dep is None:
                logger.warning(
                    f"Plugin {getattr(plugin, 'name', 'unknown')} dependency {dep_name} is not registered"
                )
                return False
            if dep.failed:
                logger.warning(
                    f"Plugin {getattr(plugin, 'name', 'unknown')} dependency {dep_name} failed; skipping"
                )
                return False
        return True

    def _active_plugins(self) -> List[Plugin]:
        """List of plugins that have not failed."""
        return [p for p in self._plugins if not p.failed]

    def _topological_sort(self) -> List[Plugin]:
        """Topologically sort the plugin list so dependencies are initialized before their dependents.

        Returns:
            Sorted plugin list

        Raises:
            ValueError: circular dependency exists
        """
        # Build dependency graph
        in_degree: dict[str, int] = {}
        dependents: dict[str, List[str]] = {}  # Depends on whom

        for p in self._plugins:
            name = getattr(p, "name", "")
            if name:
                in_degree[name] = 0
                dependents[name] = []

        for p in self._plugins:
            name = getattr(p, "name", "")
            requires = getattr(p, "requires", []) or []
            for dep in requires:
                if dep in self._by_name:
                    in_degree[name] = in_degree.get(name, 0) + 1
                    dependents[dep].append(name)
                else:
                    logger.warning(f"Plugin {name} declared dependency {dep} is not registered; ignoring")

        # Kahn algorithm
        queue = [name for name, degree in in_degree.items() if degree == 0]
        result: List[Plugin] = []

        while queue:
            # Pick the node with in-degree 0 that has the smallest priority
            queue.sort(
                key=lambda n: getattr(self._by_name.get(n), "priority", 50)
            )
            current = queue.pop(0)
            plugin = self._by_name.get(current)
            if plugin:
                result.append(plugin)

            for dependent in dependents.get(current, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len([p for p in self._plugins if getattr(p, "name", "")]):
            raise ValueError("Plugin has circular dependency")

        # Append plugins without a name at the end
        unnamed = [p for p in self._plugins if not getattr(p, "name", "")]
        result.extend(unnamed)

        return result

    def _inject_dependencies(self) -> None:
        """Inject each plugin's declared dependencies."""
        for p in self._plugins:
            requires = getattr(p, "requires", []) or []
            for dep_name in requires:
                dep_plugin = self._by_name.get(dep_name)
                if dep_plugin:
                    p._inject_dependency(dep_name, dep_plugin)
                    logger.debug(f"Injecting dependency: {p.name} <- {dep_name}")

    async def setup_all(self, ctx: "PluginContext", cmd: "PluginCommands") -> None:
        """Initialize all plugins.

        Initializes in topologically sorted order and injects dependencies automatically.
        Plugins whose setup fails or whose dependencies fail are marked failed and skipped
        in subsequent lifecycle steps.

        Args:
            ctx: plugin context
            cmd: plugin command interface
        """
        # Topological sort
        if not self._sorted:
            try:
                self._plugins = self._topological_sort()
                self._sorted = True
                logger.info(
                    f"Plugin topological sort complete: {[p.name for p in self._plugins if hasattr(p, 'name')]}"
                )
            except ValueError as e:
                logger.error(f"Plugin ordering failed: {e}", exc_info=True)
                # Fall back to priority sorting
                self._plugins.sort(key=lambda p: getattr(p, "priority", 50))

        # Inject dependencies
        self._inject_dependencies()

        # initialize
        for p in list(self._plugins):
            name = getattr(p, "name", "unknown")
            if p.failed:
                continue
            if not self._dependencies_ok(p):
                p.mark_failed()
                logger.error(f"Plugin {name} skipped setup due to failed dependency")
                continue
            try:
                await p.setup(ctx, cmd)
            except Exception as e:
                p.mark_failed()
                logger.error(f"Plugin {name} setup failed: {e}", exc_info=True)

    async def start_all(self) -> None:
        """Start all plugins that have not failed."""
        for p in list(self._plugins):
            if p.failed:
                continue
            if not self._dependencies_ok(p):
                p.mark_failed()
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} skipped start due to failed dependency"
                )
                continue
            try:
                await p.start()
            except Exception as e:
                p.mark_failed()
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} start failed: {e}",
                    exc_info=True,
                )

    async def notify_protocol_connected(self, protocol: Any) -> None:
        """Notify that the protocol is connected."""
        for p in self._active_plugins():
            try:
                if p.on_protocol_connected:
                    await p.on_protocol_connected(protocol)
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} on_protocol_connected failed: {e}",
                    exc_info=True,
                )

    async def notify_incoming_json(self, message: Any) -> None:
        """Notify when a JSON message is received."""
        for p in self._active_plugins():
            try:
                await p.on_incoming_json(message)
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} on_incoming_json failed: {e}",
                    exc_info=True,
                )

    async def notify_incoming_audio(self, data: bytes) -> None:
        """Notify when audio data is received."""
        for p in self._active_plugins():
            try:
                await p.on_incoming_audio(data)
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} on_incoming_audio failed: {e}",
                    exc_info=True,
                )

    async def notify_device_state_changed(self, state: Any) -> None:
        """Notify when device state changes."""
        for p in self._active_plugins():
            try:
                await p.on_device_state_changed(state)
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} on_device_state_changed failed: {e}",
                    exc_info=True,
                )

    async def stop_all(self) -> None:
        """Stop all plugins (in reverse order; failed plugins still get a stop attempt so they can clean up)."""
        for p in reversed(self._plugins):
            try:
                await p.stop()
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} stop failed: {e}",
                    exc_info=True,
                )
