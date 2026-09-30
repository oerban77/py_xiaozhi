import pytest

from src.mcp.tools.reminder import service as reminder_service
from src.plugins.audio import AudioPlugin


class DummyCodec:
    def __init__(self):
        self.calls = []

    async def write_audio(self, data):
        self.calls.append(data)


@pytest.mark.asyncio
async def test_on_incoming_audio_is_ignored_while_alarm_is_playing():
    plugin = AudioPlugin()
    plugin.codec = DummyCodec()
    reminder_service._alarm_playing.set()

    try:
        await plugin.on_incoming_audio(b"chunk-1")
        assert plugin.codec.calls == []
    finally:
        reminder_service._alarm_playing.clear()
