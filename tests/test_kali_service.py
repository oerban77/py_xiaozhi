from subprocess import TimeoutExpired
from unittest.mock import Mock

from src.mcp.tools.kali import service


def test_resolve_nmap_from_standard_windows_install_location(monkeypatch, tmp_path):
    install_root = tmp_path / "Program Files (x86)"
    executable = install_root / "Nmap" / "nmap.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()

    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    # No bundled copy and nothing on PATH, so the installer location is used
    monkeypatch.setattr(service, "get_tool_path", lambda _name: _name)
    monkeypatch.setenv("ProgramFiles(x86)", str(install_root))

    assert service._resolve_executable("nmap") == str(executable)


def test_resolve_prefers_bundled_tool(monkeypatch, tmp_path):
    """A bundled portable copy wins over a system install."""
    bundled = tmp_path / "libs" / "tools" / "win" / "x64" / "nmap.exe"
    monkeypatch.setattr(service, "get_tool_path", lambda _name: str(bundled))

    assert service._resolve_executable("nmap") == str(bundled)


def test_basic_nmap_scan_uses_fast_lan_options(monkeypatch):
    run = Mock(return_value=Mock(stdout="scan results", stderr="", returncode=0))
    monkeypatch.setattr(service, "_is_enabled", lambda: True)
    monkeypatch.setattr(service, "_resolve_executable", lambda command: command)
    monkeypatch.setattr(service.subprocess, "run", run)

    result = service.nmap_basic_scan.__wrapped__({"target": "192.168.1.0/24"})

    assert result == "scan results"
    args, kwargs = run.call_args
    assert args[0] == [
        "nmap", "-n", "-T4", "-F", "--max-retries", "1", "--host-timeout",
        "30s", "--stats-every", "10s", "192.168.1.0/24",
    ]
    assert kwargs["timeout"] == service._NMAP_BASIC_TIMEOUT


def test_timeout_returns_partial_nmap_output(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise TimeoutExpired("nmap", 90, output=b"Nmap scan report", stderr=b"50% done")

    monkeypatch.setattr(service, "_is_enabled", lambda: True)
    monkeypatch.setattr(service, "_resolve_executable", lambda command: command)
    monkeypatch.setattr(service.subprocess, "run", timeout)

    result = service._run(["nmap", "192.168.1.0/24"], timeout=90)

    assert "timed out after 90s" in result
    assert "Nmap scan report" in result
    assert "50% done" in result