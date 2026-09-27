"""Hardware / system command MCP tools.

Ported from the reference Xiaozhi desktop app (mcp/mcp_hardware.py):
- ``run_command``       — run any OS shell command
- ``hardware_command``  — alias for run_command
- ``list_commands``     — show the command shortcuts registered in config

How it works:
1. If the command matches a shortcut in config ``HARDWARE.COMMANDS``, the
   platform template (``windows``/``linux``) is used.
2. Otherwise the command is executed directly as a shell command.
3. Destructive commands are blocked by a regex blacklist.
4. Platform-specific adjustments (``ls`` → ``dir``, ``ping -c`` → ``ping -n`` …).

Config (config.json):

    "HARDWARE": {
        "ENABLED": true,
        "DEFAULT_CWD": "D:\\project",
        "COMMANDS": {
            "git_status": {
                "description": "Show git status",
                "windows": "git -C D:\\project status",
                "linux": "cd /home/user/project && git status"
            }
        }
    }
"""

from __future__ import annotations

import asyncio
import os
import platform
import re
import subprocess
import threading
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()

_DEFAULT_TIMEOUT = 60
_MAX_OUTPUT = 4000

# Commands that must never run.
BLACKLIST_PATTERNS = [
    r"\bformat\b.*[a-zA-Z]:",  # format C:
    r"\bdel\b.*[/\\]\s*\*",  # del /s *
    r"\bdel\b.*[/\\][sq]\b.*\*",  # del /s * / del /q *
    r"\brmdir\b.*[/\\]s",  # rmdir /s
    r"\brm\b.*-rf\b",  # rm -rf <anything>
    r"\brm\b.*-rf\b.*/",  # rm -rf /
    r"\brm\b.*-rf\b.*\*",  # rm -rf *
    r"\bmkfs\b",  # mkfs
    r"\bdd\b.*if=.*of=",  # dd if= of=
    r"\breg\b.*delete\b",  # reg delete
    r"\breg\b.*add\b.*\/f",  # reg add /f
    r"netsh\b.*firewall.*disable",
    r"ufw\b.*disable",
    r"\bnet\b.*user\b.*\/add",
    r"\buseradd\b",
    r"\bpasswd\b",
]


def is_command_safe(cmd: str) -> bool:
    """Return True when the command is not on the destructive blacklist."""
    cmd_lower = cmd.lower().strip()
    for pattern in BLACKLIST_PATTERNS:
        if re.search(pattern, cmd_lower):
            return False
    return True


class HardwareManager:
    """Runs shell commands, either from config shortcuts or generically."""

    def __init__(self) -> None:
        try:
            cfg = get_config()
            hw = cfg.get_config("HARDWARE", {}) or {}
        except Exception as e:  # ConfigManager not initialized yet
            logger.warning(f"Hardware: config unavailable: {e}")
            hw = {}

        if not isinstance(hw, dict):
            hw = {}

        self.commands = hw.get("COMMANDS", {}) or {}
        if not isinstance(self.commands, dict):
            self.commands = {}

        self.default_cwd = (
            hw.get("DEFAULT_CWD", "")
            or hw.get("DEFAULT_REPO", "")
            or os.getcwd()
        )
        self.platform = platform.system().lower()
        logger.info(
            f"Hardware: platform='{self.platform}' "
            f"cwd='{self.default_cwd}' shortcuts={len(self.commands)}"
        )

    # ── Sanitize Args ────────────────────────────────────────

    def _sanitize_args(self, command_name: str, args: str) -> str:
        """Drop flags that do not belong to the current platform."""
        if not args:
            return args

        original = args
        name_lower = (command_name or "").lower()

        if self.platform.startswith("win"):
            linux_flags = {
                "ping": ["-c", "-W", "-i", "-s"],
                "traceroute": ["-m", "-w", "-q"],
                "ls": ["-la", "-l", "-a", "-h", "--color", "--all"],
                "netstat": ["-tuln", "-tulnp"],
            }
            flags = linux_flags.get(name_lower, [])
            if flags:
                parts = args.split()
                cleaned = []
                skip_next = False
                for i, part in enumerate(parts):
                    if skip_next:
                        skip_next = False
                        continue
                    if part in flags:
                        if i + 1 < len(parts) and parts[i + 1].isdigit():
                            skip_next = True
                        continue
                    cleaned.append(part)
                args = " ".join(cleaned)
        else:
            win_flags = {
                "ping": ["-n", "/n", "-t", "/t", "-l", "/l"],
                "traceroute": ["-d", "/d", "-h", "/h"],
                "netstat": ["-an", "/an"],
            }
            flags = [f.lower() for f in win_flags.get(name_lower, [])]
            if flags:
                parts = args.split()
                cleaned = []
                skip_next = False
                for i, part in enumerate(parts):
                    if skip_next:
                        skip_next = False
                        continue
                    if part.lower() in flags:
                        if i + 1 < len(parts) and parts[i + 1].isdigit():
                            skip_next = True
                        continue
                    cleaned.append(part)
                args = " ".join(cleaned)

        if args != original:
            logger.info(f"Hardware: sanitized '{original}' -> '{args}'")
        return args.strip()

    # ── Extract Args ─────────────────────────────────────────

    def _extract_args_from_raw(self, raw_name: str, matched_key: str) -> str:
        """Pull the trailing arguments out of a raw command string."""
        raw = (raw_name or "").strip()
        key = (matched_key or "").strip()
        if not raw or not key:
            return ""

        key_words = key.replace("-", " ").replace("_", " ").split()
        raw_words = raw.split()

        if len(raw_words) >= len(key_words):
            raw_prefix = [w.lower() for w in raw_words[: len(key_words)]]
            key_prefix = [w.lower() for w in key_words]
            if raw_prefix == key_prefix:
                result = " ".join(raw_words[len(key_words) :]).strip()
                if result:
                    return result

        key_variants = [
            " ".join(key_words).lower(),
            key.lower().replace("_", " "),
            key.lower(),
        ]
        raw_lower = raw.lower()
        for variant in key_variants:
            if raw_lower.startswith(variant):
                result = raw[len(variant) :].strip()
                if result:
                    return result

        for word in raw_words:
            if os.sep in word or "/" in word or "\\" in word or ":" in word:
                return word

        return ""

    # ── Detect CWD ───────────────────────────────────────────

    def _detect_cwd(self, cmd: str, args: str) -> str:
        """Try to infer the working directory from path-like tokens."""
        for candidate in [args, cmd]:
            if not candidate:
                continue
            for token in candidate.split():
                token_clean = token.strip('"').strip("'")
                if os.path.isdir(token_clean):
                    return token_clean
                parent = os.path.dirname(token_clean)
                if parent and os.path.isdir(parent):
                    return parent
        return self.default_cwd

    # ── Find Shortcut ────────────────────────────────────────

    def _find_shortcut(self, raw_name: str) -> tuple[dict | None, str | None]:
        """Look the command up in the config shortcuts."""
        if not raw_name:
            return None, None

        if raw_name in self.commands:
            return self.commands[raw_name], raw_name

        if " " in raw_name:
            words = raw_name.split()
            for n in range(min(3, len(words)), 0, -1):
                prefix_u = "_".join(words[:n])
                prefix_s = " ".join(words[:n])
                for prefix in [prefix_u, prefix_s]:
                    if prefix in self.commands:
                        return self.commands[prefix], prefix
                for k, v in self.commands.items():
                    if k.lower() in (prefix_u.lower(), prefix_s.lower()):
                        return v, k

        norm = raw_name.replace(" ", "_").replace("-", "_")
        if norm in self.commands:
            return self.commands[norm], norm

        for k, v in self.commands.items():
            if k.lower() == raw_name.lower() or k.lower() == norm.lower():
                return v, k

        query_tokens = set(re.sub(r"[^0-9a-z]+", " ", raw_name.lower()).split())
        if query_tokens:
            for k, v in self.commands.items():
                key_tokens = set(re.sub(r"[^0-9a-z]+", " ", k.lower()).split())
                if key_tokens and (
                    query_tokens == key_tokens or key_tokens.issubset(query_tokens)
                ):
                    return v, k

        return None, None

    # ── Platform Adjust ──────────────────────────────────────

    def _platform_adjust(self, cmd: str) -> str:
        """Translate a command so it works on the current platform."""
        cmd_lower = cmd.lower().strip()

        if self.platform.startswith("win"):
            if cmd_lower.startswith("ping "):
                cmd = re.sub(r"\s-c\s+(\d+)", r" -n \1", cmd)
                if "-n " not in cmd.lower() and "-t" not in cmd.lower():
                    parts = cmd.split(None, 1)
                    if len(parts) == 2:
                        cmd = f"ping -n 4 {parts[1]}"
            if cmd_lower.startswith("ls"):
                cmd = "dir" + cmd[2:]
            if cmd_lower.startswith("traceroute "):
                cmd = "tracert" + cmd[10:]
        else:
            if cmd_lower.startswith("dir"):
                cmd = "ls -la" + cmd[3:]
            if cmd_lower.startswith("tracert "):
                cmd = "traceroute" + cmd[7:]
            if cmd_lower.startswith("ping "):
                cmd = re.sub(r"\s-n\s+(\d+)", r" -c \1", cmd)
                if "-c " not in cmd.lower():
                    parts = cmd.split(None, 1)
                    if len(parts) == 2:
                        cmd = f"ping -c 4 {parts[1]}"

        return cmd

    # ── List Commands ────────────────────────────────────────

    def list_commands(self) -> str:
        if not self.commands:
            return (
                "No command shortcuts are registered in config.\n"
                "Any OS command can still be run directly with run_command."
            )
        lines = [f"Registered command shortcuts ({len(self.commands)}):", "=" * 40]
        for name, item in self.commands.items():
            desc = item.get("description", "") if isinstance(item, dict) else ""
            lines.append(f"- {name}: {desc}")
        lines.append(
            "\nAny other OS command can be run directly with run_command."
        )
        return "\n".join(lines)

    # ── Run Command ──────────────────────────────────────────

    def run_command(self, name: str, args: str = "", timeout: int = _DEFAULT_TIMEOUT) -> str:
        raw_name = (name or "").strip()
        args = (args or "").strip()

        # STEP 1: config shortcut
        entry, matched_key = self._find_shortcut(raw_name)
        if entry and matched_key:
            if not args:
                extracted = self._extract_args_from_raw(raw_name, matched_key)
                if extracted:
                    args = extracted

            if self.platform.startswith("win"):
                cmd = entry.get("windows") or entry.get("win") or entry.get("cmd")
            else:
                cmd = entry.get("linux") or entry.get("bash") or entry.get("sh")

            if cmd:
                if args:
                    args = self._sanitize_args(matched_key, args)
                if "{args}" in cmd:
                    cmd = cmd.replace("{args}", args or "")
                elif args:
                    cmd = f"{cmd} {args}"
                cmd = " ".join(cmd.split())
                logger.info(f"Hardware: shortcut '{matched_key}' -> {cmd}")
                return self._execute(cmd, timeout)

        # STEP 2: generic shell command
        full_cmd = f"{raw_name} {args}".strip() if args else raw_name.strip()
        if not full_cmd:
            return "Error: empty command"

        full_cmd = self._platform_adjust(full_cmd)
        logger.info(f"Hardware: generic command: {full_cmd}")
        return self._execute(full_cmd, timeout)

    # ── Execute ──────────────────────────────────────────────

    def _execute(self, cmd: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
        if not is_command_safe(cmd):
            logger.warning(f"Hardware: blocked dangerous command: {cmd}")
            return (
                "Blocked: the command is potentially dangerous and is not allowed:\n"
                f"  {cmd}"
            )

        cwd = self._detect_cwd(cmd, "")
        if not os.path.isdir(cwd):
            cwd = os.getcwd()

        logger.info(f"Hardware: execute: {cmd} (cwd={cwd})")
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout} seconds"
        except FileNotFoundError:
            return (
                f"Error: command not found: {cmd.split()[0]}\n"
                "Make sure the program is installed and on PATH."
            )
        except Exception as e:
            return f"Error: {e}"

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if proc.returncode != 0:
            return self._handle_error(proc, stdout, stderr, cmd, timeout)

        if stdout:
            if len(stdout) > _MAX_OUTPUT:
                stdout = (
                    stdout[:2000]
                    + f"\n\n... ({len(stdout)} chars total, truncated) ...\n\n"
                    + stdout[-1000:]
                )
            return stdout
        return "Command finished (no output)"

    # ── Handle Error ─────────────────────────────────────────

    def _handle_error(
        self,
        proc: subprocess.CompletedProcess,
        stdout: str,
        stderr: str,
        cmd: str,
        timeout: int,
    ) -> str:
        lower_err = (stderr or "").lower()
        cmd_lower = cmd.lower()

        # Windows ping retry (wrong flags)
        if (
            self.platform.startswith("win")
            and "ping" in cmd_lower
            and any(
                p in lower_err
                for p in ["invalid option", "bad option", "invalid argument"]
            )
        ):
            ip_match = re.search(
                r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
                cmd,
            )
            if ip_match:
                target = ip_match.group(1)
                retry = f"ping -n 4 {target}"
                logger.info(f"Hardware: ping retry: {retry}")
                try:
                    r = subprocess.run(
                        retry,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        return r.stdout.strip()
                except Exception:
                    pass

        if "git" in cmd_lower:
            if "not a git repository" in lower_err:
                return "Error: this path is not a git repository."
            if "does not exist" in lower_err or "no such file" in lower_err:
                return "Error: path not found."
            if "nothing to commit" in (stdout or "").lower():
                return "Nothing to commit."

        parts = [f"Error: exit code {proc.returncode}"]
        if stdout:
            parts.append(f"Output:\n{stdout[:2000]}")
        if stderr:
            parts.append(f"Error:\n{stderr[:2000]}")
        return "\n".join(parts)


_MANAGER: "HardwareManager | None" = None
_MANAGER_LOCK = threading.Lock()


def _get_manager() -> HardwareManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = HardwareManager()
    return _MANAGER


def _list_commands_sync() -> str:
    return _get_manager().list_commands()


def _run_command_sync(command: str, args: str = "", timeout: int = _DEFAULT_TIMEOUT) -> str:
    return _get_manager().run_command(command, args, timeout)


async def list_commands(args: dict[str, Any]) -> str:
    """Show the command shortcuts registered in config."""
    return await asyncio.to_thread(_list_commands_sync)


async def run_command(args: dict[str, Any]) -> str:
    """Run an OS shell command (or a configured shortcut)."""
    command = str(args.get("command") or "").strip()
    extra = str(args.get("args") or "").strip()
    timeout = int(args.get("timeout") or _DEFAULT_TIMEOUT)
    return await asyncio.to_thread(_run_command_sync, command, extra, timeout)


async def hardware_command(args: dict[str, Any]) -> str:
    """Alias for run_command."""
    return await run_command(args)
