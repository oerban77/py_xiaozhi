"""Tests for the tools/call wall-clock budget (MCP_TOOLS.CALL_TIMEOUT).

A slow tool must not outlive the server-side tool-call limit: when it does,
the session is torn down and the result is discarded, so the user sees a
disconnect instead of an answer. The server now answers within the budget.
"""

import asyncio
import json

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
