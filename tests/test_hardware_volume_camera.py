"""Tests for the volume mute tools and the camera facing (switch) tools.

The tools are thin closures over VolumeController / the camera selection helper, so these
tests cover the parts that do not touch real hardware:
* the tools are registered with the expected names and schemas;
* the mute tools really drive the controller backend;
* the camera switch maps free-form facing words onto front/back and persists the choice.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.mcp.tools.volume.backend import VolumeBackend
from src.mcp.tools.volume.register import register_volume_tools
from src.mcp.tools.volume.volume_controller import VolumeController


class _FakeBackend:
    """VolumeBackend-compatible fake (mirrors tests/test_volume_mute.py)."""

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
    controller = VolumeController.__new__(VolumeController)
    controller.logger = MagicMock()
    controller.system = "Test"
    controller.is_arm = False
    controller._backend = backend
    return controller


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_volume_backend_protocol_still_declares_mute():
    """The mute tools rely on get_muted/set_muted being part of the backend contract."""
    assert hasattr(VolumeBackend, "get_muted")
    assert hasattr(VolumeBackend, "set_muted")


def test_register_volume_tools_exposes_mute_tools():
    backend = _FakeBackend()
    controller = _controller_with_backend(backend)
    tools: list = []
    register_volume_tools(tools.append, controller)

    names = [t.name for t in tools]
    assert "self.audio_speaker.set_muted" in names
    assert "self.audio_speaker.toggle_mute" in names
    assert "self.audio_speaker.get_muted" in names
    # The pre-existing tools must survive the addition
    assert "self.audio_speaker.set_volume" in names
    assert "self.audio_speaker.get_volume" in names
    assert "self.audio_speaker.get_volume_status" in names


def test_set_muted_tool_requires_boolean_property():
    backend = _FakeBackend()
    controller = _controller_with_backend(backend)
    tools: list = []
    register_volume_tools(tools.append, controller)

    tool = next(t for t in tools if t.name == "self.audio_speaker.set_muted")
    props = tool.properties.properties
    assert len(props) == 1
    assert props[0].name == "muted"
    assert props[0].type.value == "boolean"
    assert tool.properties.get_required() == ["muted"]


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------

def test_set_muted_true_drives_backend():
    import asyncio

    backend = _FakeBackend()
    controller = _controller_with_backend(backend)
    tools: list = []
    register_volume_tools(tools.append, controller)
    tool = next(t for t in tools if t.name == "self.audio_speaker.set_muted")

    result = json.loads(asyncio.run(tool.call({"muted": True})))
    assert result["isError"] is False
    assert backend.muted is True
    assert backend.set_muted_calls == [True]


def test_set_muted_false_drives_backend():
    import asyncio

    backend = _FakeBackend()
    backend.muted = True
    controller = _controller_with_backend(backend)
    tools: list = []
    register_volume_tools(tools.append, controller)
    tool = next(t for t in tools if t.name == "self.audio_speaker.set_muted")

    asyncio.run(tool.call({"muted": False}))
    assert backend.muted is False
    assert backend.set_muted_calls == [False]


def test_toggle_mute_flips_backend_state():
    import asyncio

    backend = _FakeBackend()
    controller = _controller_with_backend(backend)
    tools: list = []
    register_volume_tools(tools.append, controller)
    tool = next(t for t in tools if t.name == "self.audio_speaker.toggle_mute")

    result = json.loads(asyncio.run(tool.call({})))
    assert backend.muted is True
    assert json.loads(result["content"][0]["text"])["muted"] is True

    result = json.loads(asyncio.run(tool.call({})))
    assert backend.muted is False
    assert json.loads(result["content"][0]["text"])["muted"] is False


def test_get_muted_reports_backend_state():
    import asyncio

    backend = _FakeBackend()
    backend.muted = True
    controller = _controller_with_backend(backend)
    tools: list = []
    register_volume_tools(tools.append, controller)
    tool = next(t for t in tools if t.name == "self.audio_speaker.get_muted")

    payload = json.loads(json.loads(asyncio.run(tool.call({})))["content"][0]["text"])
    assert payload["muted"] is True
    assert payload["available"] is True


def test_mute_tools_report_unavailable_without_controller():
    """When the controller is missing the tools must fail gracefully, not raise."""
    import asyncio

    tools: list = []
    register_volume_tools(tools.append, None)

    set_tool = next(t for t in tools if t.name == "self.audio_speaker.set_muted")
    payload = json.loads(json.loads(asyncio.run(set_tool.call({"muted": True})))["content"][0]["text"])
    assert payload["success"] is False

    toggle = next(t for t in tools if t.name == "self.audio_speaker.toggle_mute")
    payload = json.loads(json.loads(asyncio.run(toggle.call({})))["content"][0]["text"])
    assert payload["success"] is False

    get_tool = next(t for t in tools if t.name == "self.audio_speaker.get_muted")
    payload = json.loads(json.loads(asyncio.run(get_tool.call({})))["content"][0]["text"])
    assert payload["available"] is False


# ---------------------------------------------------------------------------
# Camera facing tools
# ---------------------------------------------------------------------------

def _register_camera_tools(monkeypatch) -> list:
    from src.mcp.tools.camera.register import register_camera_tools

    camera = MagicMock()
    tools: list = []
    register_camera_tools(tools.append, camera)
    return tools


def test_register_camera_tools_exposes_facing_tools(monkeypatch):
    """register_camera_tools must add the switch + status tools alongside take_photo."""
    tools = _register_camera_tools(monkeypatch)

    names = [t.name for t in tools]
    assert "take_photo" in names
    assert "self.camera.switch" in names
    assert "self.camera.get_facing" in names


def test_camera_switch_tool_has_facing_property(monkeypatch):
    tools = _register_camera_tools(monkeypatch)

    tool = next(t for t in tools if t.name == "self.camera.switch")
    props = tool.properties.properties
    assert len(props) == 1
    assert props[0].name == "facing"
    assert props[0].type.value == "string"
    assert tool.properties.get_required() == ["facing"]


def test_normalize_facing_accepts_indonesian_and_english():
    from src.mcp.tools.camera.selection import _normalize_facing

    assert _normalize_facing("front") == "front"
    assert _normalize_facing("BACK") == "back"
    assert _normalize_facing("depan") == "front"
    assert _normalize_facing("kamera belakang") == "back"
    assert _normalize_facing("selfie") == "front"
    assert _normalize_facing("rear") == "back"
    assert _normalize_facing("") == ""
    assert _normalize_facing("sideways") == ""


class _FakeCameraConfig:
    """Minimal config stand-in for the camera selection service."""

    def __init__(self, index: int = 0, device: str = "", backend: str = "auto"):
        self.index = index
        self.device = device
        self.backend = backend
        self.updates: list[dict] = []

    def get_config(self, path, default=None):
        return {
            "CAMERA.camera_index": self.index,
            "CAMERA.device": self.device,
            "CAMERA.backend": self.backend,
        }.get(path, default)

    def update_configs(self, updates):
        self.updates.append(updates)
        return True


def test_switch_camera_rejects_unknown_facing(monkeypatch):
    import asyncio

    tools = _register_camera_tools(monkeypatch)
    tool = next(t for t in tools if t.name == "self.camera.switch")

    payload = json.loads(json.loads(asyncio.run(tool.call({"facing": "sideways"})))["content"][0]["text"])
    assert payload["success"] is False
    assert "front" in payload["reason"]


def test_switch_camera_picks_and_persists_back(monkeypatch):
    """With two cameras, 'back' selects the lowest index and writes it to config."""
    from src.mcp.tools.camera import selection

    monkeypatch.setattr(
        selection,
        "_list_devices",
        lambda: [("0", 0, "Camera 0"), ("1", 1, "Camera 1")],
    )
    monkeypatch.setattr(selection, "_v4l2_orientations", lambda: {})

    fake = _FakeCameraConfig()
    monkeypatch.setattr("src.utils.config_manager.get_config", lambda: fake)

    payload = json.loads(selection.switch_camera_sync("back"))

    assert payload["success"] is True
    assert fake.updates == [
        {
            "CAMERA.backend": "auto",
            "CAMERA.device": "",
            "CAMERA.camera_index": 0,
        }
    ]


def test_switch_camera_picks_front_as_second_index(monkeypatch):
    from src.mcp.tools.camera import selection

    monkeypatch.setattr(
        selection,
        "_list_devices",
        lambda: [("0", 0, "Camera 0"), ("1", 1, "Camera 1")],
    )
    monkeypatch.setattr(selection, "_v4l2_orientations", lambda: {})

    fake = _FakeCameraConfig()
    monkeypatch.setattr("src.utils.config_manager.get_config", lambda: fake)

    payload = json.loads(selection.switch_camera_sync("depan"))

    assert payload["success"] is True
    assert fake.updates == [
        {
            "CAMERA.backend": "auto",
            "CAMERA.device": "",
            "CAMERA.camera_index": 1,
        }
    ]


def test_switch_camera_prefers_driver_orientation(monkeypatch):
    """When the V4L2 location is known it wins over the index heuristic."""
    from src.mcp.tools.camera import selection

    monkeypatch.setattr(
        selection,
        "_list_devices",
        lambda: [
            ("/dev/video0", None, "V4L2 /dev/video0"),
            ("/dev/video1", None, "V4L2 /dev/video1"),
        ],
    )
    monkeypatch.setattr(
        selection,
        "_v4l2_orientations",
        lambda: {"/dev/video0": "back", "/dev/video1": "front"},
    )

    fake = _FakeCameraConfig()
    monkeypatch.setattr("src.utils.config_manager.get_config", lambda: fake)

    payload = json.loads(selection.switch_camera_sync("front"))
    assert payload["success"] is True
    assert fake.updates == [
        {
            "CAMERA.backend": "opencv",
            "CAMERA.device": "/dev/video1",
            "CAMERA.camera_index": 0,
        }
    ]


def test_switch_camera_reports_missing_camera(monkeypatch):
    from src.mcp.tools.camera import selection

    monkeypatch.setattr(selection, "_list_devices", lambda: [("0", 0, "Camera 0")])
    monkeypatch.setattr(selection, "_v4l2_orientations", lambda: {})

    class _NoWrite(_FakeCameraConfig):
        def update_configs(self, updates):
            raise AssertionError("nothing should be written when no camera matches")

    monkeypatch.setattr("src.utils.config_manager.get_config", lambda: _NoWrite())

    payload = json.loads(selection.switch_camera_sync("front"))
    assert payload["success"] is False
    assert "front" in payload["reason"]


def test_get_facing_reports_current_camera(monkeypatch):
    from src.mcp.tools.camera import selection

    monkeypatch.setattr(
        selection,
        "_list_devices",
        lambda: [("0", 0, "Camera 0"), ("1", 1, "Camera 1")],
    )
    monkeypatch.setattr(selection, "_v4l2_orientations", lambda: {})

    class _NoWrite(_FakeCameraConfig):
        def update_configs(self, updates):
            raise AssertionError("get_facing must not write config")

    monkeypatch.setattr("src.utils.config_manager.get_config", lambda: _NoWrite(index=1))

    payload = json.loads(selection.get_camera_facing_sync())
    assert payload["facing"] == "front"
    assert payload["available"] is True
    assert payload["front"] == ["Camera 1"]
    assert payload["back"] == ["Camera 0"]
