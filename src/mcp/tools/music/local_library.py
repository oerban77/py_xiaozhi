"""Local cached music library: scan, list, search."""

from __future__ import annotations

import time
from pathlib import Path

from src.logging import get_logger

from .cache import MusicCache
from .metadata import MUTAGEN_AVAILABLE, MusicMetadata

logger = get_logger()


class LocalLibrary:
    """Local listing built around MusicCache."""

    def __init__(self, cache: MusicCache):
        self._cache = cache
        self._playlist: list[MusicMetadata] | None = None
        self._last_scan_time = 0.0

    def invalidate(self) -> None:
        self._playlist = None
        self._last_scan_time = 0.0

    def scan(self, force_refresh: bool = False) -> list[MusicMetadata]:
        now = time.time()
        if (
            not force_refresh
            and self._playlist is not None
            and (now - self._last_scan_time) < 300
        ):
            return self._playlist

        self._cache.prepare()
        playlist: list[MusicMetadata] = []
        music_files = self._cache.list_music_files()
        if not music_files and not self._cache.root.exists():
            logger.warning(f"Cache directory does not exist: {self._cache.root}")
            return playlist

        logger.debug(f"Found {len(music_files)} music file(s)")

        for file_path in music_files:
            try:
                metadata = MusicMetadata(file_path)
                if MUTAGEN_AVAILABLE:
                    metadata.extract_metadata()
                playlist.append(metadata)
            except Exception as e:
                logger.debug(f"Failed to process music file {file_path.name}: {e}")

        playlist.sort(key=lambda x: (x.artist or "Unknown", x.title or x.filename))
        self._playlist = playlist
        self._last_scan_time = now
        logger.info(f"Scan complete; found {len(playlist)} local tracks")
        return playlist

    def get_playlist(self, force_refresh: bool = False) -> dict:
        try:
            playlist = self.scan(force_refresh)
            if not playlist:
                return {
                    "status": "info",
                    "message": "No music files in local cache",
                    "playlist": [],
                    "total_count": 0,
                }

            formatted = [m.display_name() for m in playlist]
            return {
                "status": "success",
                "message": f"Found {len(playlist)} local track(s)",
                "playlist": formatted,
                "total_count": len(playlist),
            }
        except Exception as e:
            logger.error(f"Failed to get local playlist: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Failed to get local playlist: {str(e)}",
                "playlist": [],
                "total_count": 0,
            }

    def search(self, query: str) -> dict:
        try:
            playlist = self.scan()
            if not playlist:
                return {
                    "status": "info",
                    "message": "No music files in local cache",
                    "results": [],
                    "found_count": 0,
                }

            q = (query or "").lower()
            results = []
            for metadata in playlist:
                searchable = " ".join(
                    filter(
                        None,
                        [
                            metadata.title,
                            metadata.artist,
                            metadata.album,
                            metadata.filename,
                        ],
                    )
                ).lower()
                if q in searchable:
                    results.append(
                        {
                            "song_info": metadata.display_name(),
                            "file_id": metadata.file_id,
                            "duration": metadata.format_duration(),
                        }
                    )

            return {
                "status": "success",
                "message": f"Found {len(results)} matching track(s) in local music",
                "results": results,
                "found_count": len(results),
            }
        except Exception as e:
            logger.error(f"Failed to search local music: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Search failed: {str(e)}",
                "results": [],
                "found_count": 0,
            }

    def resolve(self, file_id: str) -> tuple[Path, MusicMetadata] | None:
        """Find a file by id and read its metadata."""
        self._cache.prepare()
        path = self._cache.find_song_file(file_id)
        if path is None:
            return None
        metadata = MusicMetadata(path)
        if MUTAGEN_AVAILABLE:
            metadata.extract_metadata()
        return path, metadata
