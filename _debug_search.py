# -*- coding: utf-8 -*-
"""Debug: run web_search exactly as the MCP server would."""
import asyncio
import logging
import sys

sys.path.insert(0, ".")

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

from src.utils.config_manager import get_config, initialize_config

initialize_config()
cm = get_config()
print("CONFIG_VERSION:", cm.get_config("CONFIG_VERSION"))
print("WEB_SEARCH section:", cm.get_config("WEB_SEARCH", {}))


async def main():
    from src.mcp.tools.websearch.service import web_search

    print("\n===== query: berita terkini =====")
    out = await web_search({"query": "berita terkini", "count": 5, "language": "id-ID"})
    print("RESULT:\n" + out)

    print("\n===== query: python asyncio (sanity) =====")
    out2 = await web_search({"query": "python asyncio", "count": 3})
    print("RESULT:\n" + out2[:300])


asyncio.run(main())
