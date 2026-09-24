"""Music playback session (public entry point).

Combination:
- PlaybackEngine — decode queue / start, stop, pause, seek
- MusicEventBridge — EventBus binding and state/lyrics emission

The state fields live on the engine; this class only keeps the properties commonly used in production (is_playing / paused / current_song).
"""

from __future__ import annotations

import time
from pathlib import Path

from src.audio_codecs.music_decoder import MusicDecoder
from src.logging import get_logger

from .bus import MusicEventBridge
from .cache import MusicCache
from .config import load_music_config
from .download import MusicDownloader
from .local_library import LocalLibrary
from .lyrics import fetch_kuwo_lyrics, format_lyric_display, lyric_at
from .online_search import search_song
from .playback import PlaybackDeps, PlaybackEngine

logger = get_logger()


class MusicPlayer:
    def __init__(self):
        self._config: dict | None = None
        self._cache = MusicCache()
        self._downloader = MusicDownloader(self._cache)
        self._library = LocalLibrary(self._cache)

        self.current_url = ""
        self.lyrics: list[tuple[float, str]] = []

        self._engine = PlaybackEngine(
            PlaybackDeps(
                cache=self._cache,
                downloader=self._downloader,
                library=self._library,
                hooks=self,
            )
        )
        self._bus = MusicEventBridge(self._engine, self)

        logger.debug("MusicPlayer instance created")

    # ----- PlaybackEngine hooks -----

    def prepare_for_io(self) -> None:
        self.reload_config()
        self._cache.prepare()

    def is_speaking(self) -> bool:
        ctx = self._bus.plugin_ctx
        return bool(ctx and ctx.is_speaking())

    def format_time(self, seconds: float) -> str:
        return self._format_time(seconds)

    async def tick_lyrics(self) -> None:
        await self._tick_lyrics()

    async def emit_state_change(
        self,
        state: str,
        song_name: str | None = None,
        position: float | None = None,
    ) -> None:
        await self._bus.emit_state_change(state, song_name, position)

    # ----- State commonly used in production (flat access) -----

    @property
    def cache_dir(self) -> Path:
        return self._cache.root

    @property
    def temp_cache_dir(self) -> Path:
        return self._cache.temp_dir

    @property
    def is_playing(self) -> bool:
        return self._engine.is_playing

    @property
    def paused(self) -> bool:
        return self._engine.paused

    @property
    def current_song(self) -> str:
        return self._engine.current_song

    @property
    def config(self) -> dict:
        if self._config is None:
            self.reload_config()
        return self._config

    def reload_config(self) -> dict:
        self._config = load_music_config()
        self._downloader.set_config(self._config)
        logger.debug(
            "MusicPlayer config loaded: "
            f"search={self._config['SEARCH_URL']}, "
            f"direct-link={self._config['URL_API']}, "
            f"platform={self._config['DEFAULT_SOURCE']}"
        )
        return self._config

    # ----- Lifecycle / play delegation -----

    def set_event_bus(self, event_bus, plugin_ctx=None) -> None:
        self._bus.set_event_bus(event_bus, plugin_ctx)

    def detach(self) -> None:
        self._bus.detach()

    async def stop(self) -> dict:
        return await self._engine.stop()

    async def pause(self, source: str = "manual") -> dict:
        return await self._engine.pause(source=source)

    async def resume(self) -> dict:
        return await self._engine.resume()

    async def seek(
        self,
        position: float | None = None,
        percent: float | None = None,
    ) -> dict:
        return await self._engine.seek(position=position, percent=percent)

    async def get_position(self):
        return await self._engine.get_position()

    async def get_progress(self):
        return await self._engine.get_progress()

    # ----- Song search / local library -----

    async def get_local_playlist(self, force_refresh: bool = False) -> dict:
        self.prepare_for_io()
        return self._library.get_playlist(force_refresh)

    async def search_local_music(self, query: str) -> dict:
        self.prepare_for_io()
        return self._library.search(query)

    async def play_local_song_by_id(self, file_id: str) -> dict:
        eng = self._engine
        try:
            self.prepare_for_io()
            resolved = self._library.resolve(file_id)
            if resolved is None:
                return {"status": "error", "message": f"Local file does not exist: {file_id}"}}

            file_path, metadata = resolved
            eng.current_song = metadata.display_name()
            eng.song_id = file_id
            eng.total_duration = metadata.duration or 0
            self.current_url = str(file_path)
            self.lyrics = []

            duration = await MusicDecoder.get_duration(file_path)
            if duration > 0:
                eng.total_duration = duration
                logger.info(f"Got exact duration from audio file: {duration:.2f}s")
            elif eng.total_duration == 0:
                logger.warning("Could not get audio duration")

            success = await eng.start_playback(file_path)
            if success:
                return {
                    "status": "success",
                    "message": f"Now playing: {eng.current_song}",
                    "song": eng.current_song,
                    "duration": self._format_time(eng.total_duration),
                    "total_seconds": eng.total_duration,
                }
            return {"status": "error", "message": "Playback failed"}
        except Exception as e:
            logger.error(f"Failed to play local music: {e}", exc_info=True)
            return {"status": "error", "message": f"Playback failed: {str(e)}"}

    async def search_and_play(self, song_name: str) -> dict:
        eng = self._engine
        try:
            self.prepare_for_io()
            hit = await search_song(song_name, self.config)
            if hit is None:
                return {"status": "error", "message": f"Song not found: {song_name}"}}

            eng.current_song = hit.display_name
            eng.song_id = hit.song_id
            eng.total_duration = hit.duration
            self.current_url = hit.api_url
            await self._fetch_lyrics(hit.song_id)

            success = await eng.play_url(hit.api_url)
            if success:
                return {
                    "status": "success",
                    "message": f"Now playing: {eng.current_song}",
                    "song": eng.current_song,
                    "duration": self._format_time(eng.total_duration),
                    "total_seconds": eng.total_duration,
                }

            detail = self._downloader.last_error or "Unknown reason"
            return {"status": "error", "message": f"Playback failed: {detail}"}
        except Exception as e:
            logger.error(f"Search-and-play failed: {e}", exc_info=True)
            return {"status": "error", "message": f"Operation failed: {str(e)}"}}

    async def get_lyrics(self) -> dict:
        if not self.lyrics:
            return {"status": "info", "message": "No lyrics for current song", "lyrics": []}
        lines = [f"[{self._format_time(t)}] {text}" for t, text in self.lyrics]
        return {
            "status": "success",
            "message": f"Got {len(self.lyrics)} lyric line(s)",
            "lyrics": lines,
        }

    async def get_status(self) -> dict:
        eng = self._engine
        position = await eng.get_position()
        progress = await eng.get_progress()
        if not eng.is_playing:
            playing_state = "Not playing"
        elif eng.paused and eng.pause_source == "manual":
            playing_state = "Paused"
        elif eng.is_playing:
            playing_state = "Playing"
        else:
            playing_state = "Unknown"

        return {
            "status": "success",
            "message": (
                f"Current song: {eng.current_song}\n"
                f"Playback state: {playing_state}\n"
                f"Pause source: {eng.pause_source or 'none'} (tts = temporarily paused while speaking)\n"
                f"Total duration (s): {int(eng.total_duration)}\n"
                f"Current position (s): {int(position)}\n"
                f"Duration: {self._format_time(eng.total_duration)}\n"
                f"Current position: {self._format_time(position)}\n"
                f"Progress: {progress}%\n"
                f"Lyrics available: {'yes' if len(self.lyrics) > 0 else 'no'}\n"
                f"Tip: to jump to a percentage, call seek(percent=N); do not infer the position from the lyrics"
            ),
        }

    # ----- Lyrics -----

    async def _tick_lyrics(self) -> None:
        if not self.lyrics:
            return
        eng = self._engine
        now = time.time()
        if now - eng.last_lyric_tick < 0.2:
            return
        eng.last_lyric_tick = now

        position = now - eng.start_play_time if eng.start_play_time > 0 else 0.0
        hit = lyric_at(self.lyrics, position)
        if hit is None:
            return
        idx, text = hit
        if idx == eng.current_lyric_index:
            return
        eng.current_lyric_index = idx
        display = format_lyric_display(text, position, eng.total_duration)
        await self._bus.emit_lyrics_update(display, self.lyrics[idx][0])
        logger.debug(f"Showing lyrics: {text}")

    async def _fetch_lyrics(self, song_id: str):
        eng = self._engine
        self.lyrics = await fetch_kuwo_lyrics(
            song_id,
            lyrics_url=self.config["LYRICS_URL"],
            headers=self.config["HEADERS"],
        )
        if eng.total_duration == 0 and self.lyrics:
            last_time, _ = self.lyrics[-1]
            eng.total_duration = last_time + 5.0
            logger.info(f"Extracted song duration from lyrics: {eng.total_duration}s")

    def _format_time(self, seconds: float) -> str:
        minutes = int(seconds) // 60
        secs = int(seconds) % 60
        return f"{minutes:02d}:{secs:02d}"

    def __del__(self):
        try:
            cache = getattr(self, "_cache", None)
            if cache is not None and getattr(cache, "_ready", False):
                cache.clean_temp()
        except Exception as e:
            logger.debug(f"__del__ failed to clean temp cache: {e}")
