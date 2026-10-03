import asyncio
import json
import logging
import ssl

import websockets

from src.constants.constants import AudioConfig
from src.logging import get_logger
from src.protocols.protocol import Protocol
from src.utils.config_manager import get_config

# The server may use a self-signed certificate; skip client-side certificate verification for now
# to avoid connection failures caused by non-standard SSL certificates in production environments
ssl_context = ssl._create_unverified_context()

logger = get_logger()


class WebsocketProtocol(Protocol):
    def __init__(self):
        super().__init__()
        # Get the config manager instance
        self.config = get_config()
        self.websocket = None
        self.connected = False
        self.hello_received = None  # Set to None initially during initialization
        # Reference to the message handling task for cancellation on shutdown
        self._message_task = None

        self.WEBSOCKET_URL = self.config.get_config(
            "SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL"
        )
        access_token = self.config.get_config(
            "SYSTEM_OPTIONS.NETWORK.WEBSOCKET_ACCESS_TOKEN"
        )
        device_id = self.config.get_config("SYSTEM_OPTIONS.DEVICE_ID")
        client_id = self.config.get_config("SYSTEM_OPTIONS.CLIENT_ID")

        self.HEADERS = {
            "Authorization": f"Bearer {access_token}",
            "Protocol-Version": "1",
            "Device-Id": device_id,  # Get the device MAC address
            "Client-Id": client_id,
        }

    async def connect(self) -> bool:
        """
        Connect to the WebSocket server.
        """
        if self._is_closing:
            logger.warning("Connection is closing; cancelling new connection attempt")
            return False

        try:
            # Create the Event during connection to make sure it belongs to the correct event loop
            self.hello_received = asyncio.Event()

            # Determine whether SSL should be used
            current_ssl_context = None
            if self.WEBSOCKET_URL.startswith("wss://"):
                current_ssl_context = ssl_context

            # Establish the WebSocket connection (written to work across Python versions)
            try:
                # Newer style (Python 3.11+)
                self.websocket = await websockets.connect(
                    uri=self.WEBSOCKET_URL,
                    ssl=current_ssl_context,
                    additional_headers=self.HEADERS,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    open_timeout=5,
                    max_size=10 * 1024 * 1024,
                    compression=None,
                    proxy=None,
                )
            except TypeError:
                # Older style (for earlier Python versions)
                self.websocket = await websockets.connect(
                    self.WEBSOCKET_URL,
                    ssl=current_ssl_context,
                    extra_headers=self.HEADERS,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    open_timeout=5,
                    max_size=10 * 1024 * 1024,
                    compression=None,
                )

            # Start the message handling loop (keep the task reference so it can be cancelled on close)
            self._message_task = asyncio.create_task(self._message_handler())

            # Start connection monitoring
            self._start_connection_monitor()

            # Send client hello message
            hello_message = {
                "type": "hello",
                "version": 1,
                "features": {
                    "mcp": True,
                },
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": AudioConfig.INPUT_SAMPLE_RATE,
                    "channels": AudioConfig.CHANNELS,
                    "frame_duration": AudioConfig.FRAME_DURATION,
                },
            }
            await self.send_text(json.dumps(hello_message))

            # Wait for the server hello response
            try:
                await asyncio.wait_for(self.hello_received.wait(), timeout=10.0)
                self.connected = True
                self._reconnect_attempts = 0  # Reset reconnection count
                logger.info("Connected to WebSocket server")

                # Notify the connection state change
                if self._on_connection_state_changed:
                    self._on_connection_state_changed(True, "Connected")

                return True
            except asyncio.TimeoutError:
                logger.error("Timed out waiting for server hello response")
                await self._do_cleanup()
                if self._on_network_error:
                    await self._on_network_error("Response timed out")
                return False

        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}", exc_info=True)
            await self._do_cleanup()
            if self._on_network_error:
                await self._on_network_error(f"Cannot connect to server: {str(e)}")
            return False

    # ============ Template method implementations ============

    @property
    def _monitor_interval(self) -> float:
        """The WSS connection monitor check interval (seconds)."""
        return 5.0

    def _is_connected(self) -> bool:
        """Check whether the WebSocket connection is alive.

        `close_code` alone is not a reliable liveness signal because a socket may
        still be in the closing handshake while `close_code` is `None`. Prefer the
        websocket's actual open/closed state, falling back to `close_code` only if
        the socket object doesn't expose the newer `open` flag.
        """
        if not self.websocket:
            return False
        if getattr(self.websocket, "closed", False):
            return False
        if hasattr(self.websocket, "open"):
            return bool(self.websocket.open)
        return self.websocket.close_code is None

    async def _do_cleanup(self):
        """WebSocket protocol-specific resource cleanup.

        Cleans up the message handling task, the heartbeat task, the WebSocket connection, and the heartbeat timestamp.
        It is not responsible for cancelling the connection monitoring task (handled by the base class _handle_connection_loss).
        """
        # Cancel the message handling task
        if self._message_task and not self._message_task.done():
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Error while cancelling message-wait task: {e}")
        self._message_task = None

        # Close the WebSocket connection
        if self.websocket and self.websocket.close_code is None:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket connection: {e}", exc_info=True)

        self.websocket = None

    def get_connection_info(self) -> dict:
        """Get WSS connection info.

        Returns:
            dict: a dictionary containing the connection state, reconnect attempts, and more
        """
        info = super().get_connection_info()
        info.update(
            {
                "connected": self.connected,
                "websocket_closed": (
                    self.websocket.close_code is not None if self.websocket else True
                ),
                "websocket_url": self.WEBSOCKET_URL,
            }
        )
        return info

    async def _message_handler(self):
        """
        Handle received WebSocket messages.
        """
        try:
            async for message in self.websocket:
                if self._is_closing:
                    break

                try:
                    if isinstance(message, str):
                        try:
                            data = json.loads(message)
                            msg_type = data.get("type")
                            if msg_type == "hello":
                                # Handle the server hello message
                                await self._handle_server_hello(data)
                            else:
                                if self._on_incoming_json:
                                    self._on_incoming_json(data)
                        except json.JSONDecodeError as e:
                            logger.error(f"Invalid JSON message: {message}, error: {e}", exc_info=True)
                    elif isinstance(message, bytes):
                        # Binary message, may be audio
                        if self._on_incoming_audio:
                            self._on_incoming_audio(message)
                except Exception as e:
                    # Handle errors from a single message without stopping processing of others
                    logger.error(f"Error handling message: {e}", exc_info=True)
                    continue

        except asyncio.CancelledError:
            logger.debug("Message handling task cancelled")
            return
        except websockets.ConnectionClosedOK as e:
            if not self._is_closing:
                logger.info(f"WebSocketConnection closed normally by server: {e}")
                await self._handle_connection_loss(
                    f"Server closed connection: {e.code}", clean=True
                )
        except websockets.ConnectionClosedError as e:
            if not self._is_closing:
                logger.info(f"WebSocket connection closed with error: {e}")
                await self._handle_connection_loss(f"Connection error: {e.code} {e.reason}")
        except websockets.InvalidState as e:
            logger.error(f"Invalid WebSocket state: {e}", exc_info=True)
            await self._handle_connection_loss("Connection state abnormal")
        except ConnectionResetError:
            logger.warning("Connection reset")
            await self._handle_connection_loss("Connection reset")
        except OSError as e:
            logger.error(f"Network I/O error: {e}", exc_info=True)
            await self._handle_connection_loss("Network I/O error")
        except Exception as e:
            logger.error(f"Message handling loop error: {e}", exc_info=True)
            await self._handle_connection_loss(f"Message processing exception: {str(e)}")

    async def send_audio(self, data: bytes):
        """
        Send audio data.
        """
        if not self.is_audio_channel_opened():
            return

        try:
            await self.websocket.send(data)
        except websockets.ConnectionClosedOK as e:
            # The server took back the session normally (e.g., closed after TTS finished); this is not a network error
            logger.info(f"Connection closed normally by server while sending audio: {e}")
            await self._handle_connection_loss(
                f"Server closed while sending audio: {e.code}", clean=True
            )
        except websockets.ConnectionClosedError as e:
            logger.warning(f"Connection closed with error while sending audio: {e}")
            await self._handle_connection_loss(f"Send audio failed: {e.code} {e.reason}")
        except Exception as e:
            logger.error(f"Failed to send audio data: {e}", exc_info=True)
            # Do not call the network error callback here; let the connection handler handle it
            await self._handle_connection_loss(f"Send audio exception: {str(e)}")

    async def send_text(self, message: str):
        """
        Send textmessage.
        """
        if not self.websocket or self._is_closing:
            logger.warning("WebSocket is not connected or is closing; cannot send message")
            return

        try:
            close_code = self.websocket.close_code
        except Exception:
            close_code = None
        if close_code is not None:
            # 1000/1001/1005 count as a normal close (the server taking back the session)
            clean = close_code in (1000, 1001, 1005)
            logger.log(
                logging.INFO if clean else logging.WARNING,
                f"WebSocket closed (code={close_code}); skipping send text",
            )
            if self.connected:
                await self._handle_connection_loss(
                    f"Send text failed: Connection closed {close_code}", clean=clean
                )
            return

        try:
            await self.websocket.send(message)
        except websockets.ConnectionClosedOK as e:
            # The server took back the session normally; this is not a network error
            logger.info(f"Connection closed normally by server while sending text: {e}")
            if self.connected and not self._is_closing:
                await self._handle_connection_loss(
                    f"Server closed while sending text: {e.code}", clean=True
                )
        except websockets.ConnectionClosedError as e:
            logger.warning(f"Connection closed with error while sending text: {e}")
            if self.connected and not self._is_closing:
                await self._handle_connection_loss(
                    f"Send text error: {e.code} {e.reason}"
                )
        except Exception as e:
            logger.error(f"Failed to send text message: {e}", exc_info=True)
            if self.connected and not self._is_closing:
                await self._handle_connection_loss(f"Send text exception: {str(e)}")

    def is_audio_channel_opened(self) -> bool:
        """Check whether the audio channel is open.

        Check the connection state more accurately, including the actual WebSocket state
        """
        if not self.websocket or not self.connected or self._is_closing:
            return False

        # Check the actual WebSocket state
        try:
            return self.websocket.close_code is None
        except Exception:
            return False

    async def open_audio_channel(self) -> bool:
        """Establish the WebSocket connection.

        If not connected yet, a new WebSocket connection is created
        Returns:
            bool: whether the connection succeeded
        """
        if not self.is_audio_channel_opened():
            return await self.connect()
        return True

    async def _handle_server_hello(self, data: dict):
        """
        Handle the server's hello message.
        """
        try:
            # Verify the transport method
            transport = data.get("transport")
            if not transport or transport != "websocket":
                logger.error(f"Unsupported transport: {transport}")
                return

            # Settings hello receive event
            self.hello_received.set()

            # Notify that the audio channel is open
            if self._on_audio_channel_opened:
                await self._on_audio_channel_opened()

            logger.info("Server hello message handled successfully")

        except Exception as e:
            logger.error(f"Error handling server hello message: {e}", exc_info=True)
            if self._on_network_error:
                await self._on_network_error(f"Failed to process server response: {str(e)}")

    async def close_audio_channel(self):
        """
        Close the audio channel.
        """
        self._is_closing = True

        try:
            self.connected = False

            # Cancel the connection monitoring task (managed by base class)
            await self._cancel_monitor_task()

            # Protocol-specific cleanup
            await self._do_cleanup()

            if self._on_audio_channel_closed:
                await self._on_audio_channel_closed()

        except Exception as e:
            logger.error(f"Failed to close audio channel: {e}", exc_info=True)
        finally:
            self._is_closing = False
