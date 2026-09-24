"""Example MCP plugin: no third-party dependencies; can be copied directly into the user's mcp_plugins/."""


def register(host):
    @host.tool(
        name="example.hello",
        description="Says hello. The name parameter is optional. Used to verify that the external MCP plugin loaded successfully.",
        props=[{"name": "name", "type": "string", "default": "world"}],
    )
    async def hello(args):
        name = (args or {}).get("name") or "world"
        return f"Hello, {name}! (from the external plugin com.example.hello)"

    # Optional: read-only config
    cfg = host.get("config_readonly")
    log = host.get("logger")
    if log and cfg is not None:
        log.debug("[example.hello] got config_readonly")
