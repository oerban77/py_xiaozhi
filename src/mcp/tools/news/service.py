"""News MCP tool.

Ported from the reference Xiaozhi desktop app (mcp/modules/berita.py):
fetches the latest headlines from RSS feeds, with DuckDuckGo News as an
optional fallback for free-text topics.

Differences from the original:
- HTML cleaning uses the standard library instead of ``bs4``/``lxml``.
- The DuckDuckGo fallback is only attempted when the optional
  ``duckduckgo_search`` package is installed.
"""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

from src.logging import get_logger
from src.mcp.tools.websearch.service import _html_to_text

logger = get_logger()

_DDG_TIMEOUT = 8.0
_RSS_TIMEOUT = 8.0

# RSS feeds per category (lowercase), ordered from most reliable.
# 'default' is used for free-text topics (combined with a keyword filter).
RSS_FEEDS: dict[str, list[str]] = {
    "default": [
        "https://www.antaranews.com/rss/terkini.xml",
        "https://www.cnnindonesia.com/nasional/rss",
        "https://rss.tempo.co/nasional",
    ],
    "indonesia": [
        "https://www.antaranews.com/rss/terkini.xml",
        "https://www.cnnindonesia.com/nasional/rss",
        "https://rss.tempo.co/nasional",
    ],
    "nasional": [
        "https://www.cnnindonesia.com/nasional/rss",
        "https://rss.tempo.co/nasional",
        "https://www.antaranews.com/rss/terkini.xml",
    ],
    "teknologi": ["https://www.antaranews.com/rss/tekno.xml"],
    "tech": ["https://www.antaranews.com/rss/tekno.xml"],
    "olahraga": ["https://www.antaranews.com/rss/olahraga.xml"],
    "sepakbola": ["https://www.antaranews.com/rss/olahraga.xml"],
    "ekonomi": ["https://www.antaranews.com/rss/ekonomi.xml"],
    "finance": ["https://www.antaranews.com/rss/ekonomi.xml"],
    "hiburan": ["https://www.antaranews.com/rss/hiburan.xml"],
    "entertainment": ["https://www.antaranews.com/rss/hiburan.xml"],
    "dunia": ["https://www.antaranews.com/rss/dunia.xml"],
    "internasional": ["https://www.antaranews.com/rss/dunia.xml"],
}


def _fetch_rss(url: str, max_results: int, keyword: str = "") -> list[dict[str, str]]:
    """Fetch news from one RSS feed. Returns [] on failure (never raises)."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get(url, headers=headers, timeout=_RSS_TIMEOUT)
        if r.status_code != 200:
            logger.debug(f"RSS {url} -> HTTP {r.status_code}")
            return []
        root = ET.fromstring(r.content)
    except Exception as e:
        logger.debug(f"RSS {url} failed: {e}")
        return []

    out: list[dict[str, str]] = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        body = (it.findtext("description") or "").strip()
        date = (it.findtext("pubDate") or "").strip()
        if not title or not link:
            continue
        if body:
            body = re.sub(r"\s+", " ", _html_to_text(body))
        if keyword and keyword not in title.lower() and keyword not in body.lower():
            continue
        out.append(
            {
                "title": title,
                "body": body[:300],
                "url": link,
                "date": date,
                "source": "RSS",
            }
        )
        if len(out) >= max_results:
            break
    return out


def _ddg_news(topic: str, max_results: int) -> list[dict[str, str]]:
    """DuckDuckGo News with a hard timeout. Returns [] when unavailable/slow."""
    import threading

    try:
        from duckduckgo_search import DDGS  # noqa: PLC0415  (optional dependency)
    except ImportError:
        return []

    bag: dict[str, Any] = {"items": [], "error": None}

    def _work() -> None:
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.news(topic, max_results=max_results))
            # DDGS uses the key 'link'; normalize it to 'url'
            bag["items"] = [
                {
                    "title": d.get("title", ""),
                    "body": (d.get("body") or "")[:300],
                    "url": d.get("link") or d.get("url") or "",
                    "date": d.get("date", ""),
                    "source": d.get("source", "") or "DuckDuckGo",
                }
                for d in raw
                if d.get("title")
            ]
        except Exception as e:
            bag["error"] = e

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(_DDG_TIMEOUT)
    if t.is_alive():
        logger.warning(f"DDG news timeout ({_DDG_TIMEOUT}s); using RSS")
        return []
    if bag["error"] is not None:
        logger.debug(f"DDG news failed: {bag['error']}")
        return []
    return bag["items"]


def _format(results: list[dict[str, str]], source: str) -> str:
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. {r.get('title', '')}\n"
            f"   Source: {r.get('source', '') or source} | {r.get('date', '')}\n"
            f"   {r.get('body', '')}\n"
            f"   Link: {r.get('url', '')}"
        )
    return f"Source: {source}\n\n" + "\n\n".join(lines)


async def get_news(args: dict[str, Any]) -> str:
    topic = (args.get("topic") or "").strip()
    keyword = topic.lower()
    try:
        max_results = int(args.get("max_results", 5) or 5)
    except (TypeError, ValueError):
        max_results = 5
    max_results = max(1, min(20, max_results))

    feeds = RSS_FEEDS.get(keyword) or RSS_FEEDS["default"]
    # Category feeds are already specific -> do not filter again (articles often
    # do not mention the category name). Keyword filtering is only for free-text
    # topics that use the 'default' feed.
    apply_filter = keyword if keyword not in RSS_FEEDS else ""

    def _collect() -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        seen: set[str] = set()
        limit = max_results * 3 if apply_filter else max_results
        for url in feeds:
            for it in _fetch_rss(url, limit, apply_filter):
                u = it.get("url")
                if not u or u in seen:
                    continue
                seen.add(u)
                results.append(it)
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        return results

    try:
        results = await asyncio.to_thread(_collect)
    except Exception as e:
        logger.error(f"get_news failed: {e}", exc_info=True)
        return f"Failed to fetch news: {e}"

    if results:
        return _format(results, "RSS")

    # Free-text topic without a matching category: try DuckDuckGo (with timeout)
    if keyword:
        ddg = await asyncio.to_thread(_ddg_news, topic, max_results)
        if ddg:
            return _format(ddg, "DuckDuckGo")

    return (
        "No news found for that topic. Try another topic, e.g. 'indonesia', "
        "'nasional', 'teknologi', 'olahraga', 'ekonomi', 'hiburan', or 'dunia'."
    )
