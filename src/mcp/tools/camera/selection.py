"""Camera selection MCP service: switch between the front- and back-facing camera.

The desktop client picks a camera by writing ``CAMERA.camera_index`` / ``CAMERA.device`` /
``CAMERA.backend`` (see ``capture_backend.apply_device_selection``). This module maps the
user's "front camera" / "back camera" request onto that mechanism:

* On Linux it reads the orientation of each ``/dev/video*`` node through the V4L2
  ``location`` field (``front`` / ``back``) when the driver reports it.
* Otherwise (and on Windows/macOS, where there is no standard orientation property) it
  treats the lowest usable index as the back camera and the next one as the front
  camera — the order in which built-in laptop cameras are usually enumerated.

The selection is persisted to config so ``take_photo`` picks it up on the next capture.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()

_FACING_FRONT = "front"
_FACING_BACK = "back"
_VALID_FACINGS = (_FACING_FRONT, _FACING_BACK)


def _normalize_facing(facing: str) -> str:
    """Map free-form user input onto 'front' / 'back'."""
    value = (facing or "").strip().lower()
    if value in _VALID_FACINGS:
        return value
    # Indonesian / English synonyms the model is likely to emit
    if value in ("depan", "depan kamera", "kamera depan", "selfie", "front-facing", "front camera"):
        return _FACING_FRONT
    if value in ("belakang", "belakang kamera", "kamera belakang", "rear", "rear-facing", "back camera", "main"):
        return _FACING_BACK
    return ""


def _v4l2_orientations() -> dict[str, str]:
    """Best-effort map of /dev/video* -> 'front'/'back' from the V4L2 location field."""
    orientations: dict[str, str] = {}
    try:
        from pathlib import Path

        for entry in Path("/sys/class/video4linux").glob("video*"):
            name = entry.name
            if not name[5:].isdigit():
                continue
            location = ""
            try:
                location = (entry / "location").read_text(encoding="utf-8").strip().lower()
            except Exception:
                # Some drivers expose the orientation through the parent device uevent instead
                try:
                    uevent = (entry / "uevent").read_text(encoding="utf-8").lower()
                    match = re.search(r"location[=:]\s*(front|back)", uevent)
                    location = match.group(1) if match else ""
                except Exception:
                    location = ""
            if location in _VALID_FACINGS:
                orientations[f"/dev/{name}"] = location
    except Exception as e:
        logger.debug(f"V4L2 orientation lookup unavailable: {e}")
    return orientations


def _list_devices() -> list[tuple[str, int | None, str]]:
    """Enumerate cameras as (key, index, name) without probing frames (fast path).

    Uses ``list_camera_devices`` when OpenCV is importable, since that also validates that
    the node is a real capture device; otherwise falls back to /dev/video* on Linux.
    """
    try:
        from .capture_backend import list_camera_devices

        devices = list_camera_devices(max_index=5, consecutive_fail_limit=2)
        return [(d.key, d.index, d.name) for d in devices]
    except Exception as e:
        logger.debug(f"OpenCV camera enumeration unavailable: {e}")

    fallback: list[tuple[str, int | None, str]] = []
    try:
        from pathlib import Path

        for entry in sorted(Path("/dev").glob("video*")):
            if entry.name[5:].isdigit():
                fallback.append((str(entry), None, f"V4L2 {entry}"))
    except Exception:
        pass
    return fallback


def _current_selection() -> dict[str, Any]:
    """Read the currently configured camera (mirrors capture_backend.load_capture_config)."""
    cfg = get_config()
    backend = str(cfg.get_config("CAMERA.backend", "auto") or "auto").strip().lower()
    device = str(cfg.get_config("CAMERA.device", "") or "").strip()
    try:
        index = int(cfg.get_config("CAMERA.camera_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    return {"backend": backend, "device": device, "camera_index": index}


def _apply_selection(key: str) -> dict[str, Any]:
    """Write the camera selection to config (returns the applied fragment)."""
    from .capture_backend import apply_device_selection

    updates = apply_device_selection(key)
    if updates:
        get_config().update_configs(updates)
        logger.info(f"Camera selection applied: {key} -> {updates}")
    return updates


def _classify_devices(
    devices: list[tuple[str, int | None, str]],
    orientations: dict[str, str],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split the device list into (front, back) candidates as (key, name)."""
    front: list[tuple[str, str]] = []
    back: list[tuple[str, str]] = []

    # 1) Driver-reported orientation (reliable)
    oriented: list[tuple[str, str, str]] = []
    for key, _index, name in devices:
        location = orientations.get(key)
        if location == _FACING_FRONT:
            front.append((key, name))
        elif location == _FACING_BACK:
            back.append((key, name))
        else:
            oriented.append((key, name, ""))

    # 2) Heuristic for the remaining nodes: lowest index = back, next = front
    indexed = sorted(
        ((key, name) for key, _index, name in devices if key not in orientations),
        key=lambda item: _index_of(item[0]),
    )
    for position, (key, name) in enumerate(indexed):
        if position == 0:
            back.append((key, name))
        elif position == 1:
            front.append((key, name))
        else:
            # Extra nodes: keep them reachable through the settings page only
            logger.debug(f"Camera {name} ({key}) is not classifiable as front/back; ignoring")

    return front, back


def _index_of(key: str) -> int:
    """Sort key so numeric indices order before device paths (back camera first)."""
    if key.startswith("/dev/"):
        try:
            return int(key.rsplit("video", 1)[-1])
        except ValueError:
            return 1000
    try:
        return int(key)
    except ValueError:
        return 1001


def _pick(facing: str) -> tuple[str, str] | None:
    """Return the (key, name) of the camera facing the requested direction, or None."""
    devices = _list_devices()
    orientations = _v4l2_orientations() if facing else {}
    front, back = _classify_devices(devices, orientations)
    pool = front if facing == _FACING_FRONT else back
    if not pool:
        return None
    # Prefer the lowest index inside the pool
    return min(pool, key=lambda item: _index_of(item[0]))


def _status_payload() -> dict[str, Any]:
    """Build the camera-facing status payload from the current config."""
    selection = _current_selection()
    current_key = selection["device"] or str(selection["camera_index"])
    devices = _list_devices()
    orientations = _v4l2_orientations()
    front, back = _classify_devices(devices, orientations)

    current_facing = "unknown"
    for key, _name in front:
        if key == current_key:
            current_facing = _FACING_FRONT
            break
    if current_facing == "unknown":
        for key, _name in back:
            if key == current_key:
                current_facing = _FACING_BACK
                break

    return {
        "facing": current_facing,
        "front": [name for _key, name in front],
        "back": [name for _key, name in back],
        "current_camera": current_key,
        "available": bool(devices),
    }


def switch_camera_sync(facing: str) -> str:
    """Switch to the requested camera (front/back); returns a JSON result string."""
    normalized = _normalize_facing(facing)
    if not normalized:
        return json.dumps(
            {
                "success": False,
                "reason": (
                    f"Unknown camera facing '{facing}'. Use 'front' or 'back' "
                    "(depan / belakang)."
                ),
            },
            ensure_ascii=False,
        )

    picked = _pick(normalized)
    if picked is None:
        payload = _status_payload()
        payload["success"] = False
        payload["reason"] = (
            f"No {normalized} camera was found. Detected cameras: "
            f"front={payload.get('front')}, back={payload.get('back')}."
        )
        return json.dumps(payload, ensure_ascii=False)

    key, name = picked
    try:
        _apply_selection(key)
    except Exception as e:
        logger.error(f"Failed to apply camera selection {key}: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "reason": f"Could not save the camera selection: {e}"},
            ensure_ascii=False,
        )

    payload = _status_payload()
    payload["success"] = True
    payload["selected"] = name
    return json.dumps(payload, ensure_ascii=False)


def get_camera_facing_sync() -> str:
    """Return the current camera-facing status as a JSON string."""
    return json.dumps(_status_payload(), ensure_ascii=False)
