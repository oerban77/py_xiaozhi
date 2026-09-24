import asyncio
import json

from src.constants.constants import AbortReason, ListeningMode
from src.logging import get_logger

logger = get_logger()


class Protocol:
    def __init__(self):
        self.session_id = ""
        # Initialize the callbacks to None
        self._on_incoming_json = None
        self._on_incoming_audio = None
        self._on_audio_channel_opened = None
        self._on_audio_channel_closed = None
        self._on_network_error = None
        # Connection state change callback
        self._on_connection_state_changed = None
        self._on_reconnecting = None

        # Connection state and auto-reconnect (shared; moved up from the subclasses)
        self._is_closing = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5  # Reconnect 5 times by default
        self._auto_reconnect_enabled = False  # Auto-reconnect disabled by default
        self._connection_monitor_task = None

    def on_incoming_json(self, callback):
        """
        Set the JSON message reception callback.
        """
        self._on_incoming_json = callback

    def on_incoming_audio(self, callback):
        """
        Set the audio data reception callback.
        """
        self._on_incoming_audio = callback

    def on_audio_channel_opened(self, callback):
        """
        Set the audio channel opened callback.
        """
        self._on_audio_channel_opened = callback

    def on_audio_channel_closed(self, callback):
        """
        Set the audio channel closed callback.
        """
        self._on_audio_channel_closed = callback

    def on_network_error(self, callback):
        """
        Set the network error callback.
        """
        self._on_network_error = callback

    def on_connection_state_changed(self, callback):
        """Set the connection state change callback.

        Args:
            callback: the callback, taking the arguments (connected: bool, reason: str)
        """
        self._on_connection_state_changed = callback

    def on_reconnecting(self, callback):
        """Set the reconnect attempt callback.

        Args:
            callback: the callback, taking the arguments (attempt: int, max_attempts: int)
        """
        self._on_reconnecting = callback

    async def send_text(self, message):
        """
        Abstract method for sending a text message; must be implemented by subclasses.
        """
        raise NotImplementedError("send_text must be implemented by a subclass")

    async def send_audio(self, data: bytes):
        """
        Abstract method for sending audio data; must be implemented by subclasses.
        """
        raise NotImplementedError("send_audio must be implemented by a subclass")

    def is_audio_channel_opened(self) -> bool:
        """
        Abstract method for checking whether the audio channel is open; must be implemented by subclasses.
        """
        raise NotImplementedError("is_audio_channel_opened must be implemented by a subclass")

    async def open_audio_channel(self) -> bool:
        """
        Abstract method for opening the audio channel; must be implemented by subclasses.
        """
        raise NotImplementedError("open_audio_channel must be implemented by a subclass")

    async def close_audio_channel(self):
        """
        Abstract method for closing the audio channel; must be implemented by subclasses.
        """
        raise NotImplementedError("close_audio_channel must be implemented by a subclass")

    async def send_abort_speaking(self, reason):
        """
        Send a message aborting speech.
        """
        message = {"session_id": self.session_id, "type": "abort"}
        if reason == AbortReason.WAKE_WORD_DETECTED:
            message["reason"] = "wake_word_detected"
        await self.send_text(json.dumps(message))

    async def send_wake_word_detected(self, wake_word):
        """
        Send a message that a wake word was detected.
        """
        message = {
            "session_id": self.session_id,
            "type": "listen",
            "state": "detect",
            "text": wake_word,
        }
        await self.send_text(json.dumps(message))

    async def send_start_listening(self, mode):
        """
        Send a message that listening has started.
        """
        mode_map = {
            ListeningMode.REALTIME: "realtime",
            ListeningMode.AUTO_STOP: "auto",
            ListeningMode.MANUAL: "manual",
        }
        message = {
            "session_id": self.session_id,
            "type": "listen",
            "state": "start",
            "mode": mode_map[mode],
        }
        await self.send_text(json.dumps(message))

    async def send_stop_listening(self):
        """
        Send a message that listening has stopped.
        """
        message = {"session_id": self.session_id, "type": "listen", "state": "stop"}
        await self.send_text(json.dumps(message))

    async def send_iot_descriptors(self, descriptors):
        """
        Send IoT device descriptor information.
        """
        try:
            # Parse the descriptor data
            if isinstance(descriptors, str):
                descriptors_data = json.loads(descriptors)
            else:
                descriptors_data = descriptors

            # Check whether it is an array
            if not isinstance(descriptors_data, list):
                logger.error("IoT descriptors should be an array")
                return

            # Send a separate message for each descriptor
            for i, descriptor in enumerate(descriptors_data):
                if descriptor is None:
                    logger.error(f"Failed to get IoT descriptor at index {i}")
                    continue

                message = {
                    "session_id": self.session_id,
                    "type": "iot",
                    "update": True,
                    "descriptors": [descriptor],
                }

                try:
                    await self.send_text(json.dumps(message))
                except Exception as e:
                    logger.error(
                        f"Failed to send JSON message for IoT descriptor "
                        f"at index {i}: {e}"
                    )
                    continue

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse IoT descriptors: {e}", exc_info=True)
            return

    async def send_iot_states(self, states):
        """
        Send IoT device state information.
        """
        if isinstance(states, str):
            states_data = json.loads(states)
        else:
            states_data = states

        message = {
            "session_id": self.session_id,
            "type": "iot",
            "update": True,
            "states": states_data,
        }
        await self.send_text(json.dumps(message))

    async def send_mcp_message(self, payload):
        """
        Send an MCP message.
        """
        if isinstance(payload, str):
            payload_data = json.loads(payload)
        else:
            payload_data = payload

        message = {
            "session_id": self.session_id,
            "type": "mcp",
            "payload": payload_data,
        }

        await self.send_text(json.dumps(message))

    # ============ Connection state check (template method, implemented by subclasses) ============

    def _is_connected(self) -> bool:
        """Check whether the connection is alive.

        Subclasses must implement this method and return the health state of the current protocol connection.

        Returns:
            bool: True when the connection is alive, False otherwise
        """
        raise NotImplementedError("_is_connected must be implemented by a subclass")

    # ============ Protocol-specific cleanup (template method, implemented by subclasses) ============

    async def _do_cleanup(self):
        """Clean up protocol-specific resources (excluding shared state and monitor tasks).

        When subclasses implement this method they should:
        - Close protocol-specific network connections (socket / websocket / mqtt client, etc.)
        - Cancel protocol-specific background tasks (heartbeat, message handling, etc.)
        - Reset protocol-specific timestamps/state

        Do not do the following in this method:
        - Set self.connected = False (handled by the base class _handle_connection_loss)
        - Cancel self._connection_monitor_task (handled by the base class _handle_connection_loss)
        """
        raise NotImplementedError("_do_cleanup must be implemented by a subclass")

    # ============ Connection monitoring (shared; subclasses can override _monitor_interval) ============

    @property
    def _monitor_interval(self) -> float:
        """The connection monitor check interval (seconds); subclasses can override it."""
        return 5.0

    def _start_connection_monitor(self):
        """Start the connection health monitoring background task."""
        if (
            self._connection_monitor_task is None
            or self._connection_monitor_task.done()
        ):
            self._connection_monitor_task = asyncio.create_task(
                self._connection_monitor()
            )

    async def _connection_monitor(self):
        """Connection health state monitoring coroutine.

        Repeatedly checks the return value of self._is_connected()
        and calls self._handle_connection_loss() when a disconnect is detected.
        """
        try:
            while not self._is_closing:
                await asyncio.sleep(self._monitor_interval)

                if not self._is_connected():
                    logger.warning("Connection detected as closed")
                    await self._handle_connection_loss("Connection check failed")
                    break

        except asyncio.CancelledError:
            logger.debug("Connection monitor task cancelled")
        except Exception as e:
            logger.error(f"Connection monitor error: {e}", exc_info=True)

    # ============ Auto-reconnect (shared) ============

    def enable_auto_reconnect(self, enabled: bool = True, max_attempts: int = 5):
        """Enable or disable auto-reconnect.

        Args:
            enabled: whether to enable auto-reconnect
            max_attempts: the maximum number of reconnect attempts
        """
        self._auto_reconnect_enabled = enabled
        if enabled:
            self._max_reconnect_attempts = max_attempts
            logger.info(f"Auto-reconnect enabled, max attempts: {max_attempts}")
        else:
            self._max_reconnect_attempts = 0
            logger.info("Auto-reconnect disabled")

    async def _handle_connection_loss(self, reason: str, *, clean: bool = False):
        """Handle connection loss (shared logic).

        Flow:
        1. Update the connection state
        2. Cancel the connection monitor task
        3. Call the subclass _do_cleanup() to clean up protocol-specific resources
        4. Notify observers (state change, audio channel closed)
        5. Decide whether to auto-reconnect based on the configuration

        Args:
            clean: the server closed the connection normally (e.g., session end). Only the channel
                   is taken back; no auto-reconnect is triggered and no network error is reported.
        """
        if clean:
            logger.info(f"Connection closed normally by server: {reason}")
        else:
            logger.warning(f"Connection lost: {reason}")

        was_connected = self.connected
        self.connected = False

        # Cancel the connection monitor task
        await self._cancel_monitor_task()

        # Notify the connection state change
        if self._on_connection_state_changed and was_connected:
            try:
                self._on_connection_state_changed(False, reason)
            except Exception as e:
                logger.error(f"Failed to invoke connection state change callback: {e}", exc_info=True)

        # Call the subclass protocol-specific cleanup
        await self._do_cleanup()

        # Notify that the audio channel closed
        if self._on_audio_channel_closed:
            try:
                await self._on_audio_channel_closed()
            except Exception as e:
                logger.error(f"Failed to invoke audio channel closed callback: {e}", exc_info=True)

        # The server closed the connection normally: the session ending does not count as a fault,
        # so no reconnect and no error report; open_audio_channel is called again as needed for the next interaction
        if clean:
            return

        # Decide whether to attempt an auto-reconnect based on the configuration
        if (
            not self._is_closing
            and self._auto_reconnect_enabled
            and self._reconnect_attempts < self._max_reconnect_attempts
        ):
            await self._attempt_reconnect(reason)
        else:
            if self._on_network_error:
                if (
                    self._auto_reconnect_enabled
                    and self._reconnect_attempts >= self._max_reconnect_attempts
                ):
                    await self._on_network_error(f"Connection lost and reconnect failed: {reason}")
                else:
                    await self._on_network_error(f"Connection lost: {reason}")

    async def _attempt_reconnect(self, original_reason: str):
        """Attempt an auto-reconnect (shared logic).

        Uses an exponential backoff strategy and calls the subclass connect() for the actual connection.
        """
        self._reconnect_attempts += 1

        # Notify that a reconnect is starting
        if self._on_reconnecting:
            try:
                self._on_reconnecting(
                    self._reconnect_attempts, self._max_reconnect_attempts
                )
            except Exception as e:
                logger.error(f"Failed to invoke reconnect callback: {e}", exc_info=True)

        logger.info(
            f"Attempting auto-reconnect ({self._reconnect_attempts}/{self._max_reconnect_attempts})"
        )

        # Exponential backoff wait, up to 30 seconds
        await asyncio.sleep(min(self._reconnect_attempts * 2, 30))

        try:
            success = await self.connect()
            if success:
                logger.info("Auto-reconnect succeeded")
                if self._on_connection_state_changed:
                    self._on_connection_state_changed(True, "Reconnect succeeded")
            else:
                logger.warning(
                    f"Auto-reconnect failed ({self._reconnect_attempts}/{self._max_reconnect_attempts})"
                )
                if self._reconnect_attempts >= self._max_reconnect_attempts:
                    if self._on_network_error:
                        await self._on_network_error(
                            f"Reconnect failed; the maximum number of reconnect attempts was reached: {original_reason}"
                        )
        except Exception as e:
            logger.error(f"Error during reconnect: {e}", exc_info=True)
            if self._reconnect_attempts >= self._max_reconnect_attempts:
                if self._on_network_error:
                    await self._on_network_error(f"Reconnect error: {str(e)}")

    async def _cancel_monitor_task(self):
        """Cancel and wait for the connection monitor task to finish."""
        if self._connection_monitor_task and not self._connection_monitor_task.done():
            self._connection_monitor_task.cancel()
            try:
                await self._connection_monitor_task
            except asyncio.CancelledError:
                pass

    def get_connection_info(self) -> dict:
        """Get the connection information (base class implementation; subclasses can extend it).

        Returns:
            dict: a dictionary containing the connection state, reconnect attempts, and more
        """
        return {
            "is_closing": self._is_closing,
            "auto_reconnect_enabled": self._auto_reconnect_enabled,
            "reconnect_attempts": self._reconnect_attempts,
            "max_reconnect_attempts": self._max_reconnect_attempts,
        }
