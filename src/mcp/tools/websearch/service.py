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
import json
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

# anysearch keyless search endpoint (verified reachable without an API key).
_ANYSEARCH_URL = "https://api.anysearch.com/v1/search"
# Google News RPC that turns an opaque news.google.com article blob into the
# publisher's real URL (ported from free-search-mcp; verified working).
_BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

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
        # <source url="..."> holds the publisher's root domain (not the article
        # URL). It is the only direct pointer to the original site, because the
        # <link> is a news.google.com redirector that needs JavaScript.
        publisher_url = ""
        source_el = item.find("source")
        if source_el is not None:
            publisher_url = (source_el.get("url") or "").strip()
        if not title or not link:
            continue
        if snippet:
            snippet = _html_to_text(snippet)[:_SNIPPET_CHARS]
        out.append(
            {
                "title": title,
                "snippet": snippet,
                "url": link,
                "publisher_url": publisher_url,
                "date": date,
                "source": source or "Google News",
            }
        )
        if len(out) >= count:
            break
    return out


def _is_google_news_url(url: str) -> bool:
    """True for news.google.com article redirectors.

    These pages render the article with client-side JavaScript, so a plain HTTP
    fetch only ever returns an empty application shell. Detecting them lets
    read_article explain the situation instead of reporting a confusing
    "no readable content" error.
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host.endswith("news.google.com") and "/articles/" in url


def _meta_fallback(raw: str) -> str:
    """Last-resort description from meta tags / title for JS-heavy pages."""
    for prop in ("og:description", "twitter:description", "description"):
        m = re.search(
            r'<meta[^>]*(?:property|name)=["\']' + re.escape(prop) + r'["\'][^>]*content=["\']([^"\']+)',
            raw,
            re.I,
        )
        if m:
            text = html.unescape(m.group(1)).strip()
            if len(text) >= 40:
                return text
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    if m:
        text = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        if text:
            return f"(title only) {text}"
    return ""


def _format_results(results: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        publisher = r.get("publisher_url") or ""
        lines.append(
            f"{i}. {r.get('title', '')}\n"
            f"   Source: {r.get('source', '')} | {r.get('date', '')}\n"
            f"   {r.get('snippet', '')}\n"
            f"   Link: {r.get('url', '')}"
            + (f"\n   Publisher: {publisher}" if publisher else "")
        )
    return "\n\n".join(lines)


def _web_search_config() -> dict[str, str]:
    """Read the WEB_SEARCH config section; never raises (tools run before config init)."""
    try:
        from src.utils.config_manager import get_config

        section = get_config().get_config("WEB_SEARCH", {}) or {}
    except Exception:
        return {}
    return section if isinstance(section, dict) else {}


def _anysearch_search(query: str, count: int) -> list[dict[str, str]]:
    """Search via the keyless anysearch endpoint; returns result dicts."""
    base = (_web_search_config().get("ANYSEARCH_URL") or "").strip() or _ANYSEARCH_URL
    try:
        resp = _get_session().post(
            base,
            json={"query": query, "max_results": max(1, min(100, count))},
            headers={"Content-Type": "application/json"},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.debug(f"anysearch -> HTTP {resp.status_code}")
            return []
        data = resp.json()
    except Exception as e:
        logger.debug(f"anysearch failed: {e}")
        return []

    # Body shape: {"code": ..., "data": {"results": [...]}}; be tolerant.
    results = None
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            results = inner.get("results")
        if results is None:
            results = data.get("results")

    out: list[dict[str, str]] = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        snippet = str(item.get("snippet") or "").strip()
        if not snippet:
            # anysearch repeats the snippet in "content" for some queries.
            snippet = str(item.get("content") or "").strip()
        out.append(
            {
                "title": title,
                "snippet": _html_to_text(snippet)[:_SNIPPET_CHARS] if snippet else "",
                "url": url,
                "publisher_url": "",
                "date": "",
                "source": item.get("engine") or "anysearch",
            }
        )
        if len(out) >= count:
            break
    return out


_GARTURL_RE = re.compile(r'garturlres\\?",\s*\\?"\s*(https?://[^"\\]+)')


def _parse_batchexecute(body: str) -> str | None:
    """Pull the publisher URL out of a ``batchexecute`` response.

    Body shape (after the ``)]}'`` XSSI guard):
        [["wrb.fr","Fbv4je","[\"garturlres\",\"<URL>\",1]",...], ...]
    """
    if not body:
        return None
    text = body.lstrip(")]}'").strip()
    # The array we want is usually on its own line after a length prefix; the
    # length line itself is a bare number, so skip lines that are not arrays.
    candidate = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and "Fbv4je" in line:
            candidate = line
            break
    if candidate is None:
        candidate = text
    try:
        arr = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        arr = None
    for row in arr if isinstance(arr, list) else []:
        if not isinstance(row, list) or len(row) <= 2 or row[1] != "Fbv4je":
            continue
        # row[2] is a JSON *string* holding the garturlres payload.
        target = row[2]
        if not isinstance(target, str):
            continue
        try:
            payload = json.loads(target)
        except (json.JSONDecodeError, ValueError, TypeError):
            # Escaped/pretty-printed variants: pull the URL out directly.
            m = _GARTURL_RE.search(target)
            if m:
                return m.group(1)
            continue
        if (
            isinstance(payload, list)
            and len(payload) > 1
            and isinstance(payload[1], str)
            and payload[1].startswith("http")
        ):
            return payload[1]
    # Fallback: a pretty-printed/chunked response defeats the structured parse,
    # so pull the garturlres URL straight out of the raw text.
    m = _GARTURL_RE.search(body)
    return m.group(1) if m else None


def _resolve_google_news_url(url: str, deadline: float | None = None) -> str | None:
    """Turn an opaque news.google.com article blob into the publisher's URL.

    The blob renders with client-side JavaScript, so a plain fetch only returns
    an empty shell. Google's own web client resolves it through a private RPC:
    the article page carries a signature (``data-n-a-sg``), a timestamp
    (``data-n-a-ts``) and the article id (``data-n-a-id``), which are POSTed to
    the ``batchexecute`` endpoint; the reply contains the real URL.
    Best-effort: any failure returns ``None`` and the caller keeps the blob.
    """
    try:
        shell = _http_get(url, timeout=_remaining_timeout(deadline))
        if shell.status_code != 200:
            return None
        # The shell is ~600 KB; only the attributes at the top matter, but the
        # signature can appear late, so read the whole body within the deadline.
        html_raw = _read_limited(shell, 1024 * 1024, deadline)
    except Exception as e:
        logger.debug(f"google news shell fetch failed: {e}")
        return None

    sg = re.search(r'data-n-a-sg="([^"]+)"', html_raw)
    ts = re.search(r'data-n-a-ts="([^"]+)"', html_raw)
    aid = re.search(r'data-n-a-id="([^"]+)"', html_raw)
    if not (sg and ts and aid):
        return None

    inner = json.dumps(
        [
            "garturlreq",
            [
                ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
                 None, None, None, None, None, 0, 1],
                "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
            ],
            aid.group(1),
            int(ts.group(1)),
            sg.group(1),
        ]
    )
    freq = "f.req=" + urllib.parse.quote(
        json.dumps([[["Fbv4je", inner, None, "generic"]]])
    )
    try:
        resp = _get_session().post(
            _BATCHEXECUTE_URL,
            data=freq,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
            },
            timeout=_remaining_timeout(deadline),
        )
        if resp.status_code != 200:
            logger.debug(f"google news batchexecute -> HTTP {resp.status_code}")
            return None
        body = resp.text or ""
    except Exception as e:
        logger.debug(f"google news batchexecute failed: {e}")
        return None

    return _parse_batchexecute(body)


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

    engine = (_web_search_config().get("SEARCH_ENGINE") or "anysearch").strip().lower()

    results: list[dict[str, str]] = []
    if engine != "gnews":
        try:
            results = await asyncio.to_thread(_anysearch_search, query, count)
        except Exception as e:
            logger.error(f"web_search (anysearch) failed: {e}", exc_info=True)

    # Fall back to Google News RSS when the search backend is unavailable or
    # returns nothing (it is also the only source for news-specific results).
    if not results:
        try:
            results = await asyncio.to_thread(_google_news_search, query, count, lang)
        except Exception as e:
            logger.error(f"web_search (google news) failed: {e}", exc_info=True)
            if not results:
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

    if _is_google_news_url(url):
        # The link is a JS redirector; resolve it to the publisher's real URL
        # through Google's batchexecute RPC, then read that page instead.
        resolved = await asyncio.to_thread(_resolve_google_news_url, url, deadline)
        if resolved:
            url = resolved
        else:
            return (
                "This is a Google News link whose article page only renders with "
                "JavaScript, and the redirect could not be resolved right now. "
                "Try the search result's 'Publisher' site or a direct article URL."
            )

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
        # JS-heavy or paywalled pages may still expose a meta description.
        text = _meta_fallback(raw)
        if text:
            return text[:max_chars]
        return (
            "Could not extract any readable content from that page. "
            "The site may require JavaScript or block automated requests."
        )
    return text[:max_chars]
