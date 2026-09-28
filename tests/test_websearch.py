"""Tests for the keyless web search backends.

Covers the offline (pure) parts: config migration to the WEB_SEARCH section,
URL classification, the batchexecute response parser, and result formatting.
The live network calls are not exercised here.
"""

from __future__ import annotations

import json

from src.mcp.tools.websearch.service import (
    _format_results,
    _is_google_news_url,
    _parse_batchexecute,
)
from src.utils.config_manager import ConfigManager


# ---------------------------------------------------------------------------
# Config: the WEB_SEARCH section is added by the v4 migration
# ---------------------------------------------------------------------------
def test_config_version_is_4():
    assert ConfigManager.CONFIG_VERSION == 4
    assert ConfigManager.DEFAULT_CONFIG["CONFIG_VERSION"] == 4


def test_default_config_has_web_search_section():
    section = ConfigManager.DEFAULT_CONFIG["WEB_SEARCH"]
    assert section["SEARCH_ENGINE"] == "anysearch"
    assert section["ANYSEARCH_URL"] == ""


def test_migration_v3_to_v4_adds_web_search_defaults():
    cm = ConfigManager()
    before = {"CONFIG_VERSION": 3, "MCP_TOOLS": {"DISABLED": [], "CALL_TIMEOUT": 45}}
    after = cm._migrate_config(dict(before), from_version=3)

    assert after["CONFIG_VERSION"] == 4
    assert after["WEB_SEARCH"] == {"SEARCH_ENGINE": "anysearch", "ANYSEARCH_URL": ""}


def test_migration_v4_keeps_a_custom_engine():
    cm = ConfigManager()
    before = {
        "CONFIG_VERSION": 4,
        "WEB_SEARCH": {"SEARCH_ENGINE": "gnews", "ANYSEARCH_URL": "https://self.hosted"},
    }
    after = cm._migrate_config(dict(before), from_version=4)

    assert after["CONFIG_VERSION"] == 4
    assert after["WEB_SEARCH"]["SEARCH_ENGINE"] == "gnews"
    assert after["WEB_SEARCH"]["ANYSEARCH_URL"] == "https://self.hosted"


def test_migration_repairs_a_broken_web_search_section():
    cm = ConfigManager()
    before = {"CONFIG_VERSION": 3, "WEB_SEARCH": "not-a-dict"}
    after = cm._migrate_config(dict(before), from_version=3)

    assert isinstance(after["WEB_SEARCH"], dict)
    assert after["WEB_SEARCH"]["SEARCH_ENGINE"] == "anysearch"


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------
def test_is_google_news_url_matches_article_blobs():
    assert _is_google_news_url("https://news.google.com/rss/articles/CBMabc123")
    assert _is_google_news_url("https://news.google.com/articles/CBMabc123")


def test_is_google_news_url_rejects_non_article_urls():
    assert not _is_google_news_url("https://news.google.com/")
    assert not _is_google_news_url("https://news.google.com/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB")
    assert not _is_google_news_url("https://example.com/articles/CBMabc")
    assert not _is_google_news_url("not a url")


# ---------------------------------------------------------------------------
# batchexecute response parser
# ---------------------------------------------------------------------------
def _batchexecute(payload_str: str) -> str:
    row = json.dumps(["wrb.fr", "Fbv4je", payload_str])
    return ")]}'\n" + str(len(row) + 1) + "\n[" + row + "\n]"


def test_parse_batchexecute_extracts_publisher_url():
    body = _batchexecute(json.dumps(["garturlres", "https://www.example.com/news/story", 1]))
    assert _parse_batchexecute(body) == "https://www.example.com/news/story"


def test_parse_batchexecute_handles_spaces_after_commas():
    body = _batchexecute(json.dumps(["garturlres", "https://www.example.com/news/story", 1]))
    # Google sometimes pretty-prints; the regex must tolerate the whitespace.
    assert _parse_batchexecute(body.replace('",\\"', '", \\"')) is not None


def test_parse_batchexecute_uses_regex_fallback_on_chunked_body():
    # The array is split across lines, so the structured parse fails; the
    # garturlres URL must still be found in the raw text.
    chunked = (
        ")]}'\n84\n"
        '[["wrb.fr","Fbv4je","[\\"garturlres\\",\n'
        '\\"https://www.example.com/news/story\\",1]"]\n'
        "]"
    )
    assert _parse_batchexecute(chunked) == "https://www.example.com/news/story"


def test_parse_batchexecute_returns_none_on_garbage():
    assert _parse_batchexecute("") is None
    assert _parse_batchexecute(")]}'\n42\n[\"garbage\"]") is None
    assert _parse_batchexecute("no Fbv4je here") is None


def test_parse_batchexecute_ignores_a_payload_without_a_url():
    body = _batchexecute(json.dumps(["garturlres", None, 1]))
    assert _parse_batchexecute(body) is None


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------
def test_format_results_renders_title_snippet_and_link():
    out = _format_results(
        [{"title": "Title", "source": "Src", "date": "2026-09-28",
          "snippet": "Snippet", "url": "https://example.com/a"}]
    )
    assert "1. Title" in out
    assert "Source: Src | 2026-09-28" in out
    assert "Snippet" in out
    assert "Link: https://example.com/a" in out
    # No publisher line when the result has none.
    assert "Publisher:" not in out


def test_format_results_includes_the_publisher_when_present():
    out = _format_results(
        [{"title": "T", "source": "s", "date": "", "snippet": "",
          "url": "https://a", "publisher_url": "https://pub.example"}]
    )
    assert "Publisher: https://pub.example" in out
