"""Music MCP tools: registered with a container-injected MusicPlayer, without using the global get_instance."""

from typing import Any, Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .music_player import MusicPlayer

logger = get_logger()


def register_music_tools(
    add_tool: Callable[[McpTool], None], player: MusicPlayer
) -> None:
    """Register the music tools with McpServer (the closure holds the container-injected player)."""

    async def search_and_play(args: dict[str, Any]) -> str:
        song_name = (args or {}).get("song_name", "")
        result = await player.search_and_play(song_name)
        return result.get("message", "Search and playback complete")

    async def pause(args: dict[str, Any]) -> str:
        # Default to manual on the MCP tool side; TTS pauses go through the EventBus, not this tool
        result = await player.pause(source="manual")
        return result.get("message", "Paused")

    async def resume(args: dict[str, Any]) -> str:
        result = await player.resume()
        return result.get("message", "Playback resumed")

    async def stop(args: dict[str, Any]) -> str:
        result = await player.stop()
        return result.get("message", "Stop playback complete")

    async def seek(args: dict[str, Any]) -> str:
        percent = int(args.get("percent", -1))
        position = int(args.get("position", -1))
        kwargs: dict[str, Any] = {}
        if percent >= 0:
            kwargs["percent"] = percent
        elif position >= 0:
            kwargs["position"] = position
        else:
            return "Specify percent (0-100) or position (seconds)"
        result = await player.seek(**kwargs)
        return result.get("message", "Seek complete")

    async def get_status(args: dict[str, Any]) -> str:
        result = await player.get_status()
        return result.get("message", "Cannot get status")

    async def get_lyrics(args: dict[str, Any]) -> str:
        result = await player.get_lyrics()
        if result.get("status") == "success":
            lyrics = result.get("lyrics", [])
            return "Lyrics content:\n" + "\n".join(lyrics)
        return result.get("message", "Failed to get lyrics")

    async def get_local_playlist(args: dict[str, Any]) -> str:
        force_refresh = args.get("force_refresh", False)
        result = await player.get_local_playlist(force_refresh)
        if result.get("status") == "success":
            playlist = result.get("playlist", [])
            total_count = result.get("total_count", 0)
            if playlist:
                text = f"Local music playlist ({total_count} track(s)):\n"
                text += "\n".join(playlist)
                return text
            return "No music files in local cache"
        return result.get("message", "Failed to get local playlist")

    async def list_opus_songs(args: dict[str, Any]) -> str:
        cursor = int(args.get("cursor", 0) or 0)
        result = await player.list_opus_songs(cursor)
        return result.get("message", "Failed to get the song catalog")

    async def search_opus_songs(args: dict[str, Any]) -> str:
        query = (args or {}).get("query", "")
        result = await player.search_opus_songs(query)
        return result.get("message", "Search failed")

    async def play_opus_song(args: dict[str, Any]) -> str:
        url = (args or {}).get("url", "")
        result = await player.play_opus_song(url)
        return result.get("message", "Playback failed")

    tools: list[McpTool] = [
        McpTool(
            "music_player.search_and_play",
            (
                "Search for and play the specified song. Searches for the song online by name and starts playback automatically. "
                "If music is already playing, it stops the current track and plays the new song. "
                "Used to play a specific song requested by the user, e.g. 'play Dao Xiang by Jay Chou', 'listen to Gu Yong Zhe'."
            ),
            PropertyList([Property("song_name", PropertyType.STRING)]),
            search_and_play,
        ),
        McpTool(
            "music_player.pause",
            (
                "Pause the music currently playing, keeping the playback position; you can resume afterwards. "
                "When the user says 'pause the music', 'stop the music for a moment', 'music pause', you must call this tool. "
                "Important: call this tool before replying to the user, otherwise the music will resume automatically after TTS ends."
            ),
            PropertyList(),
            pause,
        ),
        McpTool(
            "music_player.resume",
            (
                "Resume the previously paused music, continuing from the paused position. "
                "Call this when the user says 'keep playing', 'resume the music', 'turn the music back on'. "
                "Note: the music pauses automatically while TTS is speaking and resumes automatically afterwards; no need to call this tool. "
                "It is only needed when the user paused manually and then asks to resume."
            ),
            PropertyList(),
            resume,
        ),
        McpTool(
            "music_player.stop",
            (
                "Fully stop and close music playback, resetting to the beginning."
                "When the user says 'close the music', 'stop the music', 'I don't want to listen anymore', 'turn it off', 'shut down the music', you must call this tool. "
                "Difference from pause: stop closes playback completely, while pause is a temporary pause that can be resumed."
            ),
            PropertyList(),
            stop,
        ),
        McpTool(
            "music_player.seek",
            (
                "[Progress/seek only] Jump to a specified position in the current song. "
                "When the user says 'jump to 30%', 'seek 20%', 'jump to the middle': you must use percent (0-100); "
                "the player converts it to seconds based on the total duration — do not look up the lyrics and do not guess the position. "
                "When the user says 'jump to 2 minutes', 'jump to 90 seconds', 'go back to the beginning': use position (seconds, starting from 0). "
                "For 'fast forward 30 seconds': first call get_status to get the current position, then position = current seconds + 30. "
                "Unrelated to get_lyrics; seeking by percentage does not need the lyrics."
            ),
            PropertyList(
                [
                    Property("percent", PropertyType.INTEGER, default_value=-1),
                    Property("position", PropertyType.INTEGER, default_value=-1),
                ]
            ),
            seek,
        ),
        McpTool(
            "music_player.get_status",
            (
                "Query the current playback state: song name, whether it is playing/paused, total duration (seconds), current position (seconds), and progress percentage. "
                "Used for 'where is it playing now', 'how long is this song', 'check the progress before fast forwarding', etc. "
                "Do not use this tool in place of seek; call seek to jump."
            ),
            PropertyList(),
            get_status,
        ),
        McpTool(
            "music_player.get_lyrics",
            (
                "Only used to get the lyrics text for the current song."
                "Call this when the user asks 'what are the lyrics', 'what is being sung'. "
                "Do not use it for: seeking, jumping to a percentage, fast forward/rewind, or computing the playback position — "
                "for those, use music_player.seek (percent for percentages)."
            ),
            PropertyList(),
            get_lyrics,
        ),
        McpTool(
            "music_player.get_local_playlist",
            (
                "Get the local music playlist. Displays all downloaded and cached songs. "
                "Return format: 'song name - artist', for example 'Ju Hua Tai - Jay Chou'. "
                "Used when the user asks 'what songs do I have', 'local song list', 'what music is cached', etc. "
                "Note: for songs in the playlist, call search_and_play with just the song name; "
                "for example if the list shows 'Ju Hua Tai - Jay Chou', call search_and_play(song_name='Ju Hua Tai')."
            ),
            PropertyList(
                [Property("force_refresh", PropertyType.BOOLEAN, default_value=False)]
            ),
            get_local_playlist,
        ),
        McpTool(
            "music_player.list_opus_songs",
            (
                "List the online song catalog page by page (10 tracks per page). "
                "Each entry shows the track number, title, artist, duration, and its playback url. "
                "Call it again with cursor to get the next page. "
                "Used when the user asks 'what songs are available', 'show me the song list', 'what can you play'."
            ),
            PropertyList([Property("cursor", PropertyType.INTEGER, default_value=0)]),
            list_opus_songs,
        ),
        McpTool(
            "music_player.search_opus_songs",
            (
                "Search the online song catalog by keyword (title or artist, fuzzy match, up to 5 hits). "
                "Each result shows the title, artist, duration, and its playback url. "
                "Used when the user asks 'do you have song X', 'search for song X', 'is Y in the catalog'. "
                "To actually play a result, call music_player.play_opus_song with the url from the result."
            ),
            PropertyList([Property("query", PropertyType.STRING)]),
            search_opus_songs,
        ),
        McpTool(
            "music_player.play_opus_song",
            (
                "Play a track from the online song catalog by its playback url "
                "(the url returned by list_opus_songs or search_opus_songs). "
                "If music is already playing, it stops the current track first. "
                "Note: this is for catalog urls only; to play a song by name use music_player.search_and_play."
            ),
            PropertyList([Property("url", PropertyType.STRING)]),
            play_opus_song,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d music MCP tools (MusicPlayer injected by container)", len(tools))
