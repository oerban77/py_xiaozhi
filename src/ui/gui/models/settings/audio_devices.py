"""Audio device enumeration, selection, and testing."""

from __future__ import annotations

import asyncio
import time

import numpy as np
import sounddevice as sd
from PySide6.QtCore import Slot

from src.logging import get_logger

logger = get_logger()


class SettingsAudioDevicesMixin:
    # ========== Audio device settings ==========

    def _apply_device_lists(self, devices: dict) -> None:
        """Fill the input/output lists from the enumeration result and notify QML."""
        self._input_devices = list(devices.get("input") or [])
        self._output_devices = list(devices.get("output") or [])
        self._audio_devices_loaded = True
        logger.debug(
            f"Loaded {len(self._input_devices)} input device(s), "
            f"{len(self._output_devices)} output device(s)"
        )
        self.devicesChanged.emit()

    def _load_audio_devices(self, force: bool = False):
        """Load the list of available audio devices (plain enumeration, does not reinitialize PortAudio).

        Args:
            force: when True, force re-enumeration (when the settings are opened)
        """
        if self._audio_devices_loaded and not force:
            return
        try:
            from src.utils.audio_utils import list_audio_devices

            devices = list_audio_devices(include_virtual=True)
            self._apply_device_lists(devices)
        except Exception as e:
            logger.error(f"Failed to load audio devices: {e}", exc_info=True)
            self._input_devices = []
            self._output_devices = []

    @Slot(result=list)
    def getInputDevices(self) -> list:
        """Get the input device list (enumerated on first call)."""
        self._load_audio_devices()
        return [d["name"] for d in self._input_devices]

    @Slot(result=list)
    def getOutputDevices(self) -> list:
        """Get the output device list (enumerated on first call)."""
        self._load_audio_devices()
        return [d["name"] for d in self._output_devices]

    @Slot()
    def refreshDevices(self):
        """Hot-refresh the device list (stop the streams -> PortAudio re-enumeration -> restart the streams).

        When Bluetooth is connected while running, AudioPlugin must stop the streams first before calling ``refresh_portaudio_devices``.
        Falls back to local plain enumeration when there is no EventBus.
        """
        if getattr(self, "_audio_devices_refreshing", False):
            self.statusMessage.emit("Refreshing devices…")
            return

        event_bus = getattr(self, "_event_bus", None)
        task_manager = getattr(self, "_task_manager", None)

        if event_bus is None or task_manager is None:
            logger.warning("SettingsModel: no EventBus/TaskManager; falling back to local device enumeration")
            self._load_audio_devices(force=True)
            self.statusMessage.emit(
                "Device list refreshed (audio streams not coordinated; late-connected Bluetooth may still be invisible)"
            )
            return

        self._audio_devices_refreshing = True
        self.statusMessage.emit("Refreshing audio devices (briefly interrupts mic/playback)…")

        async def _refresh():
            from src.core.event_bus import Events
            from src.utils.audio_utils import list_audio_devices

            # The Future is the payload: AudioPlugin calls set_result(device list) in its handler
            # emit awaits all handlers, so the future is usually already done when it returns
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            await event_bus.emit(Events.AUDIO_DEVICES_REFRESH_REQUEST, fut)
            if fut.done():
                result = fut.result()
            else:
                logger.warning("AudioPlugin did not complete the device refresh Future; local enumeration fallback")
                result = list_audio_devices(include_virtual=True)
                if not fut.done():
                    fut.set_result(result)
            return result if isinstance(result, dict) else {"input": [], "output": []}

        def _on_task_done(task: asyncio.Task):
            def _apply():
                try:
                    if task.cancelled():
                        devices = {"input": [], "output": []}
                    else:
                        exc = task.exception()
                        if exc is not None:
                            logger.error(f"Device refresh task error: {exc}", exc_info=exc)
                            devices = {"input": [], "output": []}
                        else:
                            devices = task.result()
                except Exception as e:
                    logger.error(f"Failed to read refresh result: {e}", exc_info=True)
                    devices = {"input": [], "output": []}
                try:
                    if not isinstance(devices, dict):
                        devices = {"input": [], "output": []}
                    self._apply_device_lists(devices)
                    n_in = len(self._input_devices)
                    n_out = len(self._output_devices)
                    self.statusMessage.emit(
                        f"Device list refreshed (inputs {n_in} / outputs {n_out})"
                    )
                finally:
                    self._audio_devices_refreshing = False

            self._schedule_ui(_apply)

        try:
            task = task_manager.spawn(_refresh(), name="ui:audio_devices_refresh")
            if task is None:
                self._audio_devices_refreshing = False
                self._load_audio_devices(force=True)
                self.statusMessage.emit("Application is closing; enumerated locally")
                return
            task.add_done_callback(_on_task_done)
        except Exception as e:
            self._audio_devices_refreshing = False
            logger.error(f"Could not schedule device refresh: {e}", exc_info=True)
            self._load_audio_devices(force=True)
            self.statusMessage.emit(f"Device refresh failed, enumerated locally: {e}")

    def _schedule_ui(self, fn) -> None:
        """Push the callback back to the Qt main thread (the spawn done-callback may run on the loop thread)."""
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, fn)
        except Exception:
            try:
                fn()
            except Exception as e:
                logger.error(f"UI callback failed: {e}", exc_info=True)

    def _get_selectedInputIndex(self) -> int:
        """Get the index of the currently selected input device."""
        current_id = self._get_value("AUDIO_DEVICES.input_device_id", -1)
        current_name = self._get_value("AUDIO_DEVICES.input_device_name", "")

        # Prefer matching by device name
        if current_name:
            for i, d in enumerate(self._input_devices):
                if d["raw_name"] == current_name:
                    return i

        # Otherwise, match by device ID
        for i, d in enumerate(self._input_devices):
            if d["index"] == current_id:
                return i
        return 0

    def _set_selectedInputIndex(self, index: int):
        """Set the selected input device."""
        if 0 <= index < len(self._input_devices):
            device = self._input_devices[index]
            self._set_value("AUDIO_DEVICES.input_device_id", device["index"])
            self._set_value("AUDIO_DEVICES.input_device_name", device["raw_name"])
            self._set_value("AUDIO_DEVICES.input_sample_rate", device["sample_rate"])
            self._set_value("AUDIO_DEVICES.input_channels", min(device["channels"], 1))
            logger.info(f"Input device selected: {device['name']}")

    def _get_selectedOutputIndex(self) -> int:
        """Get the index of the currently selected output device."""
        current_id = self._get_value("AUDIO_DEVICES.output_device_id", -1)
        current_name = self._get_value("AUDIO_DEVICES.output_device_name", "")

        # Prefer matching by device name
        if current_name:
            for i, d in enumerate(self._output_devices):
                if d["raw_name"] == current_name:
                    return i

        # Otherwise, match by device ID
        for i, d in enumerate(self._output_devices):
            if d["index"] == current_id:
                return i
        return 0

    def _set_selectedOutputIndex(self, index: int):
        """Set the selected output device."""
        if 0 <= index < len(self._output_devices):
            device = self._output_devices[index]
            self._set_value("AUDIO_DEVICES.output_device_id", device["index"])
            self._set_value("AUDIO_DEVICES.output_device_name", device["raw_name"])
            self._set_value("AUDIO_DEVICES.output_sample_rate", device["sample_rate"])
            self._set_value("AUDIO_DEVICES.output_channels", min(device["channels"], 2))
            logger.info(f"Output device selected: {device['name']}")

    # Device information display
    def _get_inputDeviceInfo(self) -> str:
        idx = self._get_selectedInputIndex()
        if 0 <= idx < len(self._input_devices):
            d = self._input_devices[idx]
            return f"Sample rate: {d['sample_rate']}Hz, Channels: {d['channels']}"
        return "No device selected"

    def _get_outputDeviceInfo(self) -> str:
        idx = self._get_selectedOutputIndex()
        if 0 <= idx < len(self._output_devices):
            d = self._output_devices[idx]
            return f"Sample rate: {d['sample_rate']}Hz, Channels: {d['channels']}"
        return "No device selected"

    # Opus output sample rate
    def _get_opusOutputSampleRate(self) -> int:
        return self._get_value("AUDIO_DEVICES.opus_output_sample_rate", 24000)

    def _set_opusOutputSampleRate(self, value: int):
        self._set_value("AUDIO_DEVICES.opus_output_sample_rate", value)

    # Audio frame duration
    def _get_frameDuration(self) -> int:
        return self._get_value("AUDIO_DEVICES.frame_duration", 20)

    def _set_frameDuration(self, value: int):
        if value in [20, 40, 60]:
            self._set_value("AUDIO_DEVICES.frame_duration", value)

    # Audio testing
    @Slot()
    def testInputDevice(self):
        """Test the input device (recording)."""
        if self._testing_input:
            return

        idx = self._get_selectedInputIndex()
        if idx < 0 or idx >= len(self._input_devices):
            self.statusMessage.emit("Please select an input device first")
            return

        device = self._input_devices[idx]
        self._testing_input = True
        self.statusMessage.emit("Starting recording test...")

        self._run_worker(
            self._do_input_test,
            device,
            name="settings:input_test",
            test_kind="input",
            clear_flags=lambda: setattr(self, "_testing_input", False),
        )

    def _do_input_test(self, device: dict):
        """Run the recording test (exceptions are caught by _run_worker)."""
        device_id = device["index"]
        sample_rate = device["sample_rate"]
        duration = 3

        self.statusMessage.emit(f"Please speak into the microphone ({duration}s)...")
        time.sleep(1)

        recording = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=1,
            device=device_id,
            dtype=np.float32,
        )
        sd.wait()

        max_amplitude = np.max(np.abs(recording))

        if max_amplitude < 0.001:
            self.statusMessage.emit("[FAIL] No audio signal detected")
            self.testComplete.emit("input", False)
        elif max_amplitude > 0.8:
            self.statusMessage.emit("[WARN] Audio signal overloaded")
            self.testComplete.emit("input", True)
        else:
            self.statusMessage.emit(f"[OK] Recording test passed (volume: {max_amplitude:.1%})")
            self.testComplete.emit("input", True)

    @Slot()
    def testOutputDevice(self):
        """Test the output device (playback)."""
        if self._testing_output:
            return

        idx = self._get_selectedOutputIndex()
        if idx < 0 or idx >= len(self._output_devices):
            self.statusMessage.emit("Please select an output device first")
            return

        device = self._output_devices[idx]
        self._testing_output = True
        self.statusMessage.emit("Starting playback test...")

        self._run_worker(
            self._do_output_test,
            device,
            name="settings:output_test",
            test_kind="output",
            clear_flags=lambda: setattr(self, "_testing_output", False),
        )

    def _do_output_test(self, device: dict):
        """Run the playback test (exceptions are caught by _run_worker)."""
        device_id = device["index"]
        sample_rate = device["sample_rate"]
        duration = 2.0
        frequency = 440

        self.statusMessage.emit("Playing 440Hz test tone...")
        time.sleep(0.5)

        t = np.linspace(0, duration, int(sample_rate * duration))
        audio = 0.3 * np.sin(2 * np.pi * frequency * t)

        fade_samples = int(0.1 * sample_rate)
        audio[:fade_samples] *= np.linspace(0, 1, fade_samples)
        audio[-fade_samples:] *= np.linspace(1, 0, fade_samples)

        sd.play(audio, samplerate=sample_rate, device=device_id)
        sd.wait()

        self.statusMessage.emit("[OK] Playback test complete")
        self.testComplete.emit("output", True)
