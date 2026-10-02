"""TTS/音乐分队列混音回归测试.

覆盖：PcmFifo 采样级语义、_pull_mixed 混音/闪避/削波。
背景：TTS 与音乐曾共用一条 FIFO，逐句 TTS 时帧交错导致"同时播放+断续"。
"""

from types import SimpleNamespace

import numpy as np
import pytest

from src.utils.config_manager import initialize_config

try:
    initialize_config()
except Exception:
    pass

from src.audio_codecs import stream_manager as stream_manager_module  # noqa: E402
from src.audio_codecs.audio_buffer import PcmFifo  # noqa: E402
from src.audio_codecs.audio_codec import (  # noqa: E402
    _MUSIC_DUCK_GAIN,
    AudioCodec,
)
from src.audio_codecs.stream_manager import AudioStreamManager  # noqa: E402
from src.constants.constants import AudioConfig  # noqa: E402
from src.constants.constants import DeviceState  # noqa: E402
from src.core.event_bus import EventBus  # noqa: E402
from src.mcp.tools.music.bus import MusicEventBridge  # noqa: E402
from src.plugins.audio import AudioPlugin  # noqa: E402
from src.plugins.audio import _is_explicit_music_stop_command  # noqa: E402
from src.utils.audio_device import DeviceConfig  # noqa: E402


@pytest.mark.parametrize(
    "text",
    ["stop lagu", "tolong matikan musiknya", "please stop the music", "turn off music"],
)
def test_explicit_music_stop_commands_are_detected(text):
    assert _is_explicit_music_stop_command(text)


@pytest.mark.parametrize(
    "text",
    ["jangan stop lagu", "lagu ini bagus", "cara menghentikan musik", "stop the conversation about music"],
)
def test_non_command_text_does_not_stop_music(text):
    assert not _is_explicit_music_stop_command(text)


@pytest.mark.asyncio
async def test_stt_stop_command_stops_music_through_event_bus():
    event_bus = EventBus()
    engine = SimpleNamespace(is_playing=True)

    class PlayerStub:
        async def stop(self):
            engine.is_playing = False

    bridge = MusicEventBridge(engine, PlayerStub())
    bridge.set_event_bus(event_bus)
    plugin = AudioPlugin()
    plugin._ctx = SimpleNamespace(event_bus=event_bus)

    await plugin.on_incoming_json({"type": "stt", "text": "stop lagu"})

    assert engine.is_playing is False


@pytest.mark.asyncio
@pytest.mark.parametrize("release_on_tts_stop", [False, True])
async def test_music_is_muted_during_listening_then_resumes(release_on_tts_stop):
    event_bus = EventBus()
    engine = SimpleNamespace(is_playing=True, paused=False, pause_source=None)

    class PlayerStub:
        async def pause(self, source):
            engine.paused = True
            engine.pause_source = source

        async def resume(self):
            engine.paused = False
            engine.pause_source = None

    bridge = MusicEventBridge(engine, PlayerStub())
    bridge.set_event_bus(event_bus)

    class CodecStub:
        aec_active = False

        def __init__(self):
            self.music_muted = False

        def is_tts_playing(self):
            return False

        def set_music_muted(self, muted):
            self.music_muted = muted

    codec = CodecStub()
    config = SimpleNamespace(get_config=lambda _path, default=None: default)
    plugin = AudioPlugin()
    plugin.codec = codec
    plugin._ctx = SimpleNamespace(event_bus=event_bus, get_config=lambda: config)

    await plugin.on_device_state_changed(DeviceState.LISTENING)
    assert codec.music_muted is True
    assert engine.paused is True

    if release_on_tts_stop:
        await plugin.on_incoming_json({"type": "tts", "state": "stop"})
    else:
        await plugin.on_device_state_changed(DeviceState.IDLE)
    assert codec.music_muted is False
    assert engine.paused is False


class TestPcmFifo:
    def test_pull_basic(self):
        f = PcmFifo(1000)
        f.push(np.ones(300, dtype=np.float32))
        out = f.pull(200)
        assert out.shape == (200,)
        assert np.all(out == 1)
        assert f.size == 100

    def test_pull_pads_zeros_when_short(self):
        f = PcmFifo(1000)
        f.push(np.ones(100, dtype=np.float32))
        out = f.pull(200)
        assert np.all(out[:100] == 1)
        assert np.all(out[100:] == 0)
        assert f.size == 0

    def test_pull_empty_returns_none(self):
        f = PcmFifo(1000)
        assert f.pull(10) is None

    def test_push_drops_oldest_over_capacity(self):
        f = PcmFifo(1000)
        f.push(np.zeros(600, dtype=np.float32))
        f.push(np.ones(600, dtype=np.float32))
        assert f.size == 1000
        assert f.dropped == 200
        out = f.pull(1000)
        # 最旧的 200 个 0 被丢掉，剩 400 个 0 + 600 个 1
        assert np.all(out[:400] == 0)
        assert np.all(out[400:] == 1)

    def test_clear(self):
        f = PcmFifo(1000)
        f.push(np.ones(500, dtype=np.float32))
        assert f.clear() == 500
        assert f.size == 0
        assert f.pull(10) is None

    def test_multichannel_flattened(self):
        f = PcmFifo(1000)
        f.push(np.ones((100, 2), dtype=np.float32))
        assert f.size == 200


class TestMixing:
    @pytest.fixture()
    def codec(self):
        return AudioCodec()

    def test_music_only_full_gain(self, codec):
        n = codec._mix_chunk
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert np.allclose(out, 0.4)

    def test_muted_music_does_not_mask_tts(self, codec):
        n = codec._mix_chunk
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        codec._tts_fifo.push(np.full(n, 0.5, dtype=np.float32))
        codec.set_music_muted(True)

        out = codec._pull_mixed(n)

        assert np.allclose(out, 0.5)
        assert codec._music_fifo.size == 0

    def test_tts_only(self, codec):
        n = codec._mix_chunk
        codec._tts_fifo.push(np.full(n, 0.5, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert np.allclose(out, 0.5)

    def test_tts_and_music_mixed_with_duck(self, codec):
        n = codec._mix_chunk
        codec._tts_fifo.push(np.full(n, 0.5, dtype=np.float32))
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert np.allclose(out, 0.5 + _MUSIC_DUCK_GAIN * 0.4)

    def test_duck_hangover_then_recover(self, codec):
        n = codec._mix_chunk
        # 一次 TTS 后，音乐在 hangover 期内仍闪避
        codec._tts_fifo.push(np.full(n, 0.5, dtype=np.float32))
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        codec._pull_mixed(n)

        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert np.allclose(out, _MUSIC_DUCK_GAIN * 0.4)

        # hangover 耗尽后恢复全量
        for _ in range(15):
            codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
            out = codec._pull_mixed(n)
        assert np.allclose(out, 0.4)

    def test_clip_protection(self, codec):
        n = codec._mix_chunk
        codec._tts_fifo.push(np.full(n, 0.9, dtype=np.float32))
        codec._music_fifo.push(np.full(n, 0.9, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert out.max() <= 1.0

    def test_both_empty_returns_none(self, codec):
        assert codec._pull_mixed(codec._mix_chunk) is None

    def test_clear_semantics_are_independent(self, codec):
        n = codec._mix_chunk
        codec._tts_fifo.push(np.full(n, 0.5, dtype=np.float32))
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        codec._tts_fifo.clear()
        # TTS 清空不影响音乐
        assert codec._music_fifo.size == n

    @pytest.mark.asyncio
    async def test_music_writer_waits_for_backlog_to_drain(self, monkeypatch):
        codec = AudioCodec.__new__(AudioCodec)
        codec._is_closing = False
        codec._closed = True
        codec.stream_manager = None
        target = int(AudioConfig.OUTPUT_SAMPLE_RATE * 0.30)

        class DelayedDrain:
            size = target + 1
            pushed = False

            def push(self, _samples):
                self.pushed = True

        fifo = DelayedDrain()
        codec._music_fifo = fifo
        sleeps = 0

        async def fake_sleep(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 105:
                fifo.size = target

        monkeypatch.setattr("src.audio_codecs.audio_codec.asyncio.sleep", fake_sleep)

        await codec.write_pcm_direct(np.zeros(480, dtype=np.float32))

        assert fifo.pushed
        assert sleeps == 105


def test_output_stream_uses_host_selected_blocksize(monkeypatch):
    calls = {"input": [], "output": []}

    class FakeStream:
        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    def fake_stream(kind):
        def create(**kwargs):
            calls[kind].append(kwargs)
            return FakeStream()

        return create

    monkeypatch.setattr(stream_manager_module.sd, "InputStream", fake_stream("input"))
    monkeypatch.setattr(stream_manager_module.sd, "OutputStream", fake_stream("output"))

    device_config = DeviceConfig(
        input_device_id=1,
        output_device_id=2,
        input_sample_rate=16000,
        output_sample_rate=48000,
        input_channels=1,
        output_channels=2,
        input_frame_size=320,
        output_frame_size=960,
    )
    manager = AudioStreamManager(device_config)

    def callback(*args):
        pass

    manager.create_streams(callback, callback)
    assert calls["input"][0]["blocksize"] == device_config.input_frame_size
    assert calls["output"][0]["blocksize"] == 0

    assert manager.reinitialize_stream(False, output_callback=callback)
    assert calls["output"][1]["blocksize"] == 0
