"""Opus music catalog: list/search the online Opus song catalog.

Ported from the reference Xiaozhi desktop app (app/mcp/modules/music.py):
- catalog: https://cdn.jsdelivr.net/gh/oerban77/music_opus@main/catalog.json
- streams: https://cdn.jsdelivr.net/gh/oerban77/music_opus@main/music/<file>.opus_stream
  with a raw.githubusercontent.com fallback
- fuzzy search (SearchNormalize + EditDistanceAtMostOne), at most 5 hits

Differences from the original:
- the catalog is cached in memory with a TTL (the reference caches forever)
- the catalog URL / stream base are configurable
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import requests

from src.logging import get_logger

logger = get_logger()

DEFAULT_CATALOG_URL = (
    "https://cdn.jsdelivr.net/gh/oerban77/music_opus@main/catalog.json"
)
DEFAULT_STREAM_BASE = "https://cdn.jsdelivr.net/gh/oerban77/music_opus@main/music/"
_RAW_STREAM_BASE = "https://raw.githubusercontent.com/oerban77/music_opus/main/music/"
_STREAM_SUFFIX = ".opus_stream"

_CATALOG_TTL_S = 300.0
_CATALOG_MAX_BYTES = 4 * 1024 * 1024
_REQUEST_TIMEOUT = 20
MAX_SEARCH_RESULTS = 5
LIST_PAGE_SIZE = 10


@dataclass
class CatalogTrack:
    file: str
    url: str
    title: str
    artist: str = ""
    dur_s: float = 0.0

    def display_name(self) -> str:
        name = self.title or self.file
        if self.artist:
            name = f"{name} - {self.artist}"
        return name


@dataclass
class CatalogPage:
    items: list[CatalogTrack]
    # offset of the next page; -1 means there are no more tracks
    next_cursor: int


_session: requests.Session | None = None
_catalog_cache: list[CatalogTrack] | None = None
_catalog_fetched_at = 0.0


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(
            {
                "User-Agent": "XiaoZhi-MusicCatalog/1.0",
                "Accept": "application/json, */*",
            }
        )
    return _session


def _normalize_base(stream_base: str) -> str:
    base = (stream_base or "").strip().rstrip("/")
    return base if base else DEFAULT_STREAM_BASE.rstrip("/")


def build_stream_url(file: str, stream_base: str) -> str:
    return f"{_normalize_base(stream_base)}/{file}{_STREAM_SUFFIX}"


def stream_url_candidates(url: str) -> list[str]:
    """Playback URL fallbacks: jsdelivr first, then raw.githubusercontent (or vice versa)."""
    cands = [url]
    if _RAW_STREAM_BASE in url:
        cands.append(url.replace(_RAW_STREAM_BASE, DEFAULT_STREAM_BASE))
    elif DEFAULT_STREAM_BASE in url:
        cands.append(url.replace(DEFAULT_STREAM_BASE, _RAW_STREAM_BASE))

    out: list[str] = []
    for cand in cands:
        if cand and cand not in out:
            out.append(cand)
    return out

def _parse_catalog(body: str, stream_base: str) -> list[CatalogTrack]:
    tracks: list[CatalogTrack] = []
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Opus catalog is not valid JSON")
        return tracks

    for item in data.get("tracks", []):
        if not isinstance(item, dict):
            continue
        file = (item.get("file") or "").strip()
        if not file:
            continue
        try:
            dur_s = float(item.get("dur_s") or 0)
        except (TypeError, ValueError):
            dur_s = 0.0
        tracks.append(
            CatalogTrack(
                file=file,
                url=build_stream_url(file, stream_base),
                title=(item.get("title") or file).strip(),
                artist=(item.get("artist") or "").strip(),
                dur_s=dur_s,
            )
        )
    return tracks


def _fetch_catalog_sync(catalog_url: str, stream_base: str) -> list[CatalogTrack]:
    url = (catalog_url or "").strip() or DEFAULT_CATALOG_URL
    try:
        resp = _get_session().get(
            url, timeout=_REQUEST_TIMEOUT, stream=True, allow_redirects=True
        )
        if resp.status_code != 200:
            logger.warning(f"Opus catalog HTTP {resp.status_code}: {url}")
            return []
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            remaining = _CATALOG_MAX_BYTES - total
            if remaining <= 0:
                logger.warning("Opus catalog exceeds the size limit; truncated")
                break
            chunks.append(chunk[:remaining])
            total += len(chunks[-1])
        body = b"".join(chunks).decode("utf-8", errors="replace")
        return _parse_catalog(body, stream_base)
    except Exception as e:
        logger.warning(f"Failed to fetch the Opus catalog: {e}")
        return []


async def fetch_catalog(
    catalog_url: str = "",
    stream_base: str = "",
    *,
    force: bool = False,
) -> list[CatalogTrack]:
    """Get the catalog (cached with a TTL). Never raises; returns [] on failure."""
    global _catalog_cache, _catalog_fetched_at

    if not force and _catalog_cache is not None:
        if (time.time() - _catalog_fetched_at) < _CATALOG_TTL_S:
            return _catalog_cache

    tracks = await asyncio.to_thread(
        _fetch_catalog_sync, catalog_url, stream_base
    )
    if tracks:
        _catalog_cache = tracks
        _catalog_fetched_at = time.time()
        logger.info(f"Opus catalog loaded: {len(tracks)} track(s)")
    return tracks


def invalidate_catalog() -> None:
    global _catalog_cache, _catalog_fetched_at
    _catalog_cache = None
    _catalog_fetched_at = 0.0


# ── Fuzzy search (port of SearchNormalize + EditDistanceAtMostOne) ──────────


def _search_normalize(value: str) -> str:
    return "".join(c.lower() if c.isalnum() else " " for c in value)


def _edit_distance_at_most_one(left: str, right: str) -> int:
    if left == right:
        return 0
    if len(left) + 1 < len(right) or len(right) + 1 < len(left):
        return 2
    if len(left) == len(right):
        diffs = 0
        for a, b in zip(left, right):
            if a != b:
                diffs += 1
                if diffs > 1:
                    return 2
        return diffs
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    si = li = 0
    skipped = False
    while si < len(shorter) and li < len(longer):
        if shorter[si] == longer[li]:
            si += 1
            li += 1
        elif skipped:
            return 2
        else:
            skipped = True
            li += 1
    return 1


def _token_matches(token: str, text: str) -> bool:
    if not token:
        return True
    start = 0
    while start < len(text):
        while start < len(text) and text[start] == " ":
            start += 1
        end = text.find(" ", start)
        if end == -1:
            end = len(text)
        if end > start:
            word = text[start:end]
            if token in word or (
                len(token) >= 4 and _edit_distance_at_most_one(token, word) <= 1
            ):
                return True
        start = end + 1
    return False


def search_tracks(query: str, tracks: list[CatalogTrack]) -> list[CatalogTrack]:
    """Substring match first, then per-token fuzzy matching (max 5 hits)."""
    if not query or not tracks:
        return []

    needle = query.lower()
    normalized_query = _search_normalize(needle)
    matches: list[CatalogTrack] = []

    for track in tracks:
        searchable = _search_normalize(f"{track.title} {track.artist} {track.file}")
        hit = normalized_query in searchable
        if not hit and normalized_query:
            hit = True
            has_token = False
            ts = 0
            while ts < len(normalized_query):
                while ts < len(normalized_query) and normalized_query[ts] == " ":
                    ts += 1
                te = normalized_query.find(" ", ts)
                if te == -1:
                    te = len(normalized_query)
                if te > ts:
                    has_token = True
                    if not _token_matches(normalized_query[ts:te], searchable):
                        hit = False
                        break
                ts = te + 1
            hit = hit and has_token
        if hit:
            matches.append(track)
            if len(matches) >= MAX_SEARCH_RESULTS:
                break
    return matches


def page(tracks: list[CatalogTrack], cursor: int) -> CatalogPage:
    """Slice one page out of the full list (cursor = offset, -1 end marker)."""
    if cursor < 0:
        cursor = 0
    if cursor >= len(tracks):
        return CatalogPage(items=[], next_cursor=-1)
    end = min(len(tracks), cursor + LIST_PAGE_SIZE)
    return CatalogPage(
        items=tracks[cursor:end],
        next_cursor=end if end < len(tracks) else -1,
    )


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def format_track_page(
    page_data: CatalogPage,
    *,
    total_count: int,
    cursor: int,
    header: str,
) -> str:
    """Render the page as text for the LLM (the URL is required to play a track)."""
    if not page_data.items:
        return (
            f"{header}: no tracks found. "
            "Use search_opus_songs with different keywords."
        )

    lines = [f"{header} ({total_count} track(s), from #{cursor + 1}):"]
    for i, track in enumerate(page_data.items, start=cursor + 1):
        dur = f" [{_format_duration(track.dur_s)}]" if track.dur_s > 0 else ""
        lines.append(f"{i}. {track.display_name()}{dur}")
        lines.append(f"   url: {track.url}")
    if page_data.next_cursor >= 0:
        lines.append(
            f"More tracks: call again with cursor={page_data.next_cursor}"
        )
    return "\n".join(lines)
