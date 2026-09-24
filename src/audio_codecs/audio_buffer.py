"""Sample-level PCM buffer.

Thread-safe FIFO used for output callback mixing
"""

import threading
from collections import deque

import numpy as np


class PcmFifo:
    """Sample-level PCM FIFO (float32 mono, thread-safe).

    The write end runs in the asyncio thread, and the read end runs in the
    audio output callback thread; used for mixing TTS / music in the output
    callback after they are split into separate streams.

    - push: drops the oldest samples when over capacity (recorded in dropped)
    - pull: returns a fixed-length block, zero-padded when data is short;
      returns None when there is no data at all
    """

    def __init__(self, max_samples: int):
        self._chunks: deque = deque()
        self._offset = 0  # Number of samples consumed in the first block
        self._size = 0  # Total readable sample count
        self._max = int(max_samples)
        self._lock = threading.Lock()
        self.dropped = 0

    @property
    def size(self) -> int:
        return self._size

    def push(self, pcm: np.ndarray) -> None:
        """Append float32 mono data; drop the oldest when over capacity."""
        if pcm.ndim > 1:
            pcm = pcm.reshape(-1)
        if pcm.dtype != np.float32:
            pcm = pcm.astype(np.float32)
        with self._lock:
            self._chunks.append(pcm)
            self._size += len(pcm)
            while self._size > self._max and self._chunks:
                head = self._chunks[0]
                remain = len(head) - self._offset
                drop = min(remain, self._size - self._max)
                self._offset += drop
                self._size -= drop
                self.dropped += drop
                if self._offset >= len(head):
                    self._chunks.popleft()
                    self._offset = 0

    def pull(self, n: int) -> np.ndarray | None:
        """Take n samples; return None if no data, zero-pad if short."""
        with self._lock:
            if self._size == 0:
                return None
            out = np.zeros(n, dtype=np.float32)
            filled = 0
            while filled < n and self._chunks:
                head = self._chunks[0]
                avail = len(head) - self._offset
                take = min(avail, n - filled)
                out[filled : filled + take] = head[
                    self._offset : self._offset + take
                ]
                filled += take
                self._offset += take
                self._size -= take
                if self._offset >= len(head):
                    self._chunks.popleft()
                    self._offset = 0
            return out

    def clear(self) -> int:
        """Clear and return the number of dropped samples."""
        with self._lock:
            count = self._size
            self._chunks.clear()
            self._offset = 0
            self._size = 0
            return count
