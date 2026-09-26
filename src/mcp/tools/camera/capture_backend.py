"""Camera capture backends: OpenCV/UVC (desktop + Pi USB) and Picamera2 (Pi CSI).

Desktop USB cameras use OpenCV; the Raspberry Pi CSI official stack uses picamera2 (optional dependency).
With ``backend=auto``, OpenCV is tried first and picamera2 is used as a fallback on failure.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()

_CAPTURE_TIMEOUT_S = 20.0
# Windows: opening a camera via DirectShow takes 1-4s the first time (the driver enumerates
# media types), and the device needs a short cooldown after release before it can be reopened.
# Without this, a capture right after the settings scan fails or times out.
_WIN_OPEN_COOLDOWN_S = 0.6


@dataclass
class CaptureConfig:
    """Capture parameters (from CAMERA.* configuration)."""

    camera_index: int = 0
    device: str = ""  # e.g. /dev/video0; takes precedence over index when non-empty
    backend: str = "auto"  # auto | opencv | picamera2
    frame_width: int = 640
    frame_height: int = 480
    warm_up_frames: int = 5
    jpeg_max_side: int = 320


@dataclass
class CameraDeviceInfo:
    """Visible/optional camera entries."""

    key: str  # identifier written to the configuration: numeric index string / device path / "picamera2"
    name: str
    kind: str  # opencv | v4l2 | picamera2
    index: int | None = None
    path: str | None = None


def load_capture_config() -> CaptureConfig:
    """Read the CAMERA capture configuration from ConfigManager."""
    cfg = get_config()
    raw_backend = (
        str(cfg.get_config("CAMERA.backend", "auto") or "auto").strip().lower()
    )
    if raw_backend not in ("auto", "opencv", "picamera2"):
        raw_backend = "auto"

    device = str(cfg.get_config("CAMERA.device", "") or "").strip()
    try:
        index = int(cfg.get_config("CAMERA.camera_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    try:
        width = int(cfg.get_config("CAMERA.frame_width", 640) or 640)
    except (TypeError, ValueError):
        width = 640
    try:
        height = int(cfg.get_config("CAMERA.frame_height", 480) or 480)
    except (TypeError, ValueError):
        height = 480
    try:
        warm = int(cfg.get_config("CAMERA.warm_up_frames", 5) or 5)
    except (TypeError, ValueError):
        warm = 5
    warm = max(0, min(warm, 30))

    return CaptureConfig(
        camera_index=max(0, index),
        device=device,
        backend=raw_backend,
        frame_width=max(1, width),
        frame_height=max(1, height),
        warm_up_frames=warm,
    )


def _silence_opencv_logs() -> None:
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    try:
        import cv2

        if hasattr(cv2, "setLogLevel"):
            cv2.setLogLevel(0)
        if hasattr(cv2, "utils") and hasattr(cv2.utils, "logging"):
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass


def _opencv_open_kwargs():
    """Prefer the platform's native capture backend.

    Linux: V4L2. Windows: DirectShow (MSMF is absent from opencv-python-headless, and the
    default backend order tries FFMPEG first, which warns about libavdevice and can block).
    Other platforms: let OpenCV pick the default.
    """
    try:
        import cv2

        if sys.platform.startswith("linux") and hasattr(cv2, "CAP_V4L2"):
            return {"apiPreference": cv2.CAP_V4L2}
        if sys.platform.startswith("win") and hasattr(cv2, "CAP_DSHOW"):
            return {"apiPreference": cv2.CAP_DSHOW}
    except Exception:
        pass
    return {}


def _open_capture(source: Any):
    """Open a VideoCapture; source is an int index or a device path string."""
    import cv2

    kwargs = _opencv_open_kwargs()
    if kwargs:
        cap = cv2.VideoCapture(source, kwargs["apiPreference"])
        if cap is not None and cap.isOpened():
            return cap
        _release_capture(cap)
    return cv2.VideoCapture(source)


def _release_capture(cap) -> None:
    """Release a VideoCapture and give Windows drivers a short cooldown before reuse.

    DirectShow holds the device for a moment after release; reopening immediately can make
    the next open block for seconds or fail outright (this is what made take_photo time out
    right after the settings camera scan).
    """
    try:
        cap.release()
    except Exception:
        pass
    if sys.platform.startswith("win"):
        time.sleep(_WIN_OPEN_COOLDOWN_S)


def _encode_bgr_jpeg(frame, max_side: int = 320) -> bytes | None:
    import cv2

    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    height, width = frame.shape[:2]
    max_dim = max(height, width)
    if max_side > 0 and max_dim > max_side:
        scale = max_side / max_dim
        frame = cv2.resize(
            frame,
            (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return None
    return buf.tobytes()


def _bgr_from_picamera_array(arr):
    """Picamera2 output is usually RGB/XRGB; convert it to OpenCV BGR."""
    import cv2
    import numpy as np

    if arr is None:
        return None
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.shape[2] == 4:
        # XRGB / BGRA etc.: take the first 3 channels as RGB and convert to BGR (picamera2 commonly outputs RGB)
        rgb = arr[:, :, :3]
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if arr.shape[2] == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return np.ascontiguousarray(arr)


def _capture_opencv(cfg: CaptureConfig) -> bytes | None:
    _silence_opencv_logs()
    import cv2

    source: Any
    if cfg.device:
        source = cfg.device
        logger.info(f"OpenCV opening device path: {source}")
    else:
        source = int(cfg.camera_index)
        logger.info(f"OpenCV opening index: {source}")

    cap = _open_capture(source)
    if not cap.isOpened():
        logger.error(f"OpenCV could not open camera source={source!r}")
        return None

    try:
        # Resolution: best effort, failures are not fatal
        if cfg.frame_width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.frame_width)
        if cfg.frame_height > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.frame_height)

        frame = None
        ret = False
        # Warm-up: the first frames from many USB/Pi devices are invalid
        loops = max(1, cfg.warm_up_frames + 1)
        for i in range(loops):
            ret, frame = cap.read()
            if ret and frame is not None and getattr(frame, "size", 0) > 0:
                # Reading one more frame is sometimes more stable, but with a valid frame we can end the warm-up tail early
                if i >= max(0, cfg.warm_up_frames - 1):
                    break
            time.sleep(0.03)

        if not ret or frame is None:
            logger.error(f"OpenCV frame read failed source={source!r}")
            return None

        jpeg = _encode_bgr_jpeg(frame, cfg.jpeg_max_side)
        if not jpeg:
            logger.error("OpenCV JPEG encoding failed")
            return None
        logger.info(
            f"OpenCV capture succeeded source={source!r} size={len(jpeg)} "
            f"backend={cap.getBackendName() if hasattr(cap, 'getBackendName') else '?'}"
        )
        return jpeg
    finally:
        _release_capture(cap)


def _picamera2_available() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        import picamera2  # noqa: F401

        return True
    except Exception:
        return False


def _capture_picamera2(cfg: CaptureConfig) -> bytes | None:
    if not sys.platform.startswith("linux"):
        logger.error("picamera2 is only supported on Linux/Raspberry Pi")
        return None
    try:
        from picamera2 import Picamera2
    except ImportError:
        logger.error(
            "picamera2 not installed; cannot use CSI camera. On Pi: sudo apt install python3-picamera2"
        )
        return None

    picam2 = None
    try:
        picam2 = Picamera2()
        # still configuration; keep the size close to the configuration, the driver picks the nearest mode
        controls = {}
        config = picam2.create_still_configuration(
            main={"size": (cfg.frame_width, cfg.frame_height)},
            controls=controls,
        )
        picam2.configure(config)
        picam2.start()
        # Give AE/AWB some time
        time.sleep(0.2)
        for _ in range(max(1, cfg.warm_up_frames)):
            try:
                picam2.capture_array()
            except Exception:
                time.sleep(0.05)
        arr = picam2.capture_array()
        bgr = _bgr_from_picamera_array(arr)
        jpeg = _encode_bgr_jpeg(bgr, cfg.jpeg_max_side)
        if not jpeg:
            logger.error("picamera2 JPEG encoding failed")
            return None
        logger.info(f"picamera2 capture succeeded size={len(jpeg)}")
        return jpeg
    except Exception as e:
        logger.error(f"picamera2 capture failed: {e}", exc_info=True)
        return None
    finally:
        if picam2 is not None:
            try:
                picam2.stop()
            except Exception:
                pass
            try:
                picam2.close()
            except Exception:
                pass


def capture_jpeg(cfg: CaptureConfig | None = None) -> bytes | None:
    """Capture one JPEG byte frame according to the configuration; return None on failure."""
    cfg = cfg or load_capture_config()
    backend = cfg.backend

    def _run() -> bytes | None:
        if backend == "opencv":
            return _capture_opencv(cfg)
        if backend == "picamera2":
            return _capture_picamera2(cfg)

        # auto: try OpenCV first (USB/UVC), then picamera2 (CSI)
        jpeg = _capture_opencv(cfg)
        if jpeg:
            return jpeg
        if _picamera2_available():
            logger.info("OpenCV capture failed; falling back to picamera2 (CSI) for auto")
            return _capture_picamera2(cfg)
        return None

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_run)
        try:
            return fut.result(timeout=_CAPTURE_TIMEOUT_S)
        except FuturesTimeout:
            logger.error(
                f"Camera capture timed out ({_CAPTURE_TIMEOUT_S}s) "
                f"backend={backend} device={cfg.device!r} index={cfg.camera_index}"
            )
            return None


def _list_v4l2_paths() -> list[str]:
    if not sys.platform.startswith("linux"):
        return []
    dev = Path("/dev")
    if not dev.exists():
        return []
    paths = sorted(
        str(p)
        for p in dev.glob("video*")
        if p.name.startswith("video") and p.name[5:].isdigit()
    )
    return paths


def list_camera_devices(
    max_index: int = 5,
    consecutive_fail_limit: int = 2,
) -> list[CameraDeviceInfo]:
    """Enumerate available cameras (settings page / scan scripts)."""
    _silence_opencv_logs()
    devices: list[CameraDeviceInfo] = []
    seen: set[str] = set()

    # 1) Linux: list /dev/video* first (makes it easier to pick the right node on Pi)
    for path in _list_v4l2_paths():
        try:

            cap = _open_capture(path)
            opened = bool(cap.isOpened())
            if opened:
                # Try reading a frame to check whether it is a real capture node
                ok = False
                try:
                    for _ in range(2):
                        ret, frame = cap.read()
                        if ret and frame is not None and getattr(frame, "size", 0) > 0:
                            ok = True
                            break
                except Exception:
                    ok = False
                _release_capture(cap)
                if ok:
                    key = path
                    if key not in seen:
                        devices.append(
                            CameraDeviceInfo(
                                key=key,
                                name=f"V4L2 {path}",
                                kind="v4l2",
                                path=path,
                            )
                        )
                        seen.add(key)
            else:
                _release_capture(cap)
        except Exception as e:
            logger.debug(f"Probe {path} failed: {e}")

    # 2) OpenCV numeric index (all platforms)
    consecutive_fail = 0
    for i in range(max(0, max_index) + 1):
        try:
            cap = _open_capture(i)
            opened = bool(cap.isOpened())
            if opened:
                key = str(i)
                # Skip duplicates already listed via /dev/video{i}
                path_alias = f"/dev/video{i}"
                if key not in seen and path_alias not in seen:
                    devices.append(
                        CameraDeviceInfo(
                            key=key,
                            name=f"Camera {i}",
                            kind="opencv",
                            index=i,
                        )
                    )
                    seen.add(key)
                _release_capture(cap)
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                _release_capture(cap)
                if consecutive_fail >= consecutive_fail_limit:
                    break
        except Exception as e:
            consecutive_fail += 1
            logger.debug(f"Probe index={i} failed: {e}")
            if consecutive_fail >= consecutive_fail_limit:
                break

    # 3) Raspberry Pi CSI
    if _picamera2_available():
        key = "picamera2"
        if key not in seen:
            devices.append(
                CameraDeviceInfo(
                    key=key,
                    name="Raspberry Pi CSI (picamera2)",
                    kind="picamera2",
                )
            )
            seen.add(key)

    logger.info(f"Camera enumeration complete: {len(devices)} device(s) {[d.name for d in devices]}")
    return devices


def apply_device_selection(key: str) -> dict[str, Any]:
    """Build the configuration fragment to write based on the enumeration key.

    Returns:
        A dict that can be updated: camera_index / device / backend
    """
    key = (key or "").strip()
    if key == "picamera2":
        return {
            "CAMERA.backend": "picamera2",
            "CAMERA.device": "",
            "CAMERA.camera_index": 0,
        }
    if key.startswith("/dev/"):
        return {
            "CAMERA.backend": "opencv",
            "CAMERA.device": key,
            "CAMERA.camera_index": 0,
        }
    try:
        idx = int(key)
    except ValueError:
        idx = 0
    return {
        "CAMERA.backend": "auto",
        "CAMERA.device": "",
        "CAMERA.camera_index": idx,
    }
