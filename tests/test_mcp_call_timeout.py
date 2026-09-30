"""Tests for the tools/call wall-clock budget (MCP_TOOLS.CALL_TIMEOUT).

A slow tool must not outlive the server-side tool-call limit: when it does,
the session is torn down and the result is discarded, so the user sees a
disconnect instead of an answer. The server now answers within the budget.
"""

import asyncio
import json

import src.mcp.tools.kali.service as kali_service
from src.mcp import mcp_server
from src.mcp.mcp_server import McpServer
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType


def _make_server() -> McpServer:
    return McpServer()


def _capture() -> tuple[list, callable]:
    """An async send callback that records every reply sent over the wire."""
    sent = []

    async def _send(payload: str):
        sent.append(json.loads(payload))

    return sent, _send


def _add_slow_tool(server: McpServer, name: str, delay: float) -> None:
    async def callback(_args: dict) -> str:
        await asyncio.sleep(delay)
        return json.dumps({"content": [{"type": "text", "text": "done"}], "isError": False})

    server.add_tool(
        McpTool(
            name,
            "A tool that sleeps",
            PropertyList([Property("target", PropertyType.STRING, "localhost")]),
            callback,
        )
    )


def _add_failing_tool(server: McpServer, name: str) -> None:
    async def callback(_args: dict) -> str:
        raise RuntimeError("boom")

    server.add_tool(
        McpTool(
            name,
            "A tool that raises",
            PropertyList([Property("target", PropertyType.STRING, "localhost")]),
            callback,
        )
    )


def test_slow_tool_replies_with_timeout_error(monkeypatch):
    """A tool exceeding the budget gets a timeout reply, not silence."""
    server = _make_server()
    _add_slow_tool(server, "slow_scan", delay=10.0)

    sent, send = _capture()
    server.set_send_callback(send)
    monkeypatch.setattr(McpServer, "_call_timeout", lambda self: 0.2)

    asyncio.run(server._handle_tool_call(1, {"name": "slow_scan", "arguments": {}}))

    assert len(sent) == 1
    reply = sent[0]
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == 1
    assert "error" in reply
    assert "timed out" in reply["error"]["message"]


def test_fast_tool_still_returns_result(monkeypatch):
    """A tool finishing inside the budget returns its normal result."""
    server = _make_server()
    _add_slow_tool(server, "fast_scan", delay=0.01)

    sent, send = _capture()
    server.set_send_callback(send)
    monkeypatch.setattr(McpServer, "_call_timeout", lambda self: 5.0)

    asyncio.run(server._handle_tool_call(2, {"name": "fast_scan", "arguments": {}}))

    assert len(sent) == 1
    reply = sent[0]
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == 2
    assert "result" in reply
    assert reply["result"]["isError"] is False


def test_tool_exception_still_replies(monkeypatch):
    """A raising tool produces an error reply so the session stays alive.

    McpTool.call converts an exception into an MCP error result (isError: true),
    which is the protocol-correct way to report a tool failure.
    """
    server = _make_server()
    _add_failing_tool(server, "bad_tool")

    sent, send = _capture()
    server.set_send_callback(send)
    monkeypatch.setattr(McpServer, "_call_timeout", lambda self: 5.0)

    asyncio.run(server._handle_tool_call(3, {"name": "bad_tool", "arguments": {}}))

    assert len(sent) == 1
    reply = sent[0]
    assert reply["id"] == 3
    assert "result" in reply
    assert reply["result"]["isError"] is True
    assert "boom" in reply["result"]["content"][0]["text"]


def test_unknown_tool_replies_error():
    server = _make_server()
    sent, send = _capture()
    server.set_send_callback(send)

    asyncio.run(server._handle_tool_call(4, {"name": "no_such_tool", "arguments": {}}))

    assert len(sent) == 1
    assert "error" in sent[0]
    assert "Unknown tool" in sent[0]["error"]["message"]


def test_missing_tool_name_replies_error():
    server = _make_server()
    sent, send = _capture()
    server.set_send_callback(send)

    asyncio.run(server._handle_tool_call(5, {}))

    assert len(sent) == 1
    assert "error" in sent[0]


def test_promoted_document_tool_is_not_repeated_across_pages():
    server = _make_server()

    async def callback(_args: dict) -> str:
        return json.dumps({"content": [], "isError": False})

    for index in range(4):
        server.add_tool(
            McpTool(
                f"page_tool_{index}",
                "x" * 2800,
                PropertyList([]),
                callback,
            )
        )
    server.add_tool(
        McpTool("document_manage", "Read an attached document", PropertyList([]), callback)
    )
    server.add_tool(McpTool("after_document", "After document", PropertyList([]), callback))
    server.set_pending_document("2.pptx", "isinya apa ini?")

    sent, send = _capture()
    server.set_send_callback(send)

    async def read_all_pages():
        names = []
        cursor = ""
        while True:
            params = {"cursor": cursor} if cursor else {}
            await server._handle_tools_list(len(sent) + 1, params)
            result = sent[-1]["result"]
            names.extend(tool["name"] for tool in result["tools"])
            cursor = result.get("nextCursor", "")
            if not cursor:
                return names

    names = asyncio.run(read_all_pages())

    assert len(sent) > 1
    assert names[0] == "document_manage"
    assert names.count("document_manage") == 1
    assert len(names) == len(set(names))

    sent.clear()
    asyncio.run(server._handle_tools_list(99, {"cursor": "document_manage"}))
    names_after_cursor = [tool["name"] for tool in sent[0]["result"]["tools"]]
    assert "document_manage" not in names_after_cursor
    assert "after_document" in names_after_cursor


def test_tools_list_disables_pagination_when_config_says_so(monkeypatch):
    """A disabled paginator returns the full tool list without nextCursor."""
    server = _make_server()

    async def callback(_args: dict) -> str:
        return json.dumps({"content": [], "isError": False})

    for index in range(200):
        server.add_tool(
            McpTool(
                f"tool_{index}",
                "x" * 2500,
                PropertyList([]),
                callback,
            )
        )

    class FakeConfig:
        def get_config(self, path, default=None):
            if path == "MCP_TOOLS.PAGINATION_ENABLED":
                return False
            if path == "MCP_TOOLS.DISABLED":
                return []
            return default

    monkeypatch.setattr(mcp_server, "get_config", lambda: FakeConfig(), raising=False)
    import src.utils.config_manager as cm
    monkeypatch.setattr(cm, "get_config", lambda: FakeConfig())

    sent, send = _capture()
    server.set_send_callback(send)

    asyncio.run(server._handle_tools_list(51, {}))

    assert len(sent) == 1
    result = sent[0]["result"]
    assert "nextCursor" not in result
    assert len(result["tools"]) >= 80


def test_kali_run_hides_windows_console(monkeypatch):
    """Windows nmap calls should not create a visible terminal window."""
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return type("Result", (), {"stdout": "ok", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(kali_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(kali_service, "_is_enabled", lambda: True)
    monkeypatch.setattr(kali_service, "_config_timeout", lambda: 5)
    monkeypatch.setattr(kali_service, "_resolve_executable", lambda cmd: cmd)
    monkeypatch.setattr(kali_service.subprocess, "run", fake_run)

    result = kali_service._run(["nmap", "-F", "127.0.0.1"], timeout=5)

    assert result == "ok"
    assert captured["kwargs"].get("creationflags") == kali_service.subprocess.CREATE_NO_WINDOW
    assert captured["kwargs"].get("startupinfo") is not None


def test_call_timeout_reads_config(monkeypatch):
    """The budget comes from MCP_TOOLS.CALL_TIMEOUT."""
    server = _make_server()

    class FakeConfig:
        def get_config(self, path, default=None):
            assert path == "MCP_TOOLS.CALL_TIMEOUT"
            return 30

    monkeypatch.setattr(
        mcp_server, "get_config", lambda: FakeConfig(), raising=False
    )
    # The helper imports get_config lazily, so patch the module it imports from
    import src.utils.config_manager as cm

    monkeypatch.setattr(cm, "get_config", lambda: FakeConfig())

    assert server._call_timeout() == 30.0


def test_call_timeout_zero_disables_limit(monkeypatch):
    server = _make_server()

    class FakeConfig:
        def get_config(self, path, default=None):
            return 0

    import src.utils.config_manager as cm

    monkeypatch.setattr(cm, "get_config", lambda: FakeConfig())

    assert server._call_timeout() == 0.0


def test_call_timeout_clamps_to_range(monkeypatch):
    """Out-of-range values are clamped to a sane window."""
    server = _make_server()

    class FakeConfig:
        def __init__(self, value):
            self._value = value

        def get_config(self, path, default=None):
            return self._value

    import src.utils.config_manager as cm

    monkeypatch.setattr(cm, "get_config", lambda: FakeConfig(-5))
    assert server._call_timeout() == 1.0

    monkeypatch.setattr(cm, "get_config", lambda: FakeConfig(99999))
    assert server._call_timeout() == 600.0


def test_call_timeout_falls_back_when_config_unavailable(monkeypatch):
    """A broken/missing config still yields a safe default."""
    server = _make_server()

    import src.utils.config_manager as cm

    def boom():
        raise RuntimeError("config not loaded")

    monkeypatch.setattr(cm, "get_config", boom)

    assert server._call_timeout() == 45.0
