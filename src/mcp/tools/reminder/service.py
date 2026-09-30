"""Reminder / alarm MCP tools.

Ported from the reference Xiaozhi desktop app (mcp/mcp_reminder.py):
- ``add_reminder_in``  — add a one-shot reminder from a relative duration
- ``add_reminder``     — add a reminder with a repeat mode
- ``list_reminders``   — list every stored reminder with its next trigger time
- ``delete_reminder``  — delete a reminder by id
- ``edit_reminder``    — edit the fields of an existing reminder
- ``toggle_reminder``  — enable / disable a reminder by id

Supported repeat modes: ``once``, ``daily``, ``hourly``, ``workday``,
``weekly``, ``monthly``, ``yearly``.

Reminders are stored as JSON in the user data directory (``reminders.json``).
When a reminder fires, the scheduler writes an alarm trigger file
(``alarm_trigger.json``) that the application reads to stop playback and ring
the alarm sound.
"""

from __future__ import annotations

import json
import re
import threading
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import sounddevice as sd

from src.logging import get_logger
from src.utils.resource_finder import get_app_root, get_user_data_dir

logger = get_logger()

# ── Paths / timing ────────────────────────────────────────────

_DATA_DIR = get_user_data_dir()
REMINDERS_FILE = _DATA_DIR / "reminders.json"
ALARM_FLAG_FILE = _DATA_DIR / "alarm_trigger.json"

CHECK_INTERVAL = 10  # seconds between scheduler checks
_TRIGGER_WINDOW_BACK = CHECK_INTERVAL + 5  # tolerate a missed tick

_MAX_DURATION_SEC = 86400 * 365  # one year

# ── Alarm sound state (mirrors xiaozhi-esp32) ─────────────────

# Tracks whether an alarm sound is currently playing.
# When True, incoming TTS messages are queued and played after alarm finishes.
_alarm_playing = threading.Event()
_alarm_playing.clear()

# Queue for TTS messages that arrive while alarm is playing.
# After alarm finishes, these are played in order.
_pending_tts_queue: list[dict] = []
_pending_tts_lock = threading.Lock()


def is_alarm_playing() -> bool:
    """Return True if an alarm sound is currently playing."""
    return _alarm_playing.is_set()


def enqueue_tts(tts_message: dict) -> None:
    """Queue a TTS message to be played after the alarm finishes."""
    with _pending_tts_lock:
        _pending_tts_queue.append(tts_message)


def get_pending_tts() -> list[dict]:
    """Get and clear all pending TTS messages."""
    with _pending_tts_lock:
        pending = list(_pending_tts_queue)
        _pending_tts_queue.clear()
        return pending

def _get_output_device_id() -> int | None:
    """Return the configured output device ID for the current app session."""
    try:
        from src.utils.config_manager import get_config

        cfg = get_config()
        output_device_id = cfg.get_config("AUDIO_DEVICES.output_device_id")
        if output_device_id is not None:
            return int(output_device_id)
    except Exception as e:
        logger.debug("Failed to resolve configured output device: %s", e)
    return None


# ── Alarm sound config (mirrors xiaozhi-esp32) ─────────────────

ALARM_REPEAT_COUNT = 8
ALARM_INTERVAL_S = 1.2  # seconds between alarm sound repeats

_SOUNDS_DIR = get_app_root() / "assets" / "sounds"


def _find_alarm_wav() -> Path | None:
    """Locate alarm.wav in locale sound folders, fallback to en-US."""
    for locale in ("id-ID", "en-US"):
        path = _SOUNDS_DIR / locale / "alarm.wav"
        if path.exists():
            return path
    return None


def _load_wav(path: Path) -> tuple[np.ndarray, int] | None:
    """Load a WAV as float32 mono and return (samples, sample_rate)."""
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        if sample_width == 2:
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sample_width == 4:
            audio = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif sample_width == 1:
            audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            logger.error("Unsupported WAV bit depth: %d bit (%s)", sample_width * 8, path)
            return None

        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        return audio, sample_rate
    except Exception as e:
        logger.error("Failed to load WAV %s: %s", path, e)
        return None


def _get_volume_controller():
    """Lazily get the platform volume controller (Windows/macOS/Linux)."""
    try:
        from src.mcp.tools.volume import create_volume_controller
        return create_volume_controller()
    except Exception as e:
        logger.warning("Could not create volume controller: %s", e)
        return None


def _play_alarm_sound(title: str) -> None:
    """Play alarm sound: 8x repeats at max volume, then restore.

    Mirrors the ESP32 behaviour:
      1. Save current volume
      2. Set volume to 100 (max)
      3. Play alarm.wav ALARM_REPEAT_COUNT times with ALARM_INTERVAL_S gap
      4. Restore original volume
      5. Log completion
    """
    alarm_path = _find_alarm_wav()
    if not alarm_path:
        logger.error("alarm.wav not found in assets/sounds/<locale>/")
        return

    loaded = _load_wav(alarm_path)
    if loaded is None:
        return
    audio, sample_rate = loaded

    device_id = _get_output_device_id()
    vc = _get_volume_controller()
    prev_volume = vc.get_volume() if vc else 70
    _alarm_playing.set()

    try:
        if vc:
            try:
                vc.set_volume(100)
            except Exception as e:
                logger.warning("Failed to set volume to 100: %s", e)

        logger.info("Alarm sound starting: %s (%d repeats)", title, ALARM_REPEAT_COUNT)

        for i in range(ALARM_REPEAT_COUNT):
            try:
                play_kwargs = {"samplerate": sample_rate}
                if device_id is not None:
                    play_kwargs["device"] = device_id
                sd.play(audio, **play_kwargs)
                sd.wait()
            except Exception as e:
                logger.warning("Alarm sound play failed (attempt %d): %s", i + 1, e)
            if i < ALARM_REPEAT_COUNT - 1:
                sd.sleep(int(ALARM_INTERVAL_S * 1000))

        # Explicitly stop + reset to release PortAudio resources
        # so the app's audio pipeline can continue without interference
        try:
            sd.stop()
        except Exception:
            pass
        try:
            sd.reset()
        except Exception:
            pass

        logger.info("Alarm sound finished: %s", title)
    finally:
        _alarm_playing.clear()
        if vc:
            try:
                vc.set_volume(prev_volume)
                logger.debug("Volume restored to %d", prev_volume)
            except Exception as e:
                logger.warning("Failed to restore volume: %s", e)

# ── Repeat modes ──────────────────────────────────────────────

MODE_ONCE = "once"
MODE_DAILY = "daily"
MODE_HOURLY = "hourly"
MODE_WORKDAY = "workday"
MODE_WEEKLY = "weekly"
MODE_MONTHLY = "monthly"
MODE_YEARLY = "yearly"

VALID_MODES = [
    MODE_ONCE,
    MODE_DAILY,
    MODE_HOURLY,
    MODE_WORKDAY,
    MODE_WEEKLY,
    MODE_MONTHLY,
    MODE_YEARLY,
]

WEEKDAY_NAMES = {
    "senin": 0,
    "selasa": 1,
    "rabu": 2,
    "kamis": 3,
    "jumat": 4,
    "sabtu": 5,
    "minggu": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Fields that may be edited on a reminder.
_EDITABLE_FIELDS = (
    "title",
    "message",
    "mode",
    "datetime",
    "time",
    "interval_hours",
    "minute",
    "weekday",
    "day",
    "month",
    "enabled",
)

# ── Storage ───────────────────────────────────────────────────

_file_lock = threading.Lock()


def _load_reminders() -> list[dict[str, Any]]:
    if not REMINDERS_FILE.exists():
        return []
    try:
        with REMINDERS_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("Failed to read %s: %s", REMINDERS_FILE, e)
        return []


def _save_reminders(reminders: list[dict[str, Any]]) -> None:
    try:
        REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = REMINDERS_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(reminders, f, indent=2, ensure_ascii=False)
        tmp.replace(REMINDERS_FILE)
    except Exception as e:
        logger.error("Failed to write %s: %s", REMINDERS_FILE, e)


def _next_id(reminders: list[dict[str, Any]]) -> int:
    if not reminders:
        return 1
    return max(int(r.get("id", 0)) for r in reminders) + 1


# ── Time helpers ──────────────────────────────────────────────


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse ``HH:MM`` into (hour, minute)."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", (time_str or "").strip())
    if not m:
        raise ValueError(f"Invalid time format: '{time_str}' (use HH:MM)")
    h, mn = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mn <= 59):
        raise ValueError(f"Invalid time of day: {h}:{mn:02d}")
    return h, mn


def _next_trigger(
    reminder: dict[str, Any], now: Optional[datetime] = None
) -> Optional[datetime]:
    """Compute the next trigger time for a reminder (None if expired)."""
    if now is None:
        now = datetime.now()

    mode = reminder.get("mode", MODE_ONCE)

    if mode == MODE_ONCE:
        try:
            dt = datetime.fromisoformat(str(reminder.get("datetime", "")))
        except ValueError:
            return None
        return dt if dt > now else None

    if mode == MODE_DAILY:
        h, mn = _parse_time(str(reminder.get("time", "00:00")))
        candidate = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    if mode == MODE_HOURLY:
        interval = int(reminder.get("interval_hours", 1) or 1)
        if interval < 1:
            interval = 1
        mn = int(reminder.get("minute", 0) or 0)
        candidate = now.replace(minute=mn, second=0, microsecond=0)
        while candidate <= now:
            candidate += timedelta(hours=interval)
        return candidate

    if mode == MODE_WORKDAY:
        h, mn = _parse_time(str(reminder.get("time", "00:00")))
        candidate = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:  # skip Sat/Sun
            candidate += timedelta(days=1)
        return candidate

    if mode == MODE_WEEKLY:
        h, mn = _parse_time(str(reminder.get("time", "00:00")))
        weekday = WEEKDAY_NAMES.get(
            str(reminder.get("weekday", "senin")).lower(), 0
        )
        days_ahead = (weekday - now.weekday()) % 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=h, minute=mn, second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(weeks=1)
        return candidate

    if mode == MODE_MONTHLY:
        h, mn = _parse_time(str(reminder.get("time", "00:00")))
        day = int(reminder.get("day", 1) or 1)
        try:
            candidate = now.replace(
                day=min(day, 28), hour=h, minute=mn, second=0, microsecond=0
            )
        except ValueError:
            candidate = now.replace(
                day=28, hour=h, minute=mn, second=0, microsecond=0
            )
        if candidate <= now:
            month = now.month % 12 + 1
            year = now.year + (1 if now.month == 12 else 0)
            candidate = candidate.replace(year=year, month=month)
        return candidate

    if mode == MODE_YEARLY:
        h, mn = _parse_time(str(reminder.get("time", "00:00")))
        day = int(reminder.get("day", 1) or 1)
        month = int(reminder.get("month", 1) or 1)
        try:
            candidate = now.replace(
                month=month,
                day=min(day, 28),
                hour=h,
                minute=mn,
                second=0,
                microsecond=0,
            )
        except ValueError:
            candidate = now.replace(
                month=month,
                day=28,
                hour=h,
                minute=mn,
                second=0,
                microsecond=0,
            )
        if candidate <= now:
            candidate = candidate.replace(year=now.year + 1)
        return candidate

    return None


# ── Alarm trigger ─────────────────────────────────────────────


def _trigger_alarm(reminder: dict[str, Any]) -> None:
    """Write the alarm flag file and play the alarm sound in a background thread.

    Mirrors xiaozhi-esp32 behaviour:
      1. Write alarm_trigger.json (kept for backwards compatibility)
      2. Spawn a background thread that plays alarm.wav 8x at max volume
    """
    payload = {
        "id": reminder.get("id"),
        "title": reminder.get("title", "Reminder"),
        "message": reminder.get("message", ""),
        "mode": reminder.get("mode", MODE_ONCE),
        "time": datetime.now().strftime("%H:%M"),
    }
    try:
        ALARM_FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ALARM_FLAG_FILE.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.error("Failed to write alarm flag: %s", e)

    # Play alarm sound in background thread (non-blocking)
    title = reminder.get("title", "Reminder")
    thread = threading.Thread(
        target=_play_alarm_sound,
        args=(title,),
        daemon=True,
        name=f"AlarmSound-{title}",
    )
    thread.start()
    logger.info("Alarm triggered: %s", title)


def _cleanup_expired(reminders: list[dict[str, Any]]) -> None:
    """Drop fired/disabled one-shot reminders."""
    now = datetime.now()
    before = len(reminders)
    keep: list[dict[str, Any]] = []

    for r in reminders:
        if r.get("mode") != MODE_ONCE:
            keep.append(r)
            continue
        try:
            dt = datetime.fromisoformat(str(r.get("datetime", "")))
            expired = dt <= now
            triggered = bool(r.get("last_triggered"))
            disabled = not r.get("enabled", True)
            if expired and (triggered or disabled):
                logger.info("Auto-delete once reminder: [%s] %s", r.get("id"), r.get("title"))
                continue
        except Exception:
            pass
        keep.append(r)

    if len(keep) < before:
        reminders[:] = keep
        _save_reminders(reminders)


# ── Scheduler thread ──────────────────────────────────────────

_scheduler_thread: Optional[threading.Thread] = None
_scheduler_stop = threading.Event()
_scheduler_lock = threading.Lock()


def _scheduler_loop() -> None:
    logger.info("Reminder scheduler started")
    while not _scheduler_stop.is_set():
        try:
            now = datetime.now()
            reminders = _load_reminders()
            changed = False

            for r in reminders:
                if not r.get("enabled", True):
                    continue

                next_t = _next_trigger(r, now)

                if next_t is None and r.get("mode") == MODE_ONCE:
                    # Already past, but maybe only just — still fire once.
                    try:
                        dt_target = datetime.fromisoformat(str(r.get("datetime", "")))
                        diff = (now - dt_target).total_seconds()
                        if 0 <= diff <= _TRIGGER_WINDOW_BACK and not r.get(
                            "last_triggered"
                        ):
                            next_t = dt_target
                        else:
                            continue
                    except Exception:
                        continue
                elif next_t is None:
                    continue

                diff = (next_t - now).total_seconds()
                if -_TRIGGER_WINDOW_BACK <= diff <= CHECK_INTERVAL:
                    logger.info("Triggering reminder: %s", r.get("title"))
                    _trigger_alarm(r)
                    if r.get("mode") == MODE_ONCE:
                        r["enabled"] = False
                    r["last_triggered"] = now.isoformat()
                    changed = True

            if changed:
                _save_reminders(reminders)

            _cleanup_expired(reminders)
        except Exception as e:
            logger.error("Reminder scheduler error: %s", e)

        _scheduler_stop.wait(timeout=CHECK_INTERVAL)

    logger.info("Reminder scheduler stopped")


def start_scheduler() -> None:
    """Start the background reminder scheduler (idempotent)."""
    global _scheduler_thread
    with _scheduler_lock:
        if _scheduler_thread and _scheduler_thread.is_alive():
            return
        _scheduler_stop.clear()
        _scheduler_thread = threading.Thread(
            target=_scheduler_loop, daemon=True, name="reminder-scheduler"
        )
        _scheduler_thread.start()


def stop_scheduler() -> None:
    """Stop the background reminder scheduler (mainly for tests)."""
    global _scheduler_thread
    with _scheduler_lock:
        if not _scheduler_thread:
            return
        _scheduler_stop.set()
        _scheduler_thread.join(timeout=CHECK_INTERVAL + 2)
        _scheduler_thread = None


# Start automatically on import, mirroring the reference implementation.
start_scheduler()


# ── Formatting ────────────────────────────────────────────────


def _format_reminder(r: dict[str, Any]) -> str:
    mode = r.get("mode", MODE_ONCE)
    title = r.get("title", "?")
    enabled = "ON" if r.get("enabled", True) else "OFF"
    rid = r.get("id", "?")

    if mode == MODE_ONCE:
        sch = f"Once at {r.get('datetime', '?')}"
    elif mode == MODE_DAILY:
        sch = f"Daily at {r.get('time', '?')}"
    elif mode == MODE_HOURLY:
        sch = f"Every {r.get('interval_hours', 1)}h at :{int(r.get('minute', 0)):02d}"
    elif mode == MODE_WORKDAY:
        sch = f"Workdays at {r.get('time', '?')}"
    elif mode == MODE_WEEKLY:
        sch = f"Every {r.get('weekday', '?')} at {r.get('time', '?')}"
    elif mode == MODE_MONTHLY:
        sch = f"Monthly on day {r.get('day', '?')} at {r.get('time', '?')}"
    elif mode == MODE_YEARLY:
        sch = (
            f"Yearly on {r.get('day', '?')}/{r.get('month', '?')} "
            f"at {r.get('time', '?')}"
        )
    else:
        sch = str(mode)

    next_t = _next_trigger(r)
    next_s = next_t.strftime("%d/%m %H:%M") if next_t else "-"
    return f"[{rid}] {enabled} {title}\n    Schedule: {sch}\n    Next: {next_s}"


def _format_duration(total_seconds: float) -> str:
    if total_seconds < 60:
        return f"{int(total_seconds)} second(s)"
    if total_seconds < 3600:
        m = int(total_seconds // 60)
        s = int(total_seconds % 60)
        return f"{m} minute(s)" + (f" {s} second(s)" if s else "")
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    return f"{h} hour(s)" + (f" {m} minute(s)" if m else "")


# ── MCP tool handlers ─────────────────────────────────────────


async def add_reminder_in(args: dict[str, Any]) -> str:
    """Add a one-shot reminder from a relative duration."""
    title = str(args.get("title", "") or "").strip()
    message = str(args.get("message", "") or title).strip()
    seconds = float(args.get("seconds", 0) or 0)
    minutes = float(args.get("minutes", 0) or 0)
    hours = float(args.get("hours", 0) or 0)

    total_seconds = seconds + minutes * 60 + hours * 3600
    if total_seconds <= 0:
        return "Duration must be greater than 0. Provide seconds, minutes or hours."
    if total_seconds > _MAX_DURATION_SEC:
        return "Duration is too long (maximum is one year)."

    target_dt = datetime.now() + timedelta(seconds=total_seconds)

    if not title:
        title = f"Reminder in {_format_duration(total_seconds)}"

    with _file_lock:
        reminders = _load_reminders()
        r: dict[str, Any] = {
            "id": _next_id(reminders),
            "title": title,
            "message": message or title,
            "mode": MODE_ONCE,
            "datetime": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "enabled": True,
            "created": datetime.now().isoformat(),
        }
        reminders.append(r)
        _save_reminders(reminders)

    return (
        "Reminder added!\n"
        f"  ID    : {r['id']}\n"
        f"  Title : {title}\n"
        f"  Time  : {target_dt.strftime('%H:%M:%S')} "
        f"(in {_format_duration(total_seconds)})"
    )


async def add_reminder(args: dict[str, Any]) -> str:
    """Add a reminder with a repeat mode."""
    mode = str(args.get("mode", MODE_ONCE) or MODE_ONCE)
    title = str(args.get("title", "") or "").strip()
    if not title:
        return "title is required"
    if mode not in VALID_MODES:
        return f"Invalid mode. Use one of: {', '.join(VALID_MODES)}"

    if mode == MODE_ONCE and not args.get("datetime"):
        return "mode 'once' requires 'datetime' (format: '2026-05-15 07:00')"

    if mode in (
        MODE_DAILY,
        MODE_WORKDAY,
        MODE_WEEKLY,
        MODE_MONTHLY,
        MODE_YEARLY,
    ):
        if not args.get("time"):
            return f"mode '{mode}' requires 'time' (format: HH:MM)"
        try:
            _parse_time(str(args["time"]))
        except ValueError as e:
            return f"{e}"

    if mode == MODE_WEEKLY and not args.get("weekday"):
        return "mode 'weekly' requires 'weekday' (monday-sunday)"

    with _file_lock:
        reminders = _load_reminders()
        r: dict[str, Any] = {
            "id": _next_id(reminders),
            "title": title,
            "message": str(args.get("message", "") or title),
            "mode": mode,
            "enabled": True,
            "created": datetime.now().isoformat(),
        }
        for field in (
            "datetime",
            "time",
            "interval_hours",
            "minute",
            "weekday",
            "day",
            "month",
        ):
            if args.get(field) is not None:
                r[field] = args[field]

        if mode == MODE_HOURLY:
            r.setdefault("interval_hours", 1)
            r.setdefault("minute", 0)

        next_t = _next_trigger(r)
        if next_t is None and mode == MODE_ONCE:
            return "That time has already passed. Use a future time."

        reminders.append(r)
        _save_reminders(reminders)

    next_s = next_t.strftime("%d %b %Y %H:%M") if next_t else "-"
    return (
        "Reminder added!\n"
        f"  ID    : {r['id']}\n"
        f"  Title : {title}\n"
        f"  Mode  : {mode}\n"
        f"  Next  : {next_s}"
    )


async def list_reminders(args: dict[str, Any]) -> str:
    """List every stored reminder with its next trigger time."""
    reminders = _load_reminders()
    if not reminders:
        return "No reminders yet. Use add_reminder to create one."
    lines = [f"Reminders ({len(reminders)})", "=" * 45]
    for r in reminders:
        lines.append(_format_reminder(r))
        lines.append("")
    return "\n".join(lines)


async def delete_reminder(args: dict[str, Any]) -> str:
    """Delete a reminder by id."""
    try:
        rid = int(args.get("id", 0))
    except (TypeError, ValueError):
        return "id must be an integer"

    with _file_lock:
        reminders = _load_reminders()
        before = len(reminders)
        reminders = [r for r in reminders if int(r.get("id", 0)) != rid]
        if len(reminders) == before:
            return f"Reminder id {rid} not found."
        _save_reminders(reminders)
    return f"Reminder id {rid} deleted."


async def edit_reminder(args: dict[str, Any]) -> str:
    """Edit the fields of an existing reminder (only given fields change)."""
    try:
        rid = int(args.get("id", 0))
    except (TypeError, ValueError):
        return "id must be an integer"

    with _file_lock:
        reminders = _load_reminders()
        for r in reminders:
            if int(r.get("id", 0)) != rid:
                continue
            for field in _EDITABLE_FIELDS:
                if args.get(field) is not None:
                    r[field] = args[field]
            r["updated"] = datetime.now().isoformat()

            if r.get("time"):
                try:
                    _parse_time(str(r["time"]))
                except ValueError as e:
                    return f"{e}"

            next_t = _next_trigger(r)
            _save_reminders(reminders)
            next_s = next_t.strftime("%d %b %Y %H:%M") if next_t else "-"
            return (
                f"Reminder id {rid} updated.\n"
                f"  Next: {next_s}\n"
                f"{_format_reminder(r)}"
            )
    return f"Reminder id {rid} not found."


async def toggle_reminder(args: dict[str, Any]) -> str:
    """Enable or disable a reminder by id."""
    try:
        rid = int(args.get("id", 0))
    except (TypeError, ValueError):
        return "id must be an integer"
    enabled = bool(args.get("enabled", True))

    with _file_lock:
        reminders = _load_reminders()
        for r in reminders:
            if int(r.get("id", 0)) != rid:
                continue
            r["enabled"] = enabled
            _save_reminders(reminders)
            status = "enabled" if enabled else "disabled"
            return f"Reminder '{r.get('title')}' {status}."
    return f"Reminder id {rid} not found."
