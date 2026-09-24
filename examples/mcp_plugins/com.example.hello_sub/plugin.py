"""Example plugin: python-subprocess runtime."""


def register(host):
    @host.tool(
        name="example.hello_sub",
        description="The subprocess plugin says hello. The name parameter is optional.",
        props=[{"name": "name", "type": "string", "default": "world"}],
    )
    async def hello(args):
        name = (args or {}).get("name") or "world"
        return f"Hello, {name}! (subprocess plugin com.example.hello_sub)"
