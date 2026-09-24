"""Music-related tools.

- music_player: play session (created by the container and injected into MCP; the only public entry point)
- playback / bus: PlaybackEngine / MusicEventBridge (internal composition, not a public API)
- cache / download / lyrics: caching, direct links, lyrics
- online_search / local_library / metadata: song search and the local library
- register_music_tools: register tools with McpServer (closure holds the player)
"""

from .music_player import MusicPlayer
from .register import register_music_tools

__all__ = [
    "MusicPlayer",
    "register_music_tools",
]
