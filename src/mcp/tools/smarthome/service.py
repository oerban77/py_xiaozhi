"""Smart home MCP tools (MQTT / Tasmota).

Ported from the reference Xiaozhi desktop app (mcp/mcp_smarthome.py):
- ``device_status``     — status of every registered smart-home device
- ``device_control``    — control a single device (ON / OFF / TOGGLE)
- ``lights_all``        — switch every light at once
- ``room_control``      — switch every device in a room
- ``discover_devices``  — rescan the MQTT broker for Tasmota devices

The MQTT broker is configured in config.json under ``SMART_HOME``:

    "SMART_HOME": {
        "ENABLED": true,
        "MQTT": {
            "BROKER": "192.168.1.10",      // IP or host, e.g. 656a351f...s1.eu.hivemq.cloud
            "PORT": 1883,                   // 1883 (plain) or 8883 (TLS)
            "USERNAME": "",
            "PASSWORD": "",
            "CLIENT_ID": "xiaozhi-mcp",
            "USE_TLS": false                // required by public brokers such as HiveMQ Cloud
        },
        "DEVICES": [
            {"topic": "sonoff-1000", "power_cmd": "POWER", "name": "Living Room Light",
             "short_name": "Living", "room": "living room", "type": "light"}
        ]
    }

``DEVICES`` is optional: when it is empty, the devices are discovered from the
broker with the Tasmota discovery protocol (``tasmota/discovery/#`` + LWT).
``paho-mqtt`` is a declared dependency of the project.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()

_DISCOVER_TIMEOUT = 8
_CONNECT_WAIT_SEC = 1.5
_POLL_SETTLE_SEC = 2.0

_MANAGER: "SmartHomeManager | None" = None
_MANAGER_LOCK = threading.Lock()


def _load_smarthome_config() -> dict:
    """Read the SMART_HOME section from config.json (never raises)."""
    try:
        cfg = get_config()
    except Exception as e:  # ConfigManager not initialized yet
        logger.warning(f"Smart home: config unavailable: {e}")
        return {}

    try:
        sh = cfg.get_config("SMART_HOME", {}) or {}
    except Exception:
        return {}

    if not isinstance(sh, dict):
        return {}

    mqtt = sh.get("MQTT", {}) or {}
    if not isinstance(mqtt, dict):
        mqtt = {}

    devices = sh.get("DEVICES", []) or []
    if not isinstance(devices, list):
        devices = []

    normalized = []
    for d in devices:
        if not isinstance(d, dict):
            continue
        topic = str(d.get("topic", "") or "").strip()
        if not topic:
            continue
        normalized.append(
            {
                "topic": topic,
                "powerCmd": str(d.get("power_cmd") or d.get("powerCmd") or "POWER"),
                "name": str(d.get("name") or topic),
                "shortName": str(d.get("short_name") or d.get("shortName") or topic)[:11],
                "room": str(d.get("room") or "unknown"),
                "type": str(d.get("type") or "light"),
            }
        )

    broker = str(mqtt.get("BROKER") or mqtt.get("broker") or "").strip()
    try:
        port = int(mqtt.get("PORT") or mqtt.get("port") or 1883)
    except (TypeError, ValueError):
        port = 1883

    # Public brokers (e.g. HiveMQ Cloud) only accept TLS on 8883.
    use_tls = mqtt.get("USE_TLS", mqtt.get("use_tls"))
    if use_tls is None:
        use_tls = port == 8883

    return {
        "enabled": bool(sh.get("ENABLED", sh.get("enabled", False))),
        "broker": broker,
        "port": port,
        "username": str(mqtt.get("USERNAME") or mqtt.get("username") or ""),
        "password": str(mqtt.get("PASSWORD") or mqtt.get("password") or ""),
        "client_id": str(
            mqtt.get("CLIENT_ID") or mqtt.get("client_id") or "xiaozhi-mcp"
        ),
        "use_tls": bool(use_tls),
        "devices": normalized,
    }


class SmartHomeManager:
    """Manages the MQTT connection and controls Tasmota devices."""

    def __init__(self) -> None:
        self.client = None
        self.connected = False
        self.devices: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._broker = ""
        self._port = 1883
        self._user = ""
        self._password = ""
        self._client_id = "xiaozhi-mcp"

        # State per device: {"<topic>_<powerCmd>": {"state": bool, "online": bool}}
        self._device_state: dict[str, dict[str, bool]] = {}
        # Discovery results
        self._disc_configs: dict[str, dict] = {}
        self._disc_lwt: dict[str, str] = {}
        self._disc_power: dict[str, str] = {}

    # ── Connection ──────────────────────────────────────────

    def configure(
        self,
        broker: str,
        port: int,
        user: str,
        password: str,
        client_id: str,
        devices: list[dict[str, Any]],
        use_tls: bool = False,
    ) -> None:
        self._broker = broker
        self._port = port
        self._user = user
        self._password = password
        self._client_id = client_id
        self.devices = list(devices or [])
        self._rebuild_state(self.devices)

        try:
            import paho.mqtt.client as mqtt
        except ImportError:  # pragma: no cover - dependency is declared
            logger.error("paho-mqtt is not installed; smart home tools unavailable")
            return

        try:
            client = mqtt.Client(client_id=client_id)
        except TypeError:
            # paho-mqtt >= 2.0 requires CallbackAPIVersion
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2, client_id=client_id
            )
        if user:
            client.username_pw_set(user, password)
        if use_tls or port == 8883:
            # Public brokers such as HiveMQ Cloud require TLS.
            client.tls_set()
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self.client = client

        try:
            client.connect(broker, port, keepalive=60)
            client.loop_start()
            logger.info(f"Smart home MQTT connecting to {broker}:{port}")
        except Exception as e:
            logger.error(f"Smart home MQTT connect error: {e}")

    def is_configured(self) -> bool:
        return bool(self._broker)

    def _rebuild_state(self, devices: list[dict[str, Any]]) -> None:
        old = self._device_state.copy()
        self._device_state = {}
        for d in devices:
            key = f"{d['topic']}_{d['powerCmd']}"
            self._device_state[key] = old.get(key, {"state": False, "online": False})

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc == 0:
            self.connected = True
            logger.info("Smart home MQTT connected")
            for topic in (
                "stat/+/POWER",
                "stat/+/POWER1",
                "stat/+/POWER2",
                "stat/+/POWER3",
                "stat/+/POWER4",
                "tele/+/LWT",
            ):
                client.subscribe(topic)
            self._poll_all()
        else:
            logger.error(f"Smart home MQTT connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None) -> None:
        self.connected = False
        logger.warning(f"Smart home MQTT disconnected rc={rc}")

    def _on_message(self, client, userdata, msg) -> None:
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="ignore").strip()

        if topic.startswith("stat/"):
            parts = topic.split("/")
            if len(parts) == 3:
                dev_topic, pwr_cmd = parts[1], parts[2]
                key = f"{dev_topic}_{pwr_cmd}"
                if key in self._device_state:
                    self._device_state[key]["state"] = payload == "ON"
                    self._device_state[key]["online"] = True
                self._disc_power[f"{dev_topic}/{pwr_cmd}"] = payload

        elif topic.startswith("tele/") and topic.endswith("/LWT"):
            parts = topic.split("/")
            if len(parts) == 3:
                dev_topic = parts[1]
                online = payload == "Online"
                for key in self._device_state:
                    if key.startswith(dev_topic + "_"):
                        self._device_state[key]["online"] = online
                self._disc_lwt[dev_topic] = payload

        elif topic.startswith("tasmota/discovery/") and topic.endswith("/config"):
            try:
                cfg = json.loads(payload)
                mac = topic.split("/")[2]
                self._disc_configs[mac] = cfg
            except Exception:
                pass

    def _poll_all(self) -> None:
        if not self.connected or not self.client:
            return
        for d in self.devices:
            try:
                self.client.publish(f"cmnd/{d['topic']}/{d['powerCmd']}", "")
            except Exception as e:
                logger.warning(f"Smart home poll failed for {d['topic']}: {e}")

    # ── Discovery ───────────────────────────────────────────

    def discover(self, timeout: int = 5) -> list[dict[str, Any]]:
        """Discover devices via Tasmota discovery + LWT."""
        if not self.connected or not self.client:
            return self.devices

        self._disc_configs = {}
        self._disc_lwt = {}
        self._disc_power = {}

        self.client.subscribe("tasmota/discovery/#")
        self.client.subscribe("tele/+/LWT")
        for i in range(1, 5):
            self.client.subscribe(f"stat/+/POWER{i}")
        self.client.subscribe("stat/+/POWER")

        # Poll to detect multiple relays
        for dev in list(self._disc_lwt.keys()):
            for i in range(1, 5):
                self.client.publish(f"cmnd/{dev}/POWER{i}", "")
        time.sleep(_POLL_SETTLE_SEC)

        deadline = time.monotonic() + timeout
        last_count = 0
        stable_for = 0.0
        while time.monotonic() < deadline:
            time.sleep(0.5)
            current = len(self._disc_configs) + len(self._disc_lwt)
            if current > 0:
                if current == last_count:
                    stable_for += 0.5
                    if stable_for >= 1.0:
                        break
                else:
                    stable_for = 0.0
                    last_count = current

        new_devices = self._build_device_list()
        if new_devices:
            self.devices = new_devices
            self._rebuild_state(new_devices)
            self._poll_all()
        return self.devices

    def _build_device_list(self) -> list[dict[str, Any]]:
        devices: list[dict[str, Any]] = []
        for cfg in self._disc_configs.values():
            t = cfg.get("t", "")
            if not t:
                continue
            fn = cfg.get("fn", [t])
            rl = cfg.get("rl", [1])
            active = [i for i, r in enumerate(rl) if r > 0]
            if len(active) <= 1:
                devices.append(
                    {
                        "topic": t,
                        "powerCmd": "POWER",
                        "name": fn[0] if fn and fn[0] else t,
                        "shortName": t.replace("sonoff-", "")[:11],
                        "room": "unknown",
                        "type": "light",
                    }
                )
            else:
                for idx in active:
                    pwr = f"POWER{idx + 1}"
                    name = (
                        fn[idx]
                        if idx < len(fn) and fn[idx]
                        else f"{t} Relay {idx + 1}"
                    )
                    devices.append(
                        {
                            "topic": t,
                            "powerCmd": pwr,
                            "name": name,
                            "shortName": f"{t.replace('sonoff-', '')[:7]}R{idx + 1}",
                            "room": "unknown",
                            "type": "switch",
                        }
                    )

        tasmota_topics = {cfg.get("t") for cfg in self._disc_configs.values()}
        for dev, _status in self._disc_lwt.items():
            if dev in tasmota_topics:
                continue
            has_multi = any(
                k.startswith(f"{dev}/POWER") and k != f"{dev}/POWER"
                for k in self._disc_power
            )
            if has_multi:
                relay_count = max(
                    (
                        int(k.replace(f"{dev}/POWER", ""))
                        for k in self._disc_power
                        if k.startswith(f"{dev}/POWER")
                        and k != f"{dev}/POWER"
                        and k.replace(f"{dev}/POWER", "").isdigit()
                    ),
                    default=1,
                )
                for i in range(1, relay_count + 1):
                    devices.append(
                        {
                            "topic": dev,
                            "powerCmd": f"POWER{i}",
                            "name": f"{dev} Relay {i}",
                            "shortName": f"{dev.replace('sonoff-', '')[:7]}R{i}",
                            "room": "unknown",
                            "type": "switch",
                        }
                    )
            else:
                devices.append(
                    {
                        "topic": dev,
                        "powerCmd": "POWER",
                        "name": dev,
                        "shortName": dev.replace("sonoff-", "")[:11],
                        "room": "unknown",
                        "type": "light",
                    }
                )
        return devices

    # ── Control ─────────────────────────────────────────────

    def _publish(self, topic: str, payload: str) -> None:
        if not self.connected or not self.client:
            raise RuntimeError("MQTT is not connected")
        self.client.publish(topic, payload)

    def _control(self, idx: int, action: str) -> str:
        if idx < 0 or idx >= len(self.devices):
            raise ValueError(f"Invalid device index: {idx + 1}")
        d = self.devices[idx]
        self._publish(f"cmnd/{d['topic']}/{d['powerCmd']}", action)
        key = f"{d['topic']}_{d['powerCmd']}"
        if action == "ON":
            self._device_state[key]["state"] = True
        elif action == "OFF":
            self._device_state[key]["state"] = False
        elif action == "TOGGLE":
            self._device_state[key]["state"] = not self._device_state[key].get(
                "state", False
            )
        return f"{d['name']} -> {action}"

    def format_status(self) -> str:
        if not self.devices:
            return "No smart home devices registered"

        online_devs = [
            (i, d)
            for i, d in enumerate(self.devices)
            if self._disc_lwt.get(d["topic"], "?") == "Online"
        ]
        if not online_devs:
            lines = [
                f"Smart Home — {len(self.devices)} devices (status unknown)",
                "=" * 45,
            ]
            for i, d in enumerate(self.devices, 1):
                key = f"{d['topic']}_{d['powerCmd']}"
                state = "ON " if self._device_state.get(key, {}).get("state") else "OFF"
                lines.append(f"{i:2}. {d['shortName']:<12} [{d['type']:<7}] {state}")
            return "\n".join(lines)

        lines = [
            f"Smart Home — {len(online_devs)} devices online",
            "=" * 45,
        ]
        for i, (_, d) in enumerate(online_devs, 1):
            key = f"{d['topic']}_{d['powerCmd']}"
            st = self._device_state.get(key, {})
            state = "ON " if st.get("state") else "OFF"
            online = self._disc_lwt.get(d["topic"], "?")
            lines.append(
                f"{i:2}. {d['shortName']:<12} [{d['type']:<7}] {state}  {online:<8} {d['room']}"
            )
        return "\n".join(lines)

    # ── Public tool methods ─────────────────────────────────

    def device_control(self, device_index: int, action: str) -> str:
        return self._control(device_index - 1, action)

    def lights_all(self, action: str) -> str:
        count = 0
        for i, d in enumerate(self.devices):
            if d.get("type") == "light":
                self._control(i, action)
                count += 1
        return f"All {count} lights -> {action}"

    def room_control(self, room: str, action: str) -> str:
        count = 0
        for i, d in enumerate(self.devices):
            if d.get("room", "").lower() == room.lower():
                self._control(i, action)
                count += 1
        if count == 0:
            raise ValueError(f"Room '{room}' not found")
        return f"{count} devices in '{room}' -> {action}"


def _get_manager() -> SmartHomeManager:
    """Return the initialized singleton manager (creates it on first use)."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            return _MANAGER

        cfg = _load_smarthome_config()
        manager = SmartHomeManager()
        if cfg.get("enabled") and cfg.get("broker"):
            manager.configure(
                broker=cfg["broker"],
                port=cfg["port"],
                user=cfg["username"],
                password=cfg["password"],
                client_id=cfg["client_id"],
                devices=cfg["devices"],
                use_tls=cfg.get("use_tls", False),
            )
            # The connection is established asynchronously by the MQTT loop;
            # wait briefly so the first tool call sees a usable state.
            time.sleep(_CONNECT_WAIT_SEC)
            logger.info(
                f"Smart home initialized: {len(cfg['devices'])} configured device(s), "
                f"broker {cfg['broker']}:{cfg['port']}"
            )
        else:
            logger.info(
                "Smart home is disabled or no broker is configured "
                "(SMART_HOME.ENABLED / SMART_HOME.MQTT.BROKER)"
            )
        _MANAGER = manager
        return manager


def _manager_status(manager: SmartHomeManager, cfg: dict) -> str:
    if not cfg.get("enabled"):
        return (
            "Smart home is disabled. Enable it in settings "
            "(SMART_HOME.ENABLED = true) and configure the MQTT broker."
        )
    if not cfg.get("broker"):
        return "No MQTT broker configured (SMART_HOME.MQTT.BROKER is empty)."
    if not manager.connected:
        return f"Not connected to the MQTT broker ({manager._broker}:{manager._port})"
    return ""


# ── MCP tool handlers ──────────────────────────────────────


async def device_status(args: dict) -> str:
    cfg = _load_smarthome_config()
    manager = _get_manager()
    err = _manager_status(manager, cfg)
    if err:
        return err
    return await asyncio.to_thread(manager.format_status)


async def device_control(args: dict) -> str:
    cfg = _load_smarthome_config()
    manager = _get_manager()
    err = _manager_status(manager, cfg)
    if err:
        return err

    try:
        idx = int(args.get("deviceIndex") or 0)
    except (TypeError, ValueError):
        return "Error: deviceIndex must be an integer."

    action = str(args.get("action") or "").upper()
    if action not in ("ON", "OFF", "TOGGLE"):
        return "Error: action must be ON, OFF or TOGGLE."

    try:
        msg = await asyncio.to_thread(manager.device_control, idx, action)
        return f"OK {msg}"
    except Exception as e:
        return f"Error: {e}"


async def lights_all(args: dict) -> str:
    cfg = _load_smarthome_config()
    manager = _get_manager()
    err = _manager_status(manager, cfg)
    if err:
        return err

    action = str(args.get("action") or "").upper()
    if action not in ("ON", "OFF"):
        return "Error: action must be ON or OFF."

    try:
        msg = await asyncio.to_thread(manager.lights_all, action)
        return f"OK {msg}"
    except Exception as e:
        return f"Error: {e}"


async def room_control(args: dict) -> str:
    cfg = _load_smarthome_config()
    manager = _get_manager()
    err = _manager_status(manager, cfg)
    if err:
        return err

    room = str(args.get("room") or "").strip()
    action = str(args.get("action") or "").upper()
    if not room:
        return "Error: room is required."
    if action not in ("ON", "OFF"):
        return "Error: action must be ON or OFF."

    try:
        msg = await asyncio.to_thread(manager.room_control, room, action)
        return f"OK {msg}"
    except Exception as e:
        return f"Error: {e}"


async def discover_devices(args: dict) -> str:
    cfg = _load_smarthome_config()
    manager = _get_manager()
    err = _manager_status(manager, cfg)
    if err:
        return err

    try:
        devices = await asyncio.to_thread(manager.discover, 5)
    except Exception as e:
        return f"Error: {e}"

    if not devices:
        return (
            "No devices found. Make sure the Tasmota devices are connected "
            "to the same MQTT broker."
        )
    return f"Found {len(devices)} devices:\n" + manager.format_status()
