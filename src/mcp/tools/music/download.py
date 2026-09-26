"""Music download: resolve direct links; prefetch to the local cache with a background FFmpeg copy."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import requests

from src.logging import get_logger
from src.utils.resource_finder import get_ffmpeg_path

from .cache import MusicCache

logger = get_logger()

# If direct-link fails, fall back to the official Kuwo preview
_KUWO_PLAYURL = "https://wapi.kuwo.cn/api/v1/www/music/playUrl"
_QUALITY_FALLBACKS = ("320k", "128k")

_SUBPROCESS_KW = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


class MusicDownloader:
    """Resolve the play URL; optionally copy the whole track to the cache in the background (at network speed, not tied to the play position)."""

    def __init__(self, cache: MusicCache, config: dict[str, Any] | None = None) -> None:
        self._cache = cache
        self._config = config or {}
        # Last failure reason for higher-level hints
        self.last_error: str | None = None
        self._prefetch_task: asyncio.Task | None = None
        self._prefetch_song_id: str | None = None

    def set_config(self, config: dict[str, Any]) -> None:
        self._config = config

    async def get_or_download(
        self,
        song_id: str,
        api_url: str,
        *,
        filename: str | None = None,
    ) -> Path | None:
        """Use the cache directly; do not re-download."""
        self._cache.prepare()
        name = filename or f"{song_id}.mp3"
        hit = self._cache.find_song_file(song_id)
        if hit is not None:
            logger.info(f"Using cache: {hit}")
            return hit

        cache_path = self._cache.root / name
        if cache_path.exists():
            logger.info(f"Using cache: {cache_path}")
            return cache_path

        return await self.download(api_url, name, song_id=song_id)

    @staticmethod
    def _extract_url_from_payload(data: Any) -> str | None:
        # JSON fields differ between providers; try to extract the url
        if not isinstance(data, dict):
            return None

        real_url = data.get("url")
        if isinstance(real_url, str) and real_url.startswith("http"):
            return real_url

        inner = data.get("data")
        if isinstance(inner, dict):
            nested = inner.get("url")
            if isinstance(nested, str) and nested.startswith("http"):
                return nested
        elif isinstance(inner, str) and inner.startswith("http"):
            return inner

        return None

    @staticmethod
    def _describe_api_failure(data: Any) -> str:
        if not isinstance(data, dict):
            return "The direct-link API returned unparseable data"

        code = data.get("code")
        msg = str(data.get("msg") or data.get("message") or "").strip()

        # Common lx-music-api codes
        if code == 1 or "bulk downloads are blocked" in msg or "block ip" in msg.lower():
            return (
                "The direct-link API has banned the current IP (bulk downloads are blocked). "
                "Switch network/IP, or change MUSIC.URL_API in the settings"
            )
        if code == 5 or "too many" in msg.lower():
            return "Direct-link API requests are too frequent; please try again later"
        if code == 2:
            return "The direct-link API failed to get the play URL (no source in the catalog or resolution failed)"
        if code == 4:
            return "Direct-link API internal error"
        if code == 6:
            return "Direct-link API parameter error"

        if msg:
            return f"Direct-link API failure: {msg}"
        return f"The direct-link API did not return a play URL: {data}"

    def _lx_headers(self) -> dict[str, str]:
        # Match Huibq/keep-alive render_api.js: only Key + UA are required
        return {
            "X-Request-Key": self._config.get("URL_API_KEY", "share-v3"),
            "User-Agent": "lx-music-request",
            "Content-Type": "application/json",
        }

    def _browser_headers(self) -> dict[str, str]:
        # Kuwo official playUrl used
        headers = dict(self._config.get("HEADERS") or {})
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        headers.setdefault("Referer", "https://www.kuwo.cn/")
        headers.setdefault("Accept", "application/json, text/plain, */*")
        return headers

    def media_headers(self, media_url: str) -> dict[str, str]:
        """Request headers for CDN / FFmpeg streaming playback (not the JSON API set)."""
        host = (urlparse(media_url).hostname or "").lower()
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        headers = {
            "User-Agent": ua,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
        if "kuwo" in host or "sycdn" in host or "bd-lv" in host:
            headers["Referer"] = "https://www.kuwo.cn/"
            headers["Origin"] = "https://www.kuwo.cn"
        return headers

    # Old name
    def _download_headers(self, download_url: str) -> dict[str, str]:
        return self.media_headers(download_url)

    async def _fetch_json(
        self, url: str, headers: dict[str, str], *, timeout: int = 15
    ) -> Any | None:
        try:
            response = await asyncio.to_thread(
                requests.get, url, headers=headers, timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(
                f"Request failed {urlparse(url).netloc}: {e}", exc_info=True
            )
            return None

    def _candidate_lx_urls(self, api_url: str) -> list[str]:
        # Try the configured audio quality first, then try 128k
        urls = [api_url]
        m = re.search(r"/url/[^/]+/[^/]+/([^/?#]+)", api_url)
        if not m:
            return urls
        current_quality = m.group(1)
        for q in _QUALITY_FALLBACKS:
            if q == current_quality:
                continue
            alt = re.sub(
                r"(/url/[^/]+/[^/]+/)[^/?#]+",
                rf"\g<1>{q}",
                api_url,
                count=1,
            )
            if alt not in urls:
                urls.append(alt)
        return urls

    async def _resolve_via_lx_api(self, api_url: str) -> tuple[str | None, str | None]:
        last_reason: str | None = None
        headers = self._lx_headers()

        for candidate in self._candidate_lx_urls(api_url):
            logger.debug(f"Trying direct-link API: {candidate}")
            data = await self._fetch_json(candidate, headers)
            if data is None:
                last_reason = "Direct-link API network request failed"
                continue

            real_url = self._extract_url_from_payload(data)
            if real_url:
                logger.info(f"Direct-link API resolved: {real_url[:80]}...")
                return real_url, None

            last_reason = self._describe_api_failure(data)
            logger.warning(f"Direct-link API returned no URL: {data}")

            # If the IP is blocked, changing quality won't help
            if "ban" in (last_reason or "") or "bulk downloads are blocked" in str(data):
                break

        return None, last_reason

    async def _resolve_via_kuwo_official(
        self, song_id: str
    ) -> tuple[str | None, str | None]:
        # Free songs can be played; paid songs will be rejected by the official API
        if not song_id or song_id == "unknown":
            return None, "Missing song ID; cannot fall back to the official endpoint"

        headers = self._browser_headers()
        last_reason: str | None = None

        for br in ("320kmp3", "128kmp3"):
            url = f"{_KUWO_PLAYURL}?mid={song_id}&type=music&httpsStatus=1&br={br}"
            logger.debug(f"Trying official Kuwo playUrl: mid={song_id} br={br}")
            data = await self._fetch_json(url, headers)
            if data is None:
                last_reason = "Kuwo official API network request failed"
                continue

            real_url = self._extract_url_from_payload(data)
            if real_url:
                logger.info(f"Official Kuwo endpoint resolved: {real_url[:80]}...")
                return real_url, None

            msg = ""
            if isinstance(data, dict):
                msg = str(data.get("msg") or data.get("message") or "").strip()
            if "paid" in msg:
                last_reason = f"This song is paid content; the official endpoint cannot play a preview ({msg})"
                break
            last_reason = msg or f"The official Kuwo endpoint did not return a URL: {data}"
            logger.warning(last_reason)

        return None, last_reason

    async def resolve_play_url(
        self, api_url: str, *, song_id: str | None = None
    ) -> str | None:
        # Direct link → lower quality → official API
        self.last_error = None
        reasons: list[str] = []

        try:
            real_url, reason = await self._resolve_via_lx_api(api_url)
            if real_url:
                return real_url
            if reason:
                reasons.append(reason)

            # Extract the song id from the url path when not provided
            sid = song_id
            if not sid or sid == "unknown":
                m = re.search(r"/url/[^/]+/([^/]+)/", api_url)
                if m:
                    sid = m.group(1)

            if sid:
                real_url, reason = await self._resolve_via_kuwo_official(sid)
                if real_url:
                    return real_url
                if reason:
                    reasons.append(reason)

            self.last_error = "; ".join(reasons) if reasons else "Failed to parse playback URL"
            logger.error(f"Could not resolve playback URL: {self.last_error}")
            return None
        except Exception as e:
            self.last_error = f"Exception while resolving the play URL: {e}"
            logger.error(self.last_error, exc_info=True)
            return None

    def _sync_download(
        self, download_url: str, headers: dict, temp_path: Path, cache_path: Path
    ) -> Path:
        # CDN occasionally returns RemoteDisconnected; retry a couple of times
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                if temp_path.exists():
                    temp_path.unlink()
                with requests.get(
                    download_url,
                    headers=headers,
                    stream=True,
                    timeout=45,
                    allow_redirects=True,
                ) as response:
                    response.raise_for_status()
                    with open(temp_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if chunk:
                                f.write(chunk)
                if temp_path.stat().st_size <= 0:
                    raise IOError("Downloaded file is empty")
                shutil.move(str(temp_path), str(cache_path))
                return cache_path
            except (requests.RequestException, OSError) as e:
                last_err = e
                logger.warning(
                    f"Download retry {attempt + 1}/3 failed: {e}"
                )
        assert last_err is not None
        raise last_err

    async def download(
        self, api_url: str, filename: str, *, song_id: str | None = None
    ) -> Path | None:
        """Download and write to the cache directory."""
        self._cache.prepare()
        temp_path = None
        try:
            download_url = await self.resolve_play_url(api_url, song_id=song_id)
            if not download_url:
                return None

            temp_path = self._cache.temp_path(filename)
            cache_path = self._cache.root / filename
            headers = self.media_headers(download_url)
            logger.debug(
                f"Starting audio download: host={urlparse(download_url).hostname}"
            )

            result = await asyncio.to_thread(
                self._sync_download,
                download_url,
                headers,
                temp_path,
                cache_path,
            )
            logger.info(f"Music downloaded and cached: {result}")
            return result
        except Exception as e:
            self.last_error = f"Download failed: {e}"
            logger.error(self.last_error, exc_info=True)
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception as cleanup_e:
                    logger.debug(f"Failed to clean temp files: {cleanup_e}")
            return None

    def start_prefetch(
        self,
        media_url: str,
        song_id: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Copy the whole track to disk in the background with FFmpeg -c copy at network speed, not synced with playback progress.

        Similar to HTML audio buffering: pull the whole file as fast as possible while playing, so it may already be cached before the track finishes.
        Switching songs cancels the previous prefetch; pausing/stopping the current song does not (buffering continues).
        """
        if not song_id or song_id == "unknown":
            return
        self._cache.prepare()
        if self._cache.find_song_file(song_id) is not None:
            logger.debug(f"Cache exists; skipping prefetch: {song_id}")
            return

        # Same song already prefetched
        if (
            self._prefetch_task
            and not self._prefetch_task.done()
            and self._prefetch_song_id == song_id
        ):
            return

        self.cancel_prefetch()
        hdrs = dict(headers or self.media_headers(media_url))
        self._prefetch_song_id = song_id
        self._prefetch_task = asyncio.create_task(
            self._prefetch_copy_loop(media_url, song_id, hdrs),
            name=f"music:prefetch:{song_id}",
        )
        logger.info(f"Background prefetch cache: song_id={song_id}")

    def cancel_prefetch(self) -> None:
        """Cancel background prefetch (when switching songs)."""
        task = self._prefetch_task
        self._prefetch_task = None
        self._prefetch_song_id = None
        if task and not task.done():
            task.cancel()

    async def _prefetch_copy_loop(
        self, media_url: str, song_id: str, headers: dict[str, str]
    ) -> None:
        final = self._cache.path_for_song(song_id)
        part = final.with_name(f"{final.stem}.part{final.suffix}")
        try:
            if part.exists():
                part.unlink()
        except Exception:
            pass

        try:
            ok = await self._ffmpeg_copy_url(media_url, part, headers)
            if not ok:
                return
            if not part.exists() or part.stat().st_size <= 1024:
                logger.warning(f"Prefetch file too small; discarding: {part.name}")
                if part.exists():
                    part.unlink()
                return
            if final.exists():
                final.unlink()
            part.replace(final)
            logger.info(
                f"Background prefetch complete (playable locally): {final.name} ({final.stat().st_size} bytes)"
            )
        except asyncio.CancelledError:
            logger.debug(f"Background prefetch cancelled: {song_id}")
            try:
                if part.exists():
                    part.unlink()
            except Exception:
                pass
            raise
        except Exception as e:
            logger.warning(f"Background prefetch failed {song_id}: {e}", exc_info=True)
            try:
                if part.exists():
                    part.unlink()
            except Exception:
                pass
        finally:
            if self._prefetch_song_id == song_id:
                self._prefetch_task = None
                self._prefetch_song_id = None

    async def _ffmpeg_copy_url(
        self, media_url: str, out_path: Path, headers: dict[str, str]
    ) -> bool:
        """Fetch the whole track with FFmpeg -c copy at network speed; no decoding, not limited by the play position."""
        from src.audio_codecs.music_decoder import _ffmpeg_header_args

        ffmpeg = get_ffmpeg_path()
        cmd = [
            ffmpeg,
            "-y",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
        ]
        cmd.extend(_ffmpeg_header_args(headers))
        cmd.extend(
            [
                "-i",
                media_url,
                "-vn",
                "-c:a",
                "copy",
                "-loglevel",
                "error",
                str(out_path),
            ]
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **_SUBPROCESS_KW,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", errors="ignore").strip()
                logger.warning(
                    f"FFmpeg copy prefetch failed rc={proc.returncode}: {err[:300]}"
                )
                return False
            return True
        except FileNotFoundError:
            logger.warning("FFmpeg unavailable; skipping background prefetch")
            return False
        except Exception as e:
            logger.warning(f"FFmpeg copy prefetch error: {e}", exc_info=True)
            return False
