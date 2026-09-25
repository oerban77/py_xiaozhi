"""Opus stream playback: download [2-byte BE length][opus packet] frames and decode them.

Ported from the reference Xiaozhi desktop app (app/mcp/modules/music.py):
- framing: 2-byte big-endian length prefix followed by one Opus packet
- decode with the project's OpusCodec (16kHz WB / 60ms frames) and feed the
  AudioCodec music path as float32 PCM

Differences from the original:
- the original decoded to int16 and wrote to a sounddevice OutputStream; here we
  feed the existing AudioCodec music FIFO (so the output device, resampling, and
  TTS ducking are reused)
- the network read runs in asyncio (aiohttp) instead of a blocking thread
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import numpy as np

from src.audio_codecs.opus_codec import OpusCodec, parse_opus_toc
from src.constants.constants import AudioConfig
from src.logging import get_logger

if TYPE_CHECKING:
    from src.audio_codecs.audio_codec import AudioCodec

logger = get_logger()

# Framing: [2-byte big-endian length][opus packet]
_HEADER_LEN = 2
_MAX_PKT_BYTES = 1600
# The catalog is 16kHz mono Opus; decode at the protocol output rate
_OPUS_INPUT_RATE = 16000
_RECV_TIMEOUT = 60


class OpusStreamDecoder:
    """Decode a length-prefixed Opus stream into float32 PCM frames."""

    def __init__(self) -> None:
        self._codec = OpusCodec(
            input_sample_rate=_OPUS_INPUT_RATE,
            output_sample_rate=AudioConfig.OUTPUT_SAMPLE_RATE,
            channels=AudioConfig.CHANNELS,
        )
        self._codec.initialize()
        self._frame_ms: float | None = None
        self._frame_size: int | None = None

    def _frame_params(self, opus_data: bytes) -> tuple[int, int]:
        if self._frame_size is None or self._frame_ms is None:
            toc = parse_opus_toc(opus_data)
            frame_ms = toc["duration_ms"] if toc else 60
            self._frame_ms = frame_ms
            self._frame_size = int(
                AudioConfig.OUTPUT_SAMPLE_RATE * frame_ms / 1000
            )
            logger.info(
                f"Opus stream params: {frame_ms}ms frames "
                f"({self._frame_size} samples @ {AudioConfig.OUTPUT_SAMPLE_RATE}Hz)"
            )
        return self._frame_size, int(self._frame_ms)

    def decode_packet(self, opus_data: bytes) -> np.ndarray | None:
        """Decode one packet → float32 PCM (shape (N,) or (N, channels))."""
        if not opus_data:
            return None
        try:
            frame_size, _ = self._frame_params(opus_data)
            return self._codec.decode(opus_data, frame_size)
        except Exception as e:
            logger.warning(f"Failed to decode an Opus packet: {e}")
            return None

    def close(self) -> None:
        try:
            self._codec.close()
        except Exception:
            pass


class OpusStreamReader:
    """Read a length-prefixed Opus stream over HTTP and feed the AudioCodec."""

    def __init__(self, audio_codec: "AudioCodec") -> None:
        self._codec = audio_codec
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    async def stream(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        deadline: asyncio.Future | None = None,
    ) -> bool:
        """Stream until EOF/stop. Returns True when the stream finished cleanly."""
        import aiohttp

        self._stopped = False
        decoder = OpusStreamDecoder()
        frames = 0
        try:
            timeout = aiohttp.ClientTimeout(total=_RECV_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(f"Opus stream HTTP {resp.status}: {url}")
                        return False

                    logger.info(f"Opus streaming: {url}")
                    buf = bytearray()
                    async for chunk in resp.content.iter_chunked(8192):
                        if self._stopped:
                            logger.info("Opus stream stopped by request")
                            return False
                        if deadline is not None and deadline.done():
                            logger.info("Opus stream deadline reached")
                            return False
                        if not chunk:
                            continue
                        buf.extend(chunk)

                        while True:
                            if len(buf) < _HEADER_LEN:
                                break
                            pkt_len = (buf[0] << 8) | buf[1]
                            if pkt_len == 0 or pkt_len > _MAX_PKT_BYTES:
                                logger.warning(
                                    f"Invalid Opus packet length: {pkt_len}"
                                )
                                return False
                            if len(buf) < _HEADER_LEN + pkt_len:
                                break
                            payload = bytes(buf[_HEADER_LEN : _HEADER_LEN + pkt_len])
                            del buf[: _HEADER_LEN + pkt_len]

                            pcm = decoder.decode_packet(payload)
                            if pcm is None:
                                continue
                            frames += 1
                            await self._codec.write_pcm_direct(pcm)
            logger.info(
                f"Opus stream finished: {frames} frames "
                f"(~{frames * (decoder._frame_ms or 60) / 1000:.0f}s)"
            )
            return True
        except asyncio.CancelledError:
            logger.debug("Opus stream cancelled")
            raise
        except Exception as e:
            logger.warning(f"Opus stream failed: {e}")
            return False
        finally:
            decoder.close()
