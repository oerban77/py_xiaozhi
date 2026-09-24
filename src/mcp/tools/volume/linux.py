"""Linux volume backend (pactl / wpctl / amixer)."""

from __future__ import annotations

import re
import shutil
import subprocess

from src.logging import get_logger

logger = get_logger()

DEFAULT_VOLUME = 70


def _run_command(
    cmd: list[str], *, check: bool = False
) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=check)
    except Exception as e:
        logger.debug(f"Command execution failed {' '.join(cmd)}: {e}")
        return None


class LinuxVolumeBackend:
    def __init__(self) -> None:
        self.linux_tool: str | None = None
        self._init()

    def _init(self) -> None:
        for tool in ("pactl", "wpctl", "amixer"):
            if shutil.which(tool):
                self.linux_tool = tool
                break

        if not self.linux_tool:
            logger.error("No usable Linux volume control tool found (pactl/wpctl/amixer)")
            raise Exception("No usable Linux volume control tool found")

        logger.debug(f"Linux volume control initialized, using: {self.linux_tool}")

    def get_volume(self) -> int:
        tool = self.linux_tool
        if tool == "pactl":
            return self._get_pactl()
        if tool == "wpctl":
            return self._get_wpctl()
        if tool == "amixer":
            return self._get_amixer()
        return DEFAULT_VOLUME

    def set_volume(self, volume: int) -> None:
        tool = self.linux_tool
        if tool == "pactl":
            self._set_pactl(volume)
        elif tool == "wpctl":
            self._set_wpctl(volume)
        elif tool == "amixer":
            self._set_amixer(volume)

    def _get_pactl(self) -> int:
        try:
            result = _run_command(["pactl", "list", "sinks"])
            if result and result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "Volume:" in line:
                        match = re.search(r"(\d+)%", line)
                        if match:
                            volume = int(match.group(1))
                            logger.debug(f"pactlgot volume: {volume}%")
                            return volume
                logger.warning("pactlNo volume info found in output")
            else:
                logger.warning(
                    f"pactl command execution failed: {result.returncode if result else 'None'}"
                )
        except Exception as e:
            logger.warning(f"Failed to get volume via pactl: {e}", exc_info=True)
        return DEFAULT_VOLUME

    def _set_pactl(self, volume: int) -> None:
        try:
            result = _run_command(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"]
            )
            if result and result.returncode == 0:
                logger.debug(f"pactl set volume succeeded: {volume}%")
            else:
                logger.warning(
                    f"pactl failed to set volume: {result.returncode if result else 'None'}"
                )
        except Exception as e:
            logger.warning(f"Failed to set volume via pactl: {e}", exc_info=True)

    def _get_wpctl(self) -> int:
        try:
            result = _run_command(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if result and result.returncode == 0:
                match = re.search(r"(\d+\.?\d*)", result.stdout)
                if match:
                    volume = int(float(match.group(1)) * 100)
                    logger.debug(f"wpctlgot volume: {volume}%")
                    return volume
                logger.warning(f"wpctlCould not parse output format: {result.stdout}")
            else:
                logger.warning(
                    f"wpctl command execution failed: {result.returncode if result else 'None'}"
                )
        except Exception as e:
            logger.warning(f"Failed to get volume via wpctl: {e}", exc_info=True)
        return DEFAULT_VOLUME

    def _set_wpctl(self, volume: int) -> None:
        try:
            result = _run_command(
                [
                    "wpctl",
                    "set-volume",
                    "@DEFAULT_AUDIO_SINK@",
                    f"{volume / 100.0:.2f}",
                ]
            )
            if result and result.returncode == 0:
                logger.debug(f"wpctl set volume succeeded: {volume}%")
            else:
                logger.warning(
                    f"wpctl failed to set volume: {result.returncode if result else 'None'}"
                )
        except Exception as e:
            logger.warning(f"Failed to set volume via wpctl: {e}", exc_info=True)

    def _get_amixer(self) -> int:
        try:
            result = _run_command(["amixer", "get", "Master"])
            if result and result.returncode == 0:
                match = re.search(r"\[(\d+)%\]", result.stdout)
                if match:
                    volume = int(match.group(1))
                    logger.debug(f"amixergot volume: {volume}%")
                    return volume
                logger.warning(f"amixerCould not parse output format: {result.stdout}")
            else:
                logger.warning(
                    f"amixer command execution failed: {result.returncode if result else 'None'}"
                )
        except Exception as e:
            logger.warning(f"Failed to get volume via amixer: {e}", exc_info=True)
        return DEFAULT_VOLUME

    def _set_amixer(self, volume: int) -> None:
        try:
            result = _run_command(["amixer", "sset", "Master", f"{volume}%"])
            if result and result.returncode == 0:
                logger.debug(f"amixer set volume succeeded: {volume}%")
            else:
                logger.warning(
                    f"amixer failed to set volume: {result.returncode if result else 'None'}"
                )
        except Exception as e:
            logger.warning(f"Failed to set volume via amixer: {e}", exc_info=True)
