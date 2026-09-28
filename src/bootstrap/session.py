"""Session control: listen/speak state machine and protocol sending.

Separated from the UI-side SessionActions (button text / mode): this module is responsible only for application-level session logic such as connect,
listen, abort, and TTS loopback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.constants.constants import DeviceState, ListeningMode
from src.core.event_bus import Events
from src.logging import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.protocol_manager import ProtocolManager
    from src.core.state_manager import StateManager
    from src.plugins.manager import PluginManager

logger = get_logger()


class ConversationSession:
    """Application-level session controller."""

    def __init__(
        self,
        state: "StateManager",
        protocol: "ProtocolManager",
        plugins: "PluginManager",
        event_bus: Optional["EventBus"] = None,
    ) -> None:
        self.state = state
        self.protocol = protocol
        self.plugins = plugins
        self._event_bus = event_bus
        self._aborted = False
        # MCP tool config reconnect, etc.: keep IDLE after the channel opens rather than automatically entering listening
        self._keep_idle_on_channel_open = False

    # -------------------------
    # Event subscription
    # -------------------------
    def bind_events(self, event_bus: "EventBus") -> None:
        """Subscribe protocol/state-related events (safe to call repeatedly; the last call wins)."""
        self._event_bus = event_bus
        event_bus.on(Events.AUDIO_CHANNEL_OPENED, self._on_audio_channel_opened)
        event_bus.on(Events.AUDIO_CHANNEL_CLOSED, self._on_audio_channel_closed)
        event_bus.on(Events.INCOMING_JSON, self._on_incoming_json)
        # Note: INCOMING_AUDIO goes through the direct channel and no longer uses EventBus
        event_bus.on(Events.NETWORK_ERROR, self._on_network_error)
        event_bus.on(Events.DEVICE_STATE_CHANGED, self._on_device_state_changed)
        event_bus.on(
            Events.PROTOCOL_RECONNECT_REQUEST, self._on_protocol_reconnect_request
        )

    # -------------------------
    # Event handler
    # -------------------------
    async def _on_audio_channel_opened(self, _=None) -> None:
        if self._keep_idle_on_channel_open:
            self._keep_idle_on_channel_open = False
            self.state.set_keep_listening(False)
            await self.state.set_device_state(DeviceState.IDLE)
            logger.info("Protocol channel opened (config reconnect): staying idle, not entering listen")
            return
        await self.state.set_device_state(DeviceState.LISTENING)

    async def _on_audio_channel_closed(self, _=None) -> None:
        await self.state.set_device_state(DeviceState.IDLE)

    async def _on_network_error(self, error_message: str = None) -> None:
        """Network error: stop the continuous listener and reset the device state to IDLE."""
        self.state.set_keep_listening(False)
        try:
            if not self.state.is_idle():
                await self.state.set_device_state(DeviceState.IDLE)
        except Exception as e:
            logger.error(f"Failed to reset device state after network error: {e}", exc_info=True)

    async def _on_device_state_changed(self, data: dict) -> None:
        new_state = data.get("new_state")
        if new_state:
            await self.plugins.notify_device_state_changed(new_state)
            if new_state == DeviceState.LISTENING:
                self._aborted = False

    async def _on_incoming_json(self, json_data: dict) -> None:
        try:
            msg_type = json_data.get("type") if isinstance(json_data, dict) else None
            logger.info(f"JSON message received: type={msg_type}")
            if msg_type == "alert":
                logger.warning(
                    "Server alert: code=%s, message=%s",
                    json_data.get("code"),
                    json_data.get("message") or json_data.get("text"),
                )

            if msg_type == "tts":
                state = json_data.get("state")
                if state == "start":
                    await self._handle_tts_start()
                elif state == "stop":
                    await self._handle_tts_stop()

            await self.plugins.notify_incoming_json(json_data)

        except Exception as e:
            logger.error(f"Failed to handle JSON message: {e}", exc_info=True)

    async def _handle_tts_start(self) -> None:
        if (
            self.state.keep_listening
            and self.state.listening_mode == ListeningMode.REALTIME
        ):
            await self.state.set_device_state(DeviceState.LISTENING)
        else:
            await self.state.set_device_state(DeviceState.SPEAKING)

    async def _handle_tts_stop(self) -> None:
        # If we still need to keep listening, send listen first, then clear the queue and update state
        if not self.state.keep_listening:
            await self.state.set_device_state(DeviceState.IDLE)
            return

        # In realtime mode we are generally still listening, so no need to send it again
        if self.state.listening_mode != ListeningMode.REALTIME:
            if self.protocol.is_audio_channel_opened():
                try:
                    await self.protocol.send_start_listening(self.state.listening_mode)
                except Exception as e:
                    logger.warning(
                        f"Failed to re-listen after TTS finished: {e}",
                        exc_info=True,
                    )
            else:
                logger.warning("TTS finished but protocol channel is closed; skipping re-listen")

        # Only discard buffered TTS audio when the speech was aborted by the user
        # (interrupt / wake word). A normal tts stop arrives as soon as the server has
        # streamed every frame, which can be well before the playback buffer has drained,
        # so clearing here would cut off the tail of the sentence.
        if self._aborted:
            try:
                audio_plugin = self.plugins.get_plugin("audio")
                if audio_plugin and audio_plugin.codec:
                    await audio_plugin.codec.clear_audio_queue()
            except Exception as e:
                logger.warning(f"Failed to clear audio queue: {e}", exc_info=True)
        else:
            logger.debug("TTS finished normally; leaving the playback buffer to drain")

        await self.state.set_device_state(DeviceState.LISTENING)

    # -------------------------
    # Operating method
    # -------------------------
    async def connect_protocol(self) -> bool:
        if self.protocol.is_audio_channel_opened():
            return True

        opened = await self.protocol.connect()
        if opened:
            await self.plugins.notify_protocol_connected(self.protocol.protocol)
        return opened

    async def _on_protocol_reconnect_request(self, _=None) -> None:
        """After settings are saved and the MCP tool list changes: if connected, disconnect and reconnect so the server can re-list tools.

        Only refreshes the protocol/tool view; it does not restore the listening session (to avoid entering a listening state immediately after saving settings).
        """
        try:
            if not self.protocol.is_audio_channel_opened():
                logger.info("MCP tool config updated (not connected; applies on next connection)")
                return
            logger.info("MCP tool config updated; reconnecting protocol...")
            # Interrupt the in-progress listen/speak flow to avoid reusing keep_listening after reconnect
            self.state.set_keep_listening(False)
            self._aborted = False
            self._keep_idle_on_channel_open = True
            try:
                await self.protocol.disconnect()
                ok = await self.connect_protocol()
            except Exception:
                self._keep_idle_on_channel_open = False
                raise
            if ok:
                # Double safety: if the OPENED callback order is abnormal, still fall back to idle
                if not self.state.is_idle():
                    await self.state.set_device_state(DeviceState.IDLE)
                logger.info("Protocol reconnected (new tools/list applies at handshake; staying idle)")
            else:
                self._keep_idle_on_channel_open = False
                logger.warning("Protocol reconnect failed; please reconnect manually")
        except Exception as e:
            self._keep_idle_on_channel_open = False
            logger.error(f"Protocol reconnect failed: {e}", exc_info=True)

    async def start_listening(self, mode: ListeningMode) -> None:
        ok = await self.connect_protocol()
        if not ok:
            return

        self.state.set_listening_mode(mode)
        self.state.set_keep_listening(mode != ListeningMode.MANUAL)
        await self.protocol.send_start_listening(mode)
        await self.state.set_device_state(DeviceState.LISTENING)

    async def stop_listening(self) -> None:
        self.state.set_keep_listening(False)
        await self.protocol.send_stop_listening()
        await self.state.set_device_state(DeviceState.IDLE)

    async def start_listening_manual(self) -> None:
        ok = await self.connect_protocol()
        if not ok:
            return

        self.state.set_keep_listening(False)

        if self.state.is_speaking():
            logger.info("Sending interrupt while speaking")
            await self.protocol.send_abort_speaking(None)
            await self.state.set_device_state(DeviceState.IDLE)

        await self.protocol.send_start_listening(ListeningMode.MANUAL)
        await self.state.set_device_state(DeviceState.LISTENING)

    async def stop_listening_manual(self) -> None:
        await self.protocol.send_stop_listening()
        await self.state.set_device_state(DeviceState.IDLE)

    async def start_auto_conversation(self) -> None:
        ok = await self.connect_protocol()
        if not ok:
            return

        mode = (
            ListeningMode.REALTIME
            if self.state.aec_enabled
            else ListeningMode.AUTO_STOP
        )
        self.state.set_listening_mode(mode)
        self.state.set_keep_listening(True)

        await self.protocol.send_start_listening(mode)
        await self.state.set_device_state(DeviceState.LISTENING)

    async def abort_speaking(self, reason: str) -> None:
        # While automatic conversation is still listening, go back to listening after an interrupt; do not stay idle
        if self._aborted:
            logger.debug(f"Already aborted; ignoring duplicate request: {reason}")
            return

        logger.info(f"Aborting speech output: {reason}")
        self._aborted = True
        self.state.set_aborted(True)
        try:
            if self.protocol.is_audio_channel_opened():
                await self.protocol.send_abort_speaking(reason)
        except Exception as e:
            logger.warning(f"Failed to send abort: {e}", exc_info=True)

        if self.state.keep_listening:
            if self.state.listening_mode != ListeningMode.REALTIME:
                if self.protocol.is_audio_channel_opened():
                    try:
                        await self.protocol.send_start_listening(
                            self.state.listening_mode
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to re-listen after interrupt: {e}",
                            exc_info=True,
                        )
            await self.state.set_device_state(DeviceState.LISTENING)
            self._aborted = False
            self.state.set_aborted(False)
            logger.debug("Continuous listening resumed after interrupt")
        else:
            await self.state.set_device_state(DeviceState.IDLE)
