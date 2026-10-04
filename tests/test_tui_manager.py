from types import SimpleNamespace

from src.core.event_bus import EventBus
from src.ui.tui.manager import TuiViewManager


def test_tui_manager_coalesces_chat_updates():
    manager = TuiViewManager(event_bus=EventBus())
    app = SimpleNamespace()
    manager._app = app

    scheduled = []

    def fake_call_soon_threadsafe(callback):
        scheduled.append(callback)

    app._loop = SimpleNamespace(is_closed=lambda: False, call_soon_threadsafe=fake_call_soon_threadsafe)

    manager.set_chat_text("hello")
    manager.set_chat_text("hello world")

    assert len(scheduled) == 1

    scheduled[0]()
    assert app.chat_text == "hello world"
