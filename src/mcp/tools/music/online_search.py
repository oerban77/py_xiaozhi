"""Online song search (Kuwo search → direct-link API template URL)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import quote

import requests

from src.logging import get_logger

from .config import DEFAULT_SEARCH_URL

logger = get_logger()


@dataclass
class SearchHit:
    song_id: str
    display_name: str
    duration: float
    # Template URL handed to downloader.resolve; not the final CDN URL
    api_url: str


async def search_song(song_name: str, config: dict) -> SearchHit | None:
    """Search for a song; return None on failure."""
    try:
        keyword_encoded = quote(song_name)
        limit = int(config.get("SEARCH_LIMIT") or 20)
        base = (config.get("SEARCH_URL") or "").strip() or DEFAULT_SEARCH_URL
        if "://" not in base:
            logger.warning(f"Invalid search API ({base!r}); using default Kuwo endpoint")
            base = DEFAULT_SEARCH_URL

        search_url = (
            f"{base}"
            f"?client=kt&all={keyword_encoded}&pn=0&rn={limit}"
            f"&uid=794762570&ver=kwplayer_ar_9.2.2.1&vipver=1"
            f"&show_copyright_off=1&newver=1&ft=music&cluster=0"
            f"&strategy=2012&encoding=utf8&rformat=json&vermerge=1"
            f"&mobi=1&issubtitle=1"
        )

        logger.info(f"Searching for song: {song_name} | {base}")

        response = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    requests.get,
                    search_url,
                    headers=config.get("HEADERS") or {},
                    timeout=10,
                )
                response.raise_for_status()
                break
            except requests.exceptions.Timeout:
                if attempt < 2:
                    logger.warning(f"Search timed out; retrying ({attempt + 1}/2)")
                    continue
                logger.error(f"Song search timed out after 2 retries: {song_name}")
                return None

        if response is None:
            return None

        data = response.json()
        results = data.get("abslist", [])
        if not results:
            logger.warning(f"Song not found: {song_name}")
            return None

        first = results[0]
        music_rid = first.get("MUSICRID", "")
        song_id = music_rid.replace("MUSIC_", "") if music_rid else ""
        title = first.get("SONGNAME", song_name)
        artist = first.get("ARTIST", "")
        album = first.get("ALBUM", "")

        if not song_id:
            logger.error("No song ID in search results")
            return None

        display_name = title
        if artist:
            display_name = f"{title} - {artist}"
            if album:
                display_name += f" ({album})"

        duration = 0.0
        duration_str = first.get("DURATION", "")
        if duration_str:
            try:
                duration = float(duration_str)
            except (ValueError, TypeError):
                pass

        quality = config.get("DEFAULT_BR") or "320k"
        url_api = (config.get("URL_API") or "").rstrip("/")
        api_url = f"{url_api}/url/kw/{song_id}/{quality}"

        logger.info(f"Song found: {display_name}, ID: {song_id}")
        return SearchHit(
            song_id=song_id,
            display_name=display_name,
            duration=duration,
            api_url=api_url,
        )

    except Exception as e:
        logger.error(f"Failed to search for song: {e}", exc_info=True)
        return None
