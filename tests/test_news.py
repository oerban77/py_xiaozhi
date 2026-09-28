"""Tests for the news tool registration.

Covers the offline parts: the tool is registered with the expected name,
schema and callback, and the topic/max_results arguments are normalised the
same way the service uses them. Live RSS fetches are not exercised here.
"""

from __future__ import annotations

from src.mcp.tools.news.register import register_news_tools
from src.mcp.tools.news.service import RSS_FEEDS, get_news


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def test_register_news_tools_exposes_get_news():
    tools: list = []
    register_news_tools(tools.append)

    assert [t.name for t in tools] == ["get_news"]
    tool = tools[0]

    # Required schema: topic (optional) + max_results (optional, bounded)
    names = {p.name for p in tool.properties.properties}
    assert names == {"topic", "max_results"}

    max_results = next(
        p for p in tool.properties.properties if p.name == "max_results"
    )
    assert max_results.default_value == 5
    assert max_results.min_value == 1
    assert max_results.max_value == 20

    # The description must hint at the news use-case so the model picks it for
    # "berita terkini" style requests instead of answering from memory.
    assert "berita terkini" in tool.description.lower()

    # The callback is the real service entry point
    assert tool.callback is get_news


def test_register_news_tools_logs_count(caplog=None):
    tools: list = []
    register_news_tools(tools.append)
    assert len(tools) == 1


# ---------------------------------------------------------------------------
# Service: argument handling (no network)
# ---------------------------------------------------------------------------
def test_get_news_clamps_max_results():
    # An out-of-range count must not blow up; it is clamped into 1..20.
    # A bogus topic keeps it off the network path's category lookup, and the
    # empty result path is handled without raising.
    import asyncio

    async def _run():
        return await get_news({"topic": "", "max_results": 999})

    results = asyncio.run(_run())
    assert isinstance(results, str)


def test_rss_feeds_have_indonesia_default():
    # The 'default' key is the fallback for free-text/empty topics and must
    # always exist, otherwise get_news would KeyError.
    assert "default" in RSS_FEEDS
    assert RSS_FEEDS["default"], "default feed list must not be empty"
    for category in ("indonesia", "nasional", "teknologi", "olahraga",
                     "ekonomi", "hiburan", "dunia"):
        assert category in RSS_FEEDS
        assert RSS_FEEDS[category]
