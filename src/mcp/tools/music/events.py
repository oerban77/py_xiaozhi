"""Music player event data type definitions.

Data structures used for EventBus event communication.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MusicStateData:
    """Music state change event data.

    Attributes:
        state: playback state ("playing", "paused", "stopped", "completed")
        song: song name
        position: current playback position (seconds)
        duration: total duration (seconds)
        pause_source: pause source ("tts", "manual", "external", None)
    """

    state: str
    song: str
    position: float
    duration: float
    pause_source: Optional[str] = None


@dataclass
class MusicLyricsData:
    """Lyrics update event data.

    Attributes:
        text: lyrics text
        time_sec: timestamp (seconds)
        song_id: song ID (optional)
    """

    text: str
    time_sec: float
    song_id: Optional[str] = None


@dataclass
class MusicControlRequest:
    """Music control request data.

    Attributes:
        source: request source ("tts", "manual", "external", etc.)
    """

    source: str = "external"
