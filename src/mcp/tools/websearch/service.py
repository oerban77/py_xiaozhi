"""Web search MCP tools.

Ported from the reference Xiaozhi desktop app (mcp/modules/websearch.py):
- ``web_search``     — search the web through the Google News RSS feed
- ``read_article``   — fetch and clean the body of an article URL

The original implementation relied on ``bs4``/``lxml``; here the HTML is cleaned
with the standard library (``html.parser``) so no extra dependency is required.
"""

from __future__ import annotations

import asyncio
import html
import html.parser
import re
import time
import urllib.parse
from typing import Any

import requests

from src.logging import get_logger

logger = get_logger()

_TIMEOUT = 20
# Total deadline (seconds) for a single page/article read attempt.
# requests only limits per-chunk time, so a "slow-drip" server or a long
# keep-alive could block the read forever. This global deadline guarantees the
# tool call always finishes.
_READ_DEADLINE_S = 25
_MAX_RESULTS = 10
_DEFAULT_RESULTS = 5
_MAX_ARTICLE_CHARS = 6000
_SNIPPET_CHARS = 300

_SESSION: requests.Session | None = None


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,id-ID;q=0.8,id;q=0.7",
            }
        )
    return _SESSION


def _remaining_timeout(deadline: float | None) -> float:
    """Remaining seconds until the deadline; at least 1s so requests does not error."""
    if deadline is None:
        return _TIMEOUT
    return max(1.0, deadline - time.monotonic())


def _http_get(url: str, headers: dict | None = None, timeout: float | None = None):
    hdrs = dict(_get_session().headers)
    if headers:
        hdrs.update(headers)
    return _get_session().get(
        url,
        headers=hdrs,
        timeout=timeout if timeout is not None else _TIMEOUT,
        allow_redirects=True,
    )


def _read_limited(resp: requests.Response, limit: int, deadline: float | None = None) -> str:
    """Read at most ``limit`` bytes of the response body (prevents memory blowup)."""
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=8192):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
        if deadline is not None and time.monotonic() > deadline:
            break
    return b"".join(chunks[:limit]).decode("utf-8", errors="replace")


class _TextExtractor(html.parser.HTMLParser):
    """Minimal HTML-to-text converter (no third-party dependency)."""

    _SKIP_TAGS = {
        "script", "style", "noscript", "iframe", "svg", "head",
        "nav", "footer", "header", "aside", "form",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_p = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if tag == "p":
            self._in_p = True
            self._parts.append("\n")
        elif tag in ("br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "p":
            self._in_p = False
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t\xa0]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()


def _html_to_text(raw: str) -> str:
    try:
        parser = _TextExtractor()
        parser.feed(raw)
        return parser.get_text()
    except Exception as e:
        logger.debug(f"HTML text extraction failed ({e}); falling back to tag stripping")
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        return re.sub(r"<[^>]+>", " ", text)


def _clean_article(raw: str) -> str:
    """Keep only the core paragraphs of an article page."""
    text = _html_to_text(raw)
    lines: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line) < 40:
            continue
        lines.append(line)
        if len("\n".join(lines)) >= _MAX_ARTICLE_CHARS:
            break
    return "\n\n".join(lines)[:_MAX_ARTICLE_CHARS]


def _google_news_search(query: str, count: int, lang: str) -> list[dict[str, str]]:
    """Search Google News RSS; returns a list of result dicts."""
    q = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={q}&hl={lang}&gl={lang.split('-')[-1].upper()}"
    try:
        resp = _http_get(url, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.debug(f"Google News RSS -> HTTP {resp.status_code}")
            return []
        body = resp.content.decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug(f"Google News RSS failed: {e}")
        return []

    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(body)
    except Exception as e:
        logger.debug(f"Google News RSS parse failed: {e}")
        return []

    out: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        snippet = (item.findtext("description") or "").strip()
        date = (item.findtext("pubDate") or "").strip()
        source = (item.findtext("source") or "").strip()
        if not title or not link:
            continue
        if snippet:
            snippet = _html_to_text(snippet)[:_SNIPPET_CHARS]
        out.append(
            {
                "title": title,
                "snippet": snippet,
                "url": link,
                "date": date,
                "source": source or "Google News",
            }
        )
        if len(out) >= count:
            break
    return out


def _format_results(results: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. {r.get('title', '')}\n"
            f"   Source: {r.get('source', '')} | {r.get('date', '')}\n"
            f"   {r.get('snippet', '')}\n"
            f"   Link: {r.get('url', '')}"
        )
    return "\n\n".join(lines)


async def web_search(args: dict[str, Any]) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "The search query must not be empty."
    try:
        count = int(args.get("count", _DEFAULT_RESULTS) or _DEFAULT_RESULTS)
    except (TypeError, ValueError):
        count = _DEFAULT_RESULTS
    count = max(1, min(_MAX_RESULTS, count))
    lang = (args.get("language") or "en-US").strip() or "en-US"

    try:
        results = await asyncio.to_thread(_google_news_search, query, count, lang)
    except Exception as e:
        logger.error(f"web_search failed: {e}", exc_info=True)
        return f"Search failed: {e}"

    if not results:
        return f"No results found for '{query}'. Try different keywords."
    return _format_results(results)


async def read_article(args: dict[str, Any]) -> str:
    url = (args.get("url") or "").strip()
    if not url:
        return "The article URL must not be empty."
    try:
        max_chars = int(args.get("max_chars", _MAX_ARTICLE_CHARS) or _MAX_ARTICLE_CHARS)
    except (TypeError, ValueError):
        max_chars = _MAX_ARTICLE_CHARS
    max_chars = max(200, min(20000, max_chars))

    deadline = time.monotonic() + _READ_DEADLINE_S
    try:
        resp = _http_get(url, timeout=_remaining_timeout(deadline))
        if resp.status_code != 200:
            return f"Failed to fetch the article (HTTP {resp.status_code})."
        raw = _read_limited(resp, 512 * 1024, deadline)
    except Exception as e:
        logger.error(f"read_article failed: {e}", exc_info=True)
        return f"Failed to read the article: {e}"

    text = _clean_article(raw)
    if not text:
        return "Could not extract any readable content from that page."
    return text[:max_chars]
