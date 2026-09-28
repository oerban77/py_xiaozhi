"""验证静音 API：VolumeController 门面 + 平台后端协议契约."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.volume.backend import VolumeBackend
from src.mcp.tools.volume.volume_controller import VolumeController


class _FakeBackend:
    """符合 VolumeBackend 协议的假后端."""

    def __init__(self) -> None:
        self.volume = 50
        self.muted = False
        self.set_muted_calls: list[bool] = []

    def get_volume(self) -> int:
        return self.volume

    def set_volume(self, volume: int) -> None:
        self.volume = volume

    def get_muted(self) -> bool:
        return self.muted

    def set_muted(self, muted: bool) -> None:
        self.set_muted_calls.append(bool(muted))
        self.muted = bool(muted)


def _controller_with_backend(backend: _FakeBackend) -> VolumeController:
    """构造 VolumeController 并替换其后端，避免触碰真实系统音频."""
    controller = VolumeController.__new__(VolumeController)
    controller.logger = MagicMock()
    controller.system = "Test"
    controller.is_arm = False
    controller._backend = backend
    return controller


def test_volume_backend_protocol_requires_mute():
    """协议必须声明静音读/写方法."""
    assert hasattr(VolumeBackend, "get_muted")
    assert hasattr(VolumeBackend, "set_muted")


def test_controller_get_muted_reads_backend():
    backend = _FakeBackend()
    backend.muted = True
    controller = _controller_with_backend(backend)

    assert controller.get_muted() is True


def test_controller_set_muted_writes_backend():
    backend = _FakeBackend()
    controller = _controller_with_backend(backend)

    controller.set_muted(True)

    assert backend.muted is True
    assert backend.set_muted_calls == [True]


def test_controller_toggle_flips_state():
    backend = _FakeBackend()
    controller = _controller_with_backend(backend)

    controller.set_muted(not controller.get_muted())

    assert backend.muted is True
    controller.set_muted(not controller.get_muted())
    assert backend.muted is False


def test_controller_get_muted_swallows_backend_errors():
    backend = _FakeBackend()
    controller = _controller_with_backend(backend)

    def _boom() -> bool:
        raise RuntimeError("backend unavailable")

    backend.get_muted = _boom  # type: ignore[method-assign]

    # 出错时回退到“未静音”，不抛异常
    assert controller.get_muted() is False


def test_controller_set_muted_swallows_backend_errors():
    backend = _FakeBackend()
    controller = _controller_with_backend(backend)

    def _boom(_muted: bool) -> None:
        raise RuntimeError("backend unavailable")

    backend.set_muted = _boom  # type: ignore[method-assign]

    controller.set_muted(True)  # 不抛异常


def test_gui_manager_handles_mute_toggle(monkeypatch):
    """GuiViewManager 应订阅 UI_MUTE_TOGGLE 并真正翻转后端静音状态."""
    import asyncio

    from src.core.event_bus import EventBus, Events
    from src.core.task_manager import TaskManager
    from src.ui.gui.manager import GuiViewManager

    bus = EventBus()
    tasks = TaskManager()
    tasks.initialize(asyncio.new_event_loop())
    vm = GuiViewManager(bus, task_manager=tasks)

    backend = _FakeBackend()
    controller = _controller_with_backend(backend)
    vm._volume_controller = controller

    async def _run():
        await bus.emit(Events.UI_MUTE_TOGGLE)
        # 事件经 TaskManager 调度，等待一轮循环
        await asyncio.sleep(0.05)

    loop = tasks.loop
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        tasks.initialize(loop)
    loop.run_until_complete(_run())
    loop.run_until_complete(asyncio.sleep(0.05))

    assert backend.muted is True
    assert vm._main.main_model.muted is True


def test_gui_manager_refresh_muted_reads_backend():
    """启动时应把后端静音状态同步到模型."""
    import asyncio

    from src.core.event_bus import EventBus
    from src.core.task_manager import TaskManager
    from src.ui.gui.manager import GuiViewManager

    tasks = TaskManager()
    loop = asyncio.new_event_loop()
    tasks.initialize(loop)

    vm = GuiViewManager(EventBus(), task_manager=tasks)
    backend = _FakeBackend()
    backend.muted = True
    vm._volume_controller = _controller_with_backend(backend)

    loop.run_until_complete(vm._refresh_muted())

    assert vm._main.main_model.muted is True
