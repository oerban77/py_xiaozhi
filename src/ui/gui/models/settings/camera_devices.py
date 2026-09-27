"""Camera enumeration and testing."""

import sys

from PySide6.QtCore import Slot

from src.logging import get_logger

logger = get_logger()


def _camera_failure_message() -> str:
    """Platform-specific hint shown when a camera capture fails in the settings test."""
    if sys.platform.startswith("win"):
        return (
            "[FAIL] Could not capture image. Check Windows privacy settings "
            "(Settings > Privacy > Camera, allow desktop apps) and that no other "
            "app is using the camera."
        )
    if sys.platform.startswith("linux"):
        return (
            "[FAIL] Could not capture image. For Pi CSI install python3-picamera2; "
            "for USB try another index or /dev/video node."
        )
    return "[FAIL] Could not capture image. Check the camera connection and permissions."


class SettingsCameraDevicesMixin:
    # ========== Camera device list ==========

    def _load_cameras(self, force: bool = False):
        """Load the camera list on a background thread to avoid blocking the Qt main thread.

        Not called at application startup; only triggered when the settings are opened / refreshed / the camera page is shown.
        """
        if self._cameras_loading:
            return
        if self._cameras_loaded_once and not force and self._cameras:
            return

        self._cameras_loading = True
        self._run_worker(
            self._do_load_cameras,
            name="settings:scan_cameras",
            clear_flags=lambda: setattr(self, "_cameras_loading", False),
        )

    def _do_load_cameras(self):
        """Run the camera scan (OpenCV/V4L2 + optional picamera2)."""
        cameras: list[dict] = []
        try:
            from src.mcp.tools.camera.capture_backend import list_camera_devices

            devices = list_camera_devices(max_index=5, consecutive_fail_limit=2)
            for d in devices:
                cameras.append(
                    {
                        "key": d.key,
                        "index": d.index if d.index is not None else -1,
                        "name": d.name,
                        "kind": d.kind,
                        "path": d.path,
                    }
                )
            logger.info(f"Camera scan complete: found {len(cameras)}")
        except Exception as e:
            logger.error(f"Failed to scan cameras: {e}", exc_info=True)
            raise
        finally:
            self._cameras = cameras
            self._cameras_loaded_once = True
            self.camerasChanged.emit()
            self.devicesChanged.emit()
            self.statusMessage.emit(
                f"Camera list refreshed ({len(cameras)} found)"
                if cameras
                else "No camera detected (Pi CSI requires picamera2)"
            )

    @Slot(result=list)
    def getCameras(self) -> list:
        """Get the camera list (does not trigger a scan; triggered by the settings page / refresh)."""
        return [c["name"] for c in self._cameras]

    @Slot()
    def refreshCameras(self):
        """Refresh the camera list (non-blocking)."""
        self._load_cameras(force=True)

    def _current_camera_key(self) -> str:
        """Restore the currently selected enum key from config."""
        device = str(self._get_value("CAMERA.device", "") or "").strip()
        backend = (
            str(self._get_value("CAMERA.backend", "auto") or "auto").strip().lower()
        )
        if backend == "picamera2":
            return "picamera2"
        if device:
            return device
        try:
            return str(int(self._get_value("CAMERA.camera_index", 0) or 0))
        except (TypeError, ValueError):
            return "0"

    def _get_selectedCameraIndex(self) -> int:
        """Get the index of the currently selected camera in the list."""
        current_key = self._current_camera_key()
        for i, c in enumerate(self._cameras):
            if c.get("key") == current_key:
                return i
        # Backward compatibility: only index was configured
        try:
            current_idx = int(self._get_value("CAMERA.camera_index", 0) or 0)
        except (TypeError, ValueError):
            current_idx = 0
        for i, c in enumerate(self._cameras):
            if c.get("index") == current_idx and c.get("kind") in ("opencv", "v4l2"):
                return i
        return 0

    def _set_selectedCameraIndex(self, index: int):
        """Set the selected camera (writes backend/device/index)."""
        if 0 <= index < len(self._cameras):
            camera = self._cameras[index]
            from src.mcp.tools.camera.capture_backend import apply_device_selection

            updates = apply_device_selection(str(camera.get("key", "")))
            for path, value in updates.items():
                self._set_value(path, value)
            logger.info(f"Camera selected: {camera['name']} -> {updates}")

    @Slot()
    def testCamera(self):
        """Test the camera by capturing and displaying one frame."""
        if not self._cameras:
            self.statusMessage.emit("No camera available")
            return

        idx = self._get_selectedCameraIndex()
        if idx < 0 or idx >= len(self._cameras):
            self.statusMessage.emit("Please select a camera first")
            return

        camera = self._cameras[idx]
        self.statusMessage.emit(f"Testing camera {camera['name']}...")

        self._run_worker(
            self._do_camera_test,
            camera,
            name="settings:camera_test",
            test_kind="camera",
        )

    def _do_camera_test(self, camera: dict):
        """Run the camera test (via the unified capture_backend)."""
        from src.mcp.tools.camera.capture_backend import (
            CaptureConfig,
            apply_device_selection,
            capture_jpeg,
        )

        updates = apply_device_selection(str(camera.get("key", "")))
        backend = updates.get("CAMERA.backend", "auto")
        device = updates.get("CAMERA.device", "") or ""
        try:
            cam_index = int(updates.get("CAMERA.camera_index", 0) or 0)
        except (TypeError, ValueError):
            cam_index = 0

        try:
            width = int(self._get_value("CAMERA.frame_width", 640) or 640)
            height = int(self._get_value("CAMERA.frame_height", 480) or 480)
            warm = int(self._get_value("CAMERA.warm_up_frames", 5) or 5)
        except (TypeError, ValueError):
            width, height, warm = 640, 480, 5

        cfg = CaptureConfig(
            camera_index=cam_index,
            device=device,
            backend=str(backend),
            frame_width=width,
            frame_height=height,
            warm_up_frames=warm,
            jpeg_max_side=640,
        )
        jpeg = capture_jpeg(cfg)
        if not jpeg:
            self.statusMessage.emit(_camera_failure_message())
            self.testComplete.emit("camera", False)
            return

        self.statusMessage.emit(
            f"[OK] Camera working (JPEG {len(jpeg)} bytes, {camera['name']})"
        )
        self.testComplete.emit("camera", True)
