"""Kali Linux security and binary-analysis MCP tools.

Ported from ``ccq1/awsome_kali_MCPServers`` (Apache-2.0), which exposes the
Kali CLI toolchain to an LLM: nmap, nm, objdump, strings, tshark and
traceroute.

Every tool builds an argv list and runs it WITHOUT a shell, so a parameter
value can never be re-interpreted as a shell operator. A tool that is not
installed on this host reports a plain message instead of raising, so the
assistant can fall back to another tool instead of aborting.

Config (config.json)::

    "KALI": {
        "ENABLED": true,
        "TIMEOUT": 120
    }
"""

from __future__ import annotations

import asyncio
import functools
import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Callable

from src.logging import get_logger
from src.utils.config_manager import get_config
from src.utils.resource_finder import get_tool_path

logger = get_logger()

_DEFAULT_TIMEOUT = 60
_MAX_TIMEOUT = 600
_MAX_OUTPUT = 16_000

# The reference gives the network tools a longer budget (300s), but the xiaozhi
# server tears the session down long before that. These stay inside the default
# MCP_TOOLS.CALL_TIMEOUT window so a scan still gets an answer back to the LLM.
_NET_TIMEOUT = 40
_NMAP_BASIC_TIMEOUT = 30

# strings -t accepts d (decimal), o (octal), x (hexadecimal).
_STRING_FORMATS = ("d", "o", "x")
# strings -e accepts s/S (7/8-bit) and b/l (16-bit big/little endian).
_STRING_ENCODINGS = ("s", "S", "b", "l")

# traceroute is tracert on Windows.
_TRACEROUTE_CMD = "tracert" if platform.system().lower().startswith("win") else "traceroute"


def _kali_config() -> dict[str, Any]:
    """Read the KALI config section (empty dict when unset)."""
    try:
        section = get_config().get_config("KALI", {}) or {}
    except Exception:  # ConfigManager not initialised yet
        return {}
    return section if isinstance(section, dict) else {}


def _config_timeout() -> int:
    section = _kali_config()
    raw = None
    for key in ("TIMEOUT", "timeout"):
        if key in section:
            raw = section[key]
            break
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        timeout = _DEFAULT_TIMEOUT
    return max(1, min(timeout, _MAX_TIMEOUT))


def _is_enabled() -> bool:
    section = _kali_config()
    for key in ("ENABLED", "enabled"):
        if key in section:
            return bool(section[key])
    return True


def _resolve_executable(command: str) -> str:
    """Resolve a command name to an executable path.

    Search order: bundled portable copy (libs/tools/<plat>/<arch>/) -> PATH ->
    common Windows installer locations (a system Nmap install).
    """
    resolved = get_tool_path(command)
    if resolved != command:
        return resolved

    if command.lower() == "nmap" and platform.system().lower().startswith("win"):
        for root in (
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramW6432"),
        ):
            if root:
                candidate = Path(root) / "Nmap" / "nmap.exe"
                if candidate.is_file():
                    return str(candidate)
    return command


def _captured_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").rstrip()
    return (value or "").rstrip()


def _tool(fn: Callable[[dict[str, Any]], str]) -> Callable[[dict[str, Any]], Any]:
    """Wrap a blocking handler so the framework can await it."""

    @functools.wraps(fn)
    async def runner(args: dict[str, Any]) -> str:
        return await asyncio.to_thread(fn, args)

    return runner


def _run(argv: list[str], timeout: int | None = None) -> str:
    """Run argv (no shell) and return bounded stdout+stderr as text."""
    if not _is_enabled():
        return "Kali MCP is disabled (KALI.ENABLED: false)"

    if timeout is None:
        timeout = _config_timeout()

    command = [_resolve_executable(argv[0]), *argv[1:]]
    logger.info(f"kali run: {' '.join(command)}")
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return (
            f"Command '{argv[0]}' is not installed or not in PATH. Install it "
            "with your package manager (apt install nmap/tshark/binutils "
            "traceroute, or the Windows equivalent) and try again."
        )
    except subprocess.TimeoutExpired as exc:
        partial = "\n".join(
            part
            for part in (_captured_text(exc.stdout), _captured_text(exc.stderr))
            if part
        )
        message = f"Command timed out after {timeout}s."
        if partial:
            message += f" Partial output:\n{partial}"
        else:
            message += " Narrow the scan or raise the KALI.TIMEOUT setting."
        return message
    except PermissionError:
        return (
            f"Permission denied running '{argv[0]}'. This option usually "
            "needs elevated/root privileges."
        )

    stdout = (proc.stdout or "").rstrip()
    stderr = (proc.stderr or "").rstrip()
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    output = "\n".join(parts)
    if not output:
        return f"Command finished with no output (exit code {proc.returncode})."
    if len(output) > _MAX_OUTPUT:
        return (
            output[:_MAX_OUTPUT]
            + f"\n... output truncated at {_MAX_OUTPUT} characters "
            f"({len(output)} total, exit code {proc.returncode})"
        )
    return output


def _target(args: dict[str, Any]) -> str:
    """The host/IP (nmap, traceroute) or file path (nm, objdump, strings)."""
    return str(args.get("target", "") or "").strip()


def _int_arg(args: dict[str, Any], name: str, default: int) -> int:
    try:
        return int(args.get(name))
    except (TypeError, ValueError):
        return default


# ─── nmap ───────────────────────────────────────────────────────────────────


@_tool
def nmap_basic_scan(args: dict[str, Any]) -> str:
    """Fast basic port scan suitable for local networks."""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(
        [
            "nmap",
            "-n",
            "-T4",
            "-F",
            "--max-retries",
            "1",
            "--host-timeout",
            "20s",
            "--stats-every",
            "10s",
            target,
        ],
        _NMAP_BASIC_TIMEOUT,
    )


@_tool
def nmap_intense_scan(args: dict[str, Any]) -> str:
    """Intense scan: nmap -T4 -A <target> (OS + version + scripts + traceroute)"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nmap", "-T4", "-A", target], _NET_TIMEOUT)


@_tool
def nmap_stealth_scan(args: dict[str, Any]) -> str:
    """SYN half-open scan: nmap -sS <target> (needs root)"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nmap", "-sS", target], _NET_TIMEOUT)


@_tool
def nmap_quick_scan(args: dict[str, Any]) -> str:
    """Quick scan of common ports: nmap -T4 -F <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nmap", "-T4", "-F", target], _NET_TIMEOUT)


@_tool
def nmap_vulnerability_scan(args: dict[str, Any]) -> str:
    """Version + vuln scripts: nmap -sV --script vuln <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nmap", "-sV", "--script", "vuln", target], _NET_TIMEOUT)


# ─── nm ─────────────────────────────────────────────────────────────────────


@_tool
def nm_basic_symbols(args: dict[str, Any]) -> str:
    """List symbols: nm <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nm", target])


@_tool
def nm_dynamic_symbols(args: dict[str, Any]) -> str:
    """List dynamic symbols: nm -D <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nm", "-D", target])


@_tool
def nm_demangle_symbols(args: dict[str, Any]) -> str:
    """Demangle C++ symbols: nm -C <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nm", "-C", target])


@_tool
def nm_numeric_sort(args: dict[str, Any]) -> str:
    """Symbols sorted by address: nm -n <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nm", "-n", target])


@_tool
def nm_size_sort(args: dict[str, Any]) -> str:
    """Symbols sorted by size: nm -S <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nm", "-S", target])


@_tool
def nm_undefined_symbols(args: dict[str, Any]) -> str:
    """List undefined symbols: nm -u <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["nm", "-u", target])


# ─── objdump ────────────────────────────────────────────────────────────────


@_tool
def objdump_file_headers(args: dict[str, Any]) -> str:
    """Show file headers: objdump -f <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["objdump", "-f", target])


@_tool
def objdump_disassemble(args: dict[str, Any]) -> str:
    """Disassemble a section: objdump -d -j <section> <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    section = str(args.get("section", "") or "").strip() or ".text"
    return _run(["objdump", "-d", "-j", section, target])


@_tool
def objdump_symbol_table(args: dict[str, Any]) -> str:
    """Show the symbol table: objdump -t <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["objdump", "-t", target])


@_tool
def objdump_section_headers(args: dict[str, Any]) -> str:
    """Show section headers: objdump -h <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["objdump", "-h", target])


@_tool
def objdump_full_contents(args: dict[str, Any]) -> str:
    """Show all headers and contents: objdump -x <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["objdump", "-x", target])


# ─── strings ────────────────────────────────────────────────────────────────


@_tool
def strings_basic(args: dict[str, Any]) -> str:
    """Extract printable strings: strings <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run(["strings", target])


@_tool
def strings_min_length(args: dict[str, Any]) -> str:
    """Strings at least N characters: strings -n <length> <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    length = max(1, _int_arg(args, "length", 6))
    return _run(["strings", "-n", str(length), target])


@_tool
def strings_offset(args: dict[str, Any]) -> str:
    """Strings with their offsets: strings -t <format> <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    fmt = str(args.get("format", "") or "").strip().lower()
    if fmt not in _STRING_FORMATS:
        fmt = "x"
    return _run(["strings", "-t", fmt, target])


@_tool
def strings_encoding(args: dict[str, Any]) -> str:
    """Strings in a given encoding: strings -e <encoding> <target>"""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    encoding = str(args.get("encoding", "") or "").strip()
    if encoding not in _STRING_ENCODINGS:
        encoding = "S"
    return _run(["strings", "-e", encoding, target])


# ─── tshark (Wireshark CLI) ─────────────────────────────────────────────────


@_tool
def tshark_capture_live(args: dict[str, Any]) -> str:
    """Capture live traffic: tshark -i <iface> -a duration:N [-f filter]"""
    interface = str(args.get("interface", "") or "").strip()
    if not interface:
        return "Field 'interface' is required"
    duration = max(1, _int_arg(args, "duration", 30))
    capture_filter = str(args.get("filter", "") or "").strip()
    argv = ["tshark", "-i", interface, "-a", f"duration:{duration}"]
    if capture_filter:
        argv += ["-f", capture_filter]
    return _run(argv, _NET_TIMEOUT)


@_tool
def tshark_analyze_pcap(args: dict[str, Any]) -> str:
    """Read a pcap with a display filter: tshark -r <file> [-Y filter]"""
    pcap_file = str(args.get("pcap_file", "") or "").strip()
    if not pcap_file:
        return "Field 'pcap_file' is required"
    argv = ["tshark", "-r", pcap_file]
    display_filter = str(args.get("display_filter", "") or "").strip()
    if display_filter:
        argv += ["-Y", display_filter]
    return _run(argv, _NET_TIMEOUT)


@_tool
def tshark_extract_http(args: dict[str, Any]) -> str:
    """Extract HTTP requests from a pcap: tshark -r <file> -Y http -T fields ..."""
    pcap_file = str(args.get("pcap_file", "") or "").strip()
    if not pcap_file:
        return "Field 'pcap_file' is required"
    return _run(
        [
            "tshark",
            "-r",
            pcap_file,
            "-Y",
            "http",
            "-T",
            "fields",
            "-e",
            "http.request.method",
            "-e",
            "http.request.uri",
        ],
        _NET_TIMEOUT,
    )


@_tool
def tshark_protocol_hierarchy(args: dict[str, Any]) -> str:
    """Protocol hierarchy stats: tshark -r <file> -q -z io,phs"""
    pcap_file = str(args.get("pcap_file", "") or "").strip()
    if not pcap_file:
        return "Field 'pcap_file' is required"
    return _run(["tshark", "-r", pcap_file, "-q", "-z", "io,phs"], _NET_TIMEOUT)


@_tool
def tshark_conversation_statistics(args: dict[str, Any]) -> str:
    """IP conversation stats: tshark -r <file> -q -z conv,ip"""
    pcap_file = str(args.get("pcap_file", "") or "").strip()
    if not pcap_file:
        return "Field 'pcap_file' is required"
    return _run(["tshark", "-r", pcap_file, "-q", "-z", "conv,ip"], _NET_TIMEOUT)


@_tool
def tshark_expert_info(args: dict[str, Any]) -> str:
    """Expert info (errors/warnings/notes): tshark -r <file> -q -z expert"""
    pcap_file = str(args.get("pcap_file", "") or "").strip()
    if not pcap_file:
        return "Field 'pcap_file' is required"
    return _run(["tshark", "-r", pcap_file, "-q", "-z", "expert"], _NET_TIMEOUT)


# ─── traceroute ─────────────────────────────────────────────────────────────


@_tool
def traceroute(args: dict[str, Any]) -> str:
    """Trace the route to a host (tracert on Windows)."""
    target = _target(args)
    if not target:
        return "Field 'target' is required"
    return _run([_TRACEROUTE_CMD, target], _NET_TIMEOUT)
