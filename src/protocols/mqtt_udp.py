"""The UDP encrypted audio channel that accompanies an MQTT session.

Responsible for: the socket, the receive thread, and AES-encrypted sending; frames are handed back to the upper layer through the event loop.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

from src.logging import get_logger
from src.protocols.mqtt_crypto import (
    aes_ctr_decrypt,
    aes_ctr_encrypt,
    build_audio_nonce,
)

logger = get_logger()


class MqttUdpChannel:
    """UDP audio transport (encrypted Opus frames)."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.running = False

        self.server = ""
        self.port = 0
        self.aes_key: str | None = None  # hex
        self.aes_nonce: str | None = None  # hex
        self.local_sequence = 0
        self.remote_sequence = 0

        self._on_incoming_audio = None

    def configure(
        self,
        server: str,
        port: int,
        aes_key: str,
        aes_nonce: str,
    ) -> None:
        self.server = server
        self.port = port
        self.aes_key = aes_key
        self.aes_nonce = aes_nonce
        self.local_sequence = 0
        self.remote_sequence = 0

    def set_audio_handler(self, handler) -> None:
        self._on_incoming_audio = handler

    def is_ready(self) -> bool:
        return (
            self.socket is not None
            and self.running
            and bool(self.server)
            and self.port > 0
        )

    def start(self) -> None:
        """Create the socket and start the receive thread (stops the old resources first)."""
        self.stop()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Bind explicitly so the macOS network stack routes UDP correctly
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(0.5)
        self.socket = sock

        self.running = True
        self.thread = threading.Thread(
            target=self._receive_loop, name="mqtt-udp-rx", daemon=True
        )
        self.thread.start()
        logger.info(
            f"UDP receive thread started, listening for data from {self.server}:{self.port}"
        )

    def stop(self) -> None:
        """Stop the receive thread and close the socket."""
        self.running = False
        if self.thread and self.thread.is_alive():
            try:
                self.thread.join(1.0)
            except RuntimeError:
                pass
        self.thread = None

        if self.socket:
            try:
                self.socket.close()
            except Exception as e:
                logger.error(f"Failed to close UDP socket: {e}", exc_info=True)
        self.socket = None

    def reset_session(self) -> None:
        """Clear the session-side fields after goodbye."""
        self.stop()
        self.server = ""
        self.port = 0
        self.aes_key = None
        self.aes_nonce = None
        self.local_sequence = 0
        self.remote_sequence = 0

    def send_audio(self, audio_data: bytes) -> bool:
        if not self.socket or not self.server or not self.port:
            logger.error("UDP channel not initialized")
            return False
        if not self.aes_key or not self.aes_nonce:
            logger.error("UDP encryption parameters missing")
            return False

        self.local_sequence = (self.local_sequence + 1) & 0xFFFFFFFF
        new_nonce = build_audio_nonce(
            self.aes_nonce, len(audio_data), self.local_sequence
        )
        encrypt_encoded_data = aes_ctr_encrypt(
            bytes.fromhex(self.aes_key),
            bytes.fromhex(new_nonce),
            bytes(audio_data),
        )
        packet = bytes.fromhex(new_nonce) + encrypt_encoded_data
        self.socket.sendto(packet, (self.server, self.port))

        if self.local_sequence % 10 == 0:
            logger.info(
                f"Audio packet sent, sequence number: {self.local_sequence}, target: "
                f"{self.server}:{self.port}"
            )
        return True

    def _receive_loop(self) -> None:
        debug_counter = 0
        while self.running and self.socket:
            try:
                data, _addr = self.socket.recvfrom(4096)
                debug_counter += 1
                try:
                    if len(data) < 16:
                        logger.error(f"Invalid audio packet size: {len(data)}")
                        continue
                    if not self.aes_key:
                        continue

                    received_nonce = data[:16]
                    encrypted_audio = data[16:]
                    decrypted = aes_ctr_decrypt(
                        bytes.fromhex(self.aes_key),
                        received_nonce,
                        encrypted_audio,
                    )

                    if debug_counter % 100 == 0:
                        logger.debug(
                            f"Decrypted audio packet #{debug_counter}, "
                            f"size: {len(decrypted)} bytes"
                        )

                    if self._on_incoming_audio:
                        self._dispatch_audio(decrypted)

                except Exception as e:
                    logger.error(f"Error handling audio packet: {e}", exc_info=True)
                    continue
            except TimeoutError:
                pass
            except Exception as e:
                logger.error(f"UDP receive thread error: {e}", exc_info=True)
                if not self.running:
                    break
                time.sleep(0.1)

        logger.info("UDP receive thread stopped")

    def _dispatch_audio(self, audio_data: bytes) -> None:
        """Schedule the frame callback from the receive thread onto the event loop (the handler must be synchronous and runs on the loop thread)."""
        handler = self._on_incoming_audio
        if not handler:
            return
        try:
            self._loop.call_soon_threadsafe(handler, audio_data)
        except Exception as e:
            logger.error(f"Failed to schedule audio callback: {e}", exc_info=True)
