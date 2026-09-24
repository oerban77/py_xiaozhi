import numpy as np

# ============================================================
# Opus library loading (must happen before importing opuslib)
# ============================================================
from src.utils.opus_loader import setup_opus

setup_opus()

# Must be imported after setup_opus()
import opuslib  # noqa: E402
import opuslib.api.decoder as decoder_api  # noqa: E402
import opuslib.api.encoder as encoder_api  # noqa: E402

from src.logging import get_logger  # noqa: E402

logger = get_logger()


_OPUS_BANDWIDTHS = {
    "NB": "8kHz", "MB": "12kHz", "WB": "16kHz",
    "SWB": "24kHz", "FB": "48kHz",
}


def parse_opus_toc(opus_data: bytes) -> dict:
    """Parse the encoding parameters from the TOC byte of an Opus packet.

    Parsed according to RFC 6716 Section 3.1:
    - the high 5 bits of the TOC = config (determines mode / bandwidth / single-frame duration)
    - the low 2 bits of the TOC = code (determines the number of frames)

    Returns:
        dict with keys: duration_ms, frame_ms, num_frames, bandwidth, mode
        An empty packet returns None
    """
    if not opus_data:
        return None

    toc = opus_data[0]
    config = (toc >> 3) & 0x1F
    code = toc & 0x03

    # config → mode + bandwidth + single-frame duration
    if config < 12:
        mode = "SILK"
        bandwidth = ("NB", "MB", "WB")[config // 4]
        frame_ms = (10, 20, 40, 60)[config % 4]
    elif config < 16:
        mode = "Hybrid"
        bandwidth = ("SWB", "FB")[(config - 12) // 2]
        frame_ms = (10, 20)[config % 2]
    else:
        mode = "CELT"
        bandwidth = ("NB", "WB", "SWB", "FB")[(config - 16) // 4]
        frame_ms = (2.5, 5, 10, 20)[config % 4]

    # code → frame count
    if code == 0:
        num_frames = 1
    elif code <= 2:
        num_frames = 2
    else:
        num_frames = (opus_data[1] & 0x3F) if len(opus_data) >= 2 else 1

    return {
        "duration_ms": frame_ms * num_frames,
        "frame_ms": frame_ms,
        "num_frames": num_frames,
        "bandwidth": bandwidth,
        "bandwidth_hz": _OPUS_BANDWIDTHS[bandwidth],
        "mode": mode,
    }


class OpusCodec:
    """Opus codec

    Uses the encode_float and decode_float interfaces of libopus.
    """

    def __init__(
        self,
        input_sample_rate: int,
        output_sample_rate: int,
        channels: int = 1,
    ):
        """Initialize the Opus encoder/decoder

        Args:
            input_sample_rate: input sample rate (encoding), e.g. 16000
            output_sample_rate: output sample rate (decoding), e.g. 24000
            channels: channel count, default 1
        """
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.channels = channels
        self.encoder = None
        self.decoder = None

    def initialize(self):
        """Create the codec

        Raises:
            Exception: creation failed
        """
        try:
            # Input encoder: 16kHz mono
            self.encoder = opuslib.Encoder(
                self.input_sample_rate,
                self.channels,
                opuslib.APPLICATION_VOIP,
            )

            # Output decoder: 24kHz mono
            self.decoder = opuslib.Decoder(self.output_sample_rate, self.channels)

            logger.info(
                f"Opus codec created (float32 mode) | "
                f"encode: {self.input_sample_rate}Hz | "
                f"decode: {self.output_sample_rate}Hz"
            )
        except Exception as e:
            logger.error(f"Failed to create Opus codec: {e}", exc_info=True)
            raise

    def encode(self, pcm_float32: np.ndarray, frame_size: int) -> bytes:
        """Encode float32 PCM → Opus

        Args:
            pcm_float32: float32 array in the range [-1.0, 1.0]
            frame_size: sample count

        Returns:
            Opus encoded data

        Raises:
            RuntimeError: Encoder not initialized
            Exception: encoding failed
        """
        if self.encoder is None:
            raise RuntimeError("Encoder not initialized")

        # Convert to bytes (float32 format)
        pcm_bytes = pcm_float32.astype(np.float32).tobytes()

        # Use encode_float (natively supported by libopus)
        return self.encoder.encode_float(pcm_bytes, frame_size)

    def decode(self, opus_data: bytes, frame_size: int) -> np.ndarray:
        """Decode Opus → float32 PCM

        Args:
            opus_data: Opus encoded data
            frame_size: expected sample count

        Returns:
            float32 array in the range [-1.0, 1.0]

        Raises:
            RuntimeError: Decoder not initialized
            Exception: decoding failed
        """
        if self.decoder is None:
            raise RuntimeError("Decoder not initialized")

        # Use decode_float (natively supported by libopus)
        # Note: channels was specified when creating the Decoder; decode_float does not need this parameter
        pcm_bytes = self.decoder.decode_float(opus_data, frame_size, decode_fec=False)

        # Convert to a NumPy array
        return np.frombuffer(pcm_bytes, dtype=np.float32)

    def close(self):
        """Release resources and explicitly destroy the C-level codec state.

        opuslib's Encoder/Decoder.__del__ also calls destroy(), so encoder_state /
        decoder_state must be set to None first to avoid a double free.
        Idempotent: safe to call repeatedly.
        """
        if getattr(self, '_closed', False):
            return
        self._closed = True

        if self.encoder is not None:
            encoder_api.destroy(self.encoder.encoder_state)
            self.encoder.encoder_state = None  # Prevent a second release in __del__
            self.encoder = None
        if self.decoder is not None:
            decoder_api.destroy(self.decoder.decoder_state)
            self.decoder.decoder_state = None  # Prevent a second release in __del__
            self.decoder = None
        logger.debug("Opus codec released")
