"""Smart home MCP tools (generic MQTT, not Tasmota-only).

Tools (unchanged names, so existing callers keep working):
- ``device_status``     — status of every known smart-home device
- ``device_control``    — control a single device (ON / OFF / TOGGLE)
- ``lights_all``        — switch every controllable light at once
- ``room_control``      — switch every controllable device in a room
- ``discover_devices``  — rescan the MQTT broker for devices
- ``device_details``    — full topics/protocol for one device (debugging)

Discovery protocols (all read-only during the scan — nothing is
published to an unknown topic just because it was discovered):

1. Tasmota (kept, as before): ``tasmota/discovery/#/config``,
   ``tele/+/LWT``, ``stat/+/POWER*``, control ``cmnd/<topic>/<cmd>``.
2. Home Assistant discovery: ``homeassistant/<component>/.../config``.
   The config JSON itself carries ``state_topic``, ``command_topic``,
   ``payload_on``/``payload_off`` and ``availability_topic``, so any
   firmware that speaks HA discovery (ESPHome, Zigbee2MQTT, Shelly with
   HA discovery enabled, Tasmota with SetOption19, ...) is supported
   without a per-vendor code path.
3. Zigbee2MQTT (direct, no HA needed): ``zigbee2mqtt/bridge/devices``
   for the device list, ``zigbee2mqtt/<name>`` for JSON state and
   ``zigbee2mqtt/<name>/availability``. Control is
   ``zigbee2mqtt/<name>/set`` with ``{"state": "ON"|"OFF"}``.
4. Shelly: Gen1 ``shellies/<id>/relay/<n>`` (state) /
   ``shellies/<id>/relay/<n>/command`` (``on``/``off``) and
   Gen2 ``<prefix>/status/switch:<n>`` / ``<prefix>/status/light:<n>``
   (JSON ``output``) controlled via ``<prefix>/rpc``
   ``Switch.Set`` / ``Light.Set``.
5. Generic MQTT: any other retained / observed topic that looks like a
   state topic (plain ON/OFF, a JSON ``state``/``output``/``power``
   field, or a plain sensor value such as ``vacuum/status`` or
   ``xiaomi/<device>/status``). Generic devices are **read-only**
   unless a command topic is known from one of the protocols above
   or from explicit configuration — the code never guesses a command
   topic and publishes to it. That is a deliberate safety rule:
   publishing a guessed payload to a sensor, a vacuum command topic
   or an openHAB item could trigger a real action.

Configured devices (``SMART_HOME.DEVICES`` in config.json) keep the
old Tasmota shape working::

    {"topic": "sonoff-1000", "power_cmd": "POWER", "name": "...",
     "short_name": "...", "room": "living room", "type": "light"}

and gain a generic shape for everything else — this is also how an
openHAB item or a custom topic pair is wired in, because openHAB
MQTT topics are user-defined, not a fixed protocol::

    {"name": "Vacuum", "room": "living room", "type": "sensor",
     "state_topic": "vacuum/status"}
    {"name": "Vacuum clean", "room": "living room", "type": "switch",
     "protocol": "generic",
     "state_topic": "vacuum/status",
     "command_topic": "vacuum/command/clean",
     "payload_on": "START", "payload_off": "STOP"}
    {"name": "Bedroom lamp", "room": "bedroom", "type": "light",
     "state_topic": "home/bedroom/lamp/state",
     "command_topic": "home/bedroom/lamp/set"}

The broker itself is configured in config.json under ``SMART_HOME``::

    "SMART_HOME": {
        "MQTT": {
            "BROKER": "192.168.1.10", "PORT": 1883,
            "USERNAME": "", "PASSWORD": "",
            "CLIENT_ID": "xiaozhi-mcp", "USE_TLS": false
        },
        "DEVICES": [...],
        "GENERIC_SCAN": true
    }

``GENERIC_SCAN`` (default true) enables step 5 during
``discover_devices``. It subscribes to ``#`` for a few seconds, capped
at 1000 distinct topics, and ignores ``$SYS/#``. On a very busy
broker set it to false and rely on protocols 1-4 plus configuration.

``paho-mqtt`` is a declared dependency of the project.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()

_DISCOVER_TIMEOUT = 8
_CONNECT_WAIT_SEC = 1.5
_POLL_SETTLE_SEC = 2.0
_GENERIC_SCAN_MAX_TOPICS = 1000

_MANAGER: "SmartHomeManager | None" = None
_MANAGER_LOCK = threading.Lock()

_ON_VALUES = {"on", "true", "1", "yes"}
_OFF_VALUES = {"off", "false", "0", "no"}
# "online"/"offline" are availability words, not switch states: a
# "xiaomi/<device>/status" payload of "Online - 192.168.x.x" must not
# become an ON switch. Availability is handled separately, by exact
# availability_on/off comparison in _on_message.

# Topics that are never state topics, even in a generic scan.
_SKIP_TOPIC_SUFFIXES = (
    "/config", "/announce", "/set", "/command", "/cmnd",
    "/rpc", "/availability", "/online", "/LWT", "/info",
)
_SKIP_TOPIC_PREFIXES = ("$SYS/", "homeassistant/", "tasmota/discovery/")


# ── Small pure helpers (unit-testable without a broker) ──────────────


def _s(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _json_value(payload: str, key: str) -> Any:
    """Extract ``key`` (dot path, e.g. ``state`` or ``a.b``) from JSON."""
    try:
        data = json.loads(payload)
    except Exception:
        return None
    cur: Any = data
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _bool_from_value(value: Any, payload_on: str = "ON",
                      payload_off: str = "OFF") -> bool | None:
    """Map a payload value to True/False, or None when it is not binary."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    text = _s(value).strip()
    if not text:
        return None
    if text == payload_on or text.lower() in _ON_VALUES:
        return True
    if text == payload_off or text.lower() in _OFF_VALUES:
        return False
    return None


def parse_state(payload: str, state_key: str | None = None,
                payload_on: str = "ON", payload_off: str = "OFF"
                ) -> tuple[bool | None, str]:
    """Return (binary state or None, raw payload text) for a state message.

    JSON payloads are read through ``state_key`` when given, otherwise
    through a ``state`` field when the payload is an object that has
    one. Anything else is a sensor value: state is None, raw is kept
    so ``device_status`` can still show it (e.g. a vacuum status
    string such as ``Mengisi daya``).
    """
    raw = payload.strip()
    if state_key:
        value = _json_value(raw, state_key)
        if value is not None:
            return _bool_from_value(value, payload_on, payload_off), raw
    if raw.startswith("{"):
        value = _json_value(raw, "state")
        if value is not None:
            return _bool_from_value(value, payload_on, payload_off), raw
        # JSON sensor object (temperature, power, ...): keep raw.
        return None, raw
    return _bool_from_value(raw, payload_on, payload_off), raw


def _short_name(name: str, fallback: str) -> str:
    base = (name or fallback or "").strip()
    return base[:11] if base else fallback[:11]


def _device_id(protocol: str, state_topic: str, command_topic: str = "",
               topic: str = "", power_cmd: str = "") -> str:
    if protocol == "tasmota" and topic:
        return f"tasmota:{topic}:{power_cmd or 'POWER'}"
    if state_topic:
        return f"{protocol}:{state_topic}"
    return f"{protocol}:{topic}:{power_cmd}"


def make_device(*, protocol: str, name: str, state_topic: str = "",
                command_topic: str = "", topic: str = "",
                power_cmd: str = "POWER", room: str = "unknown",
                type_: str = "switch", payload_on: str = "ON",
                payload_off: str = "OFF", state_key: str | None = None,
                availability_topic: str = "",
                availability_on: str = "Online",
                availability_off: str = "Offline",
                poll_topic: str = "", controllable: bool | None = None,
                source: str = "discovered") -> dict[str, Any]:
    """Build one canonical device dict (all protocols share this shape)."""
    if controllable is None:
        controllable = bool(command_topic)
    return {
        "id": _device_id(protocol, state_topic, command_topic, topic,
                         power_cmd),
        "protocol": protocol,
        "topic": topic,
        "powerCmd": power_cmd,
        "name": name or state_topic or topic,
        "shortName": _short_name(name, state_topic or topic),
        "room": room or "unknown",
        "type": type_,
        "state_topic": state_topic,
        "command_topic": command_topic,
        "payload_on": payload_on,
        "payload_off": payload_off,
        "state_key": state_key,
        "availability_topic": availability_topic,
        "availability_on": availability_on,
        "availability_off": availability_off,
        "poll_topic": poll_topic,
        "controllable": bool(controllable),
        "source": source,
    }


def tasmota_device(topic: str, power_cmd: str, name: str,
                   room: str = "unknown", type_: str = "light",
                   source: str = "discovered") -> dict[str, Any]:
    return make_device(
        protocol="tasmota", name=name, topic=topic, power_cmd=power_cmd,
        state_topic=f"stat/{topic}/{power_cmd}",
        command_topic=f"cmnd/{topic}/{power_cmd}",
        availability_topic=f"tele/{topic}/LWT",
        availability_on="Online", availability_off="Offline",
        poll_topic=f"cmnd/{topic}/{power_cmd}",
        room=room, type_=type_, source=source,
    )


def ha_device_from_config(config_topic: str, cfg: dict) -> dict[str, Any] | None:
    """Parse one Home Assistant discovery config into a device (or None)."""
    parts = config_topic.split("/")
    if len(parts) < 3 or parts[0] != "homeassistant":
        return None
    component = parts[1]
    state_topic = _s(cfg.get("state_topic")).strip()
    if not state_topic:
        return None
    command_topic = _s(cfg.get("command_topic")).strip()
    name = _s(cfg.get("name")).strip()
    device_info = cfg.get("device") if isinstance(cfg.get("device"), dict) else {}
    if not name:
        name = _s(device_info.get("name")).strip() or state_topic
    device_class = _s(cfg.get("device_class")).strip()
    if component in ("light", "switch"):
        type_ = component
    elif component == "binary_sensor":
        type_ = "sensor"
    elif component == "sensor":
        type_ = "sensor"
    else:
        # cover / fan / lock / climate / ...: their command payloads are
        # not ON/OFF (open/close, ...), so v1 shows them read-only.
        type_ = component
        command_topic = ""
    availability_topic = _s(cfg.get("availability_topic")).strip()
    payload_available = _s(cfg.get("payload_available"), "online")
    payload_not_available = _s(cfg.get("payload_not_available"), "offline")
    # HA value_template may name the JSON key, e.g. "{{ value_json.state }}".
    state_key = None
    template = _s(cfg.get("value_template"))
    match = re.search(r"value_json\.([A-Za-z0-9_.-]+)", template)
    if match:
        state_key = match.group(1)
    return make_device(
        protocol="homeassistant", name=name, state_topic=state_topic,
        command_topic=command_topic,
        payload_on=_s(cfg.get("payload_on"), "ON"),
        payload_off=_s(cfg.get("payload_off"), "OFF"),
        state_key=state_key, availability_topic=availability_topic,
        availability_on=payload_available or "online",
        availability_off=payload_not_available or "offline",
        room="unknown", type_=type_,
        controllable=bool(command_topic) and component in ("light", "switch"),
    )


def z2m_device_from_entry(entry: dict) -> dict[str, Any] | None:
    """Parse one zigbee2mqtt/bridge/devices entry (or None if not usable).

    Only entries whose exposes list contains a light/switch ``state``
    feature become controllable; everything else with a friendly name
    becomes a read-only sensor, so a temperature sensor is never
    presented as a switch.
    """
    name = _s(entry.get("friendly_name")).strip()
    if not name:
        return None
    state_topic = f"zigbee2mqtt/{name}"
    definition = entry.get("definition") if isinstance(
        entry.get("definition"), dict) else {}
    exposes = definition.get("exposes") if isinstance(
        definition.get("exposes"), list) else []
    kind: str | None = None
    for expose in exposes:
        if not isinstance(expose, dict):
            continue
        if expose.get("type") in ("light", "switch"):
            features = expose.get("features") if isinstance(
                expose.get("features"), list) else []
            if any(isinstance(f, dict) and f.get("property") == "state"
                   for f in features):
                kind = str(expose["type"])
                break
    if kind:
        return make_device(
            protocol="zigbee2mqtt", name=name, state_topic=state_topic,
            command_topic=f"{state_topic}/set", state_key="state",
            availability_topic=f"{state_topic}/availability",
            availability_on="online", availability_off="offline",
            type_=kind,
        )
    return make_device(
        protocol="zigbee2mqtt", name=name, state_topic=state_topic,
        state_key=None, availability_topic=f"{state_topic}/availability",
        availability_on="online", availability_off="offline",
        type_="sensor", controllable=False,
    )


def shelly_gen1_device(device_id: str, relay: int, model: str = "") -> dict[str, Any]:
    base = f"shellies/{device_id}"
    return make_device(
        protocol="shelly", name=f"{model or device_id} relay {relay}",
        state_topic=f"{base}/relay/{relay}",
        command_topic=f"{base}/relay/{relay}/command",
        payload_on="on", payload_off="off",
        availability_topic=f"{base}/online",
        availability_on="true", availability_off="false",
        type_="switch",
    )


def shelly_gen2_device(prefix: str, kind: str, index: int) -> dict[str, Any]:
    """Gen2 switch/light device. Control goes through ``<prefix>/rpc``."""
    return make_device(
        protocol="shelly", name=f"{prefix} {kind} {index}",
        state_topic=f"{prefix}/status/{kind}:{index}",
        command_topic=f"{prefix}/rpc", state_key="output",
        type_="light" if kind == "light" else "switch",
    )


def generic_device_from_topic(topic: str, payload: str) -> dict[str, Any] | None:
    """Classify one observed topic as a generic device (or None).

    Returns a read-only device. Callers must never turn this into a
    controllable device by guessing a command topic.
    """
    if topic.startswith(_SKIP_TOPIC_PREFIXES):
        return None
    if any(topic.endswith(suffix) for suffix in _SKIP_TOPIC_SUFFIXES):
        return None
    if topic.startswith("zigbee2mqtt/bridge") or topic.startswith("shellies/"):
        return None
    parts = [p for p in topic.split("/") if p]
    if len(parts) < 2:
        return None
    state, _raw = parse_state(payload)
    if state is not None:
        type_ = "light" if any(
            word in topic.lower() for word in ("light", "lamp", "lampu")) else "switch"
        name = parts[-2].replace("_", " ").replace("-", " ") if len(parts) >= 2 else topic
    else:
        type_ = "sensor"
        if len(parts) >= 3 and parts[0].lower() == "xiaomi":
            name = parts[1].replace("_", " ").replace("-", " ")
        elif parts[-1].lower() in ("status", "state") and len(parts) >= 2:
            name = parts[-2].replace("_", " ").replace("-", " ")
        else:
            # e.g. vacuum/battery -> "vacuum battery", so two sensors
            # of one device do not share one indistinguishable name.
            name = " ".join(p.replace("_", " ").replace("-", " ") for p in parts)
    return make_device(
        protocol="generic", name=name, state_topic=topic,
        type_=type_, controllable=False,
    )


def control_message(device: dict[str, Any], action: str,
                    current_state: bool | None = None
                    ) -> tuple[str, str]:
    """Return (topic, payload) for ``action`` on ``device``.

    Raises ValueError for read-only devices and for TOGGLE when the
    current state is unknown (inverting an unknown state would be a
    guess, not a toggle).
    """
    if not device.get("controllable"):
        raise ValueError(
            f"Device '{device.get('name')}' is read-only "
            f"(protocol={device.get('protocol')}, "
            f"state_topic={device.get('state_topic')}); "
            "configure a command_topic to make it controllable."
        )
    action = action.upper()
    if action == "TOGGLE":
        if current_state is None:
            raise ValueError(
                "Cannot TOGGLE: current state is unknown, use ON or OFF.")
        action = "OFF" if current_state else "ON"
    if action not in ("ON", "OFF"):
        raise ValueError("action must be ON, OFF or TOGGLE")
    protocol = device.get("protocol")
    if protocol == "zigbee2mqtt":
        return device["command_topic"], json.dumps(
            {"state": device["payload_on"] if action == "ON"
             else device["payload_off"]})
    if protocol == "shelly" and device["command_topic"].endswith("/rpc"):
        # Gen2 RPC: Switch.Set / Light.Set. state_topic looks like
        # "<prefix>/status/switch:0" or "<prefix>/status/light:0".
        match = re.search(r"/status/(switch|light):(\d+)$",
                          device.get("state_topic", ""))
        kind, index = ("switch", 0)
        if match:
            kind, index = match.group(1), int(match.group(2))
        method = "Light.Set" if kind == "light" else "Switch.Set"
        return device["command_topic"], json.dumps({
            "id": 0, "src": "xiaozhi",
            "method": method,
            "params": {"id": index, "on": action == "ON"},
        })
    payload = device["payload_on"] if action == "ON" else device["payload_off"]
    return device["command_topic"], payload


def merge_devices(configured: list[dict[str, Any]],
                  discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge lists by device id: configured entries win on name/room/type.

    Unlike the old code, discovery never deletes a configured device
    that simply was not seen in this scan — it is kept, marked by
    its (unknown/offline) availability, instead of vanishing.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for dev in discovered:
        by_id[dev["id"]] = dev
    for dev in configured:
        existing = by_id.get(dev["id"])
        if existing is None:
            by_id[dev["id"]] = dev
        else:
            merged = dict(existing)
            for key in ("name", "shortName", "room", "type"):
                if dev.get(key):
                    merged[key] = dev[key]
            merged["source"] = "configured"
            by_id[dev["id"]] = merged
    # Configured devices keep their order first (stable device numbers
    # for device_control); newly discovered devices follow.
    order = [dev["id"] for dev in configured]
    order += [dev["id"] for dev in discovered
              if dev["id"] not in {d["id"] for d in configured}]
    return [by_id[dev_id] for dev_id in order]


# ── Configuration ──────────────────────────────────────────────────


def _normalize_configured_device(d: dict) -> dict[str, Any] | None:
    """Normalize one SMART_HOME.DEVICES entry (old and new shapes)."""
    if not isinstance(d, dict):
        return None
    protocol = _s(d.get("protocol")).strip().lower()
    state_topic = _s(d.get("state_topic") or d.get("stateTopic")).strip()
    command_topic = _s(d.get("command_topic") or d.get("commandTopic")).strip()
    topic = _s(d.get("topic")).strip()
    power_cmd = _s(d.get("power_cmd") or d.get("powerCmd") or "POWER")
    if not state_topic and not topic:
        return None
    if not protocol:
        protocol = "tasmota" if topic and not state_topic else "generic"
    if protocol == "tasmota" and topic:
        dev = tasmota_device(
            topic, power_cmd,
            _s(d.get("name") or topic),
            room=_s(d.get("room") or "unknown"),
            type_=_s(d.get("type") or "light"),
            source="configured",
        )
        if d.get("short_name") or d.get("shortName"):
            dev["shortName"] = _s(d.get("short_name") or d.get("shortName"))[:11]
        return dev
    name = _s(d.get("name") or state_topic or topic)
    controllable = d.get("controllable")
    if controllable is not None:
        controllable = bool(controllable)
    return make_device(
        protocol=protocol, name=name, state_topic=state_topic,
        command_topic=command_topic, topic=topic, power_cmd=power_cmd,
        room=_s(d.get("room") or "unknown"),
        type_=_s(d.get("type") or "switch"),
        payload_on=_s(d.get("payload_on") or d.get("payloadOn"), "ON"),
        payload_off=_s(d.get("payload_off") or d.get("payloadOff"), "OFF"),
        state_key=_s(d.get("state_key") or d.get("stateKey")) or None,
        availability_topic=_s(
            d.get("availability_topic") or d.get("availabilityTopic")),
        availability_on=_s(
            d.get("availability_on") or d.get("availabilityOn"), "Online"),
        availability_off=_s(
            d.get("availability_off") or d.get("availabilityOff"), "Offline"),
        poll_topic=_s(d.get("poll_topic") or d.get("pollTopic")),
        controllable=controllable, source="configured",
    )


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
    devices_raw = sh.get("DEVICES", []) or []
    if not isinstance(devices_raw, list):
        devices_raw = []
    devices = []
    for d in devices_raw:
        dev = _normalize_configured_device(d)
        if dev is not None:
            devices.append(dev)
    broker = _s(mqtt.get("BROKER") or mqtt.get("broker")).strip()
    try:
        port = int(mqtt.get("PORT") or mqtt.get("port") or 1883)
    except (TypeError, ValueError):
        port = 1883
    use_tls = mqtt.get("USE_TLS", mqtt.get("use_tls"))
    if use_tls is None:
        use_tls = port == 8883
    generic_scan = sh.get("GENERIC_SCAN", sh.get("generic_scan"))
    if generic_scan is None:
        generic_scan = True
    return {
        "broker": broker,
        "port": port,
        "username": _s(mqtt.get("USERNAME") or mqtt.get("username")),
        "password": _s(mqtt.get("PASSWORD") or mqtt.get("password")),
        "client_id": _s(mqtt.get("CLIENT_ID") or mqtt.get("client_id"),
                        "xiaozhi-mcp"),
        "use_tls": bool(use_tls),
        "devices": devices,
        "generic_scan": bool(generic_scan),
    }


# ── Manager ────────────────────────────────────────────────────────


class SmartHomeManager:
    """Manages the MQTT connection and controls devices of any protocol."""

    def __init__(self) -> None:
        self.client = None
        self.connected = False
        self.devices: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._broker = ""
        self._port = 1883
        self._user = ""
        self._password = ""
        self._client_id = "xiaozhi-mcp"
        self._config_signature = None
        self._generic_scan = True
        # State per device id: {"state": bool|None, "raw": str,
        # "online": bool|None}
        self._device_state: dict[str, dict[str, Any]] = {}
        # Discovery buffers (reset on every discover() call).
        self._disc_configs: dict[str, dict] = {}
        self._disc_lwt: dict[str, str] = {}
        self._disc_power: dict[str, str] = {}
        self._ha_configs: dict[str, dict] = {}
        self._z2m_entries: dict[str, dict] = {}
        self._z2m_state: dict[str, str] = {}
        self._z2m_availability: dict[str, str] = {}
        self._shelly_models: dict[str, str] = {}
        self._shelly_relays: dict[str, str] = {}
        self._shelly_online: dict[str, str] = {}
        self._shelly_gen2_state: dict[str, str] = {}
        self._generic_topics: dict[str, str] = {}
        self._generic_scan_active = False

    # ── Connection ──────────────────────────────────────────

    def shutdown(self) -> None:
        """Stop the previous MQTT client before applying new broker settings."""
        client = self.client
        self.client = None
        self.connected = False
        if client is None:
            return
        try:
            client.disconnect()
        except Exception as e:
            logger.debug(f"Smart home MQTT disconnect failed: {e}")
        try:
            client.loop_stop()
        except Exception as e:
            logger.debug(f"Smart home MQTT loop stop failed: {e}")

    def configure(self, broker: str, port: int, user: str, password: str,
                  client_id: str, devices: list[dict[str, Any]],
                  use_tls: bool = False, generic_scan: bool = True) -> None:
        self._broker = broker
        self._port = port
        self._user = user
        self._password = password
        self._client_id = client_id
        self._generic_scan = generic_scan
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
                mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if user:
            client.username_pw_set(user, password)
        if use_tls or port == 8883:
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
        with self._lock:
            old = self._device_state.copy()
            self._device_state = {}
            for d in devices:
                self._device_state[d["id"]] = old.get(
                    d["id"], {"state": None, "raw": "", "online": None})

    def _set_state(self, dev_id: str, state: bool | None, raw: str) -> None:
        with self._lock:
            entry = self._device_state.setdefault(
                dev_id, {"state": None, "raw": "", "online": None})
            entry["state"] = state
            entry["raw"] = raw

    def _set_online(self, dev_id: str, online: bool | None) -> None:
        with self._lock:
            entry = self._device_state.setdefault(
                dev_id, {"state": None, "raw": "", "online": None})
            entry["online"] = online

    def _devices_by_state_topic(self) -> dict[str, dict[str, Any]]:
        return {d["state_topic"]: d for d in self.devices if d.get("state_topic")}

    def _devices_by_availability_topic(self) -> dict[str, dict[str, Any]]:
        return {d["availability_topic"]: d for d in self.devices
                if d.get("availability_topic")}

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc == 0:
            self.connected = True
            logger.info("Smart home MQTT connected")
            for topic in (
                "stat/+/POWER", "stat/+/POWER1", "stat/+/POWER2",
                "stat/+/POWER3", "stat/+/POWER4", "tele/+/LWT",
                "homeassistant/#",
                "zigbee2mqtt/bridge/devices",
                "zigbee2mqtt/+/availability",
                "zigbee2mqtt/+",
                "shellies/+/announce", "shellies/+/online",
                "shellies/+/relay/+",
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
        with self._lock:
            # 1. Known device state / availability (exact topic match).
            by_state = self._devices_by_state_topic()
            if topic in by_state:
                dev = by_state[topic]
                state, raw = parse_state(
                    payload, dev.get("state_key"),
                    dev.get("payload_on", "ON"), dev.get("payload_off", "OFF"))
                self._set_state(dev["id"], state, raw)
                return
            by_avail = self._devices_by_availability_topic()
            if topic in by_avail:
                dev = by_avail[topic]
                online = None
                if payload == dev.get("availability_on"):
                    online = True
                elif payload == dev.get("availability_off"):
                    online = False
                elif payload.lower() in ("online", "true", "1"):
                    online = True
                elif payload.lower() in ("offline", "false", "0"):
                    online = False
                self._set_online(dev["id"], online)
                return
            # 2. Tasmota patterns (also feed discovery buffers).
            if topic.startswith("stat/"):
                parts = topic.split("/")
                if len(parts) == 3:
                    self._disc_power[f"{parts[1]}/{parts[2]}"] = payload
                    for dev in self.devices:
                        if (dev.get("protocol") == "tasmota"
                                and dev.get("topic") == parts[1]
                                and dev.get("powerCmd") == parts[2]):
                            self._set_state(
                                dev["id"], payload == "ON", payload)
                            self._set_online(dev["id"], True)
                return
            if topic.startswith("tele/") and topic.endswith("/LWT"):
                parts = topic.split("/")
                if len(parts) == 3:
                    self._disc_lwt[parts[1]] = payload
                    for dev in self.devices:
                        if dev.get("topic") == parts[1]:
                            self._set_online(dev["id"], payload == "Online")
                return
            if topic.startswith("tasmota/discovery/") and topic.endswith("/config"):
                try:
                    cfg = json.loads(payload)
                    self._disc_configs[topic.split("/")[2]] = cfg
                except Exception:
                    pass
                return
            # 3. Home Assistant discovery configs (retained).
            if topic.startswith("homeassistant/") and topic.endswith("/config"):
                try:
                    cfg = json.loads(payload)
                    if isinstance(cfg, dict):
                        self._ha_configs[topic] = cfg
                except Exception:
                    pass
                return
            # 4. Zigbee2MQTT.
            if topic == "zigbee2mqtt/bridge/devices":
                try:
                    entries = json.loads(payload)
                    if isinstance(entries, list):
                        for entry in entries:
                            if isinstance(entry, dict):
                                name = _s(entry.get("friendly_name")).strip()
                                if name:
                                    self._z2m_entries[name] = entry
                except Exception:
                    pass
                return
            if topic.startswith("zigbee2mqtt/"):
                rest = topic[len("zigbee2mqtt/"):]
                if rest.endswith("/availability") and "/" not in rest[:-13]:
                    self._z2m_availability[rest[:-13]] = payload
                    return
                if "/" not in rest and rest not in ("bridge",):
                    self._z2m_state[rest] = payload
                    return
            # 5. Shelly.
            if topic.startswith("shellies/"):
                parts = topic.split("/")
                if len(parts) == 4 and parts[2] == "relay":
                    self._shelly_relays[f"{parts[1]}/{parts[3]}"] = payload
                    return
                if len(parts) == 3 and parts[2] == "online":
                    self._shelly_online[parts[1]] = payload
                    return
                if len(parts) == 3 and parts[2] == "announce":
                    try:
                        info = json.loads(payload)
                        if isinstance(info, dict) and info.get("model"):
                            self._shelly_models[parts[1]] = _s(info["model"])
                    except Exception:
                        pass
                    return
            match = re.match(r"^(.+)/status/(switch|light):(\d+)$", topic)
            if match and match.group(1).split("/")[0].startswith("shelly"):
                self._shelly_gen2_state[topic] = payload
                return
            # 6. Generic scan: record, never publish, never guess.
            if self._generic_scan_active:
                if topic.startswith("$SYS/"):
                    return
                if len(self._generic_topics) < _GENERIC_SCAN_MAX_TOPICS:
                    self._generic_topics[topic] = payload

    def _poll_all(self) -> None:
        if not self.connected or not self.client:
            return
        for d in self.devices:
            poll_topic = d.get("poll_topic") or (
                f"cmnd/{d['topic']}/{d['powerCmd']}"
                if d.get("protocol") == "tasmota" else "")
            if not poll_topic:
                continue
            try:
                self.client.publish(poll_topic, "")
            except Exception as e:
                logger.warning(f"Smart home poll failed for {d['name']}: {e}")

    # ── Discovery ───────────────────────────────────────────

    def discover(self, timeout: int = 5) -> list[dict[str, Any]]:
        """Discover devices via every supported protocol, then merge.

        Configured devices are never dropped (see merge_devices).
        """
        if not self.connected or not self.client:
            return self.devices
        with self._lock:
            self._disc_configs = {}
            self._disc_lwt = {}
            self._disc_power = {}
            self._ha_configs = {}
            self._z2m_entries = {}
            self._z2m_state = {}
            self._z2m_availability = {}
            self._shelly_models = {}
            self._shelly_relays = {}
            self._shelly_online = {}
            self._shelly_gen2_state = {}
            self._generic_topics = {}
            self._generic_scan_active = bool(self._generic_scan)
        for topic in (
            "tasmota/discovery/#", "tele/+/LWT",
            "stat/+/POWER", "stat/+/POWER1", "stat/+/POWER2",
            "stat/+/POWER3", "stat/+/POWER4",
            "homeassistant/#",
            "zigbee2mqtt/bridge/devices", "zigbee2mqtt/+/availability",
            "zigbee2mqtt/+",
            "shellies/+/announce", "shellies/+/online", "shellies/+/relay/+",
        ):
            self.client.subscribe(topic)
        if self._generic_scan:
            self.client.subscribe("#")
        # Retained configs (HA, Tasmota discovery, Z2M bridge list)
        # arrive immediately; then poll the Tasmota devices that are
        # actually online so multi-relay devices are detected. The old
        # code polled here before any LWT had been received, so that
        # poll loop always ran over an empty list.
        time.sleep(_POLL_SETTLE_SEC)
        for dev_topic in list(self._disc_lwt.keys()):
            for i in range(1, 5):
                self.client.publish(f"cmnd/{dev_topic}/POWER{i}", "")
        deadline = time.monotonic() + timeout
        last_count = 0
        stable_for = 0.0
        while time.monotonic() < deadline:
            time.sleep(0.5)
            with self._lock:
                current = (
                    len(self._disc_configs) + len(self._disc_lwt)
                    + len(self._ha_configs) + len(self._z2m_entries)
                    + len(self._shelly_relays) + len(self._shelly_gen2_state)
                    + len(self._generic_topics))
            if current > 0:
                if current == last_count:
                    stable_for += 0.5
                    if stable_for >= 1.0:
                        break
                else:
                    stable_for = 0.0
                    last_count = current
        with self._lock:
            self._generic_scan_active = False
        new_devices = self._build_device_list()
        if new_devices:
            self.devices = merge_devices(self.devices, new_devices)
            self._rebuild_state(self.devices)
            self._sync_discovered_state()
            self._poll_all()
        return self.devices

    def _tasmota_devices(self) -> list[dict[str, Any]]:
        """The old Tasmota discovery logic, unchanged in behaviour."""
        devices: list[dict[str, Any]] = []
        for cfg in self._disc_configs.values():
            t = cfg.get("t", "")
            if not t:
                continue
            fn = cfg.get("fn", [t])
            rl = cfg.get("rl", [1])
            active = [i for i, r in enumerate(rl) if r > 0]
            if len(active) <= 1:
                devices.append(tasmota_device(
                    t, "POWER", fn[0] if fn and fn[0] else t, type_="light"))
            else:
                for idx in active:
                    devices.append(tasmota_device(
                        t, f"POWER{idx + 1}",
                        fn[idx] if idx < len(fn) and fn[idx]
                        else f"{t} Relay {idx + 1}", type_="switch"))
        tasmota_topics = {cfg.get("t") for cfg in self._disc_configs.values()}
        for dev, _status in self._disc_lwt.items():
            if dev in tasmota_topics:
                continue
            has_multi = any(
                k.startswith(f"{dev}/POWER") and k != f"{dev}/POWER"
                for k in self._disc_power)
            if has_multi:
                relay_count = max(
                    (int(k.replace(f"{dev}/POWER", ""))
                     for k in self._disc_power
                     if k.startswith(f"{dev}/POWER")
                     and k != f"{dev}/POWER"
                     and k.replace(f"{dev}/POWER", "").isdigit()),
                    default=1)
                for i in range(1, relay_count + 1):
                    devices.append(tasmota_device(
                        dev, f"POWER{i}", f"{dev} Relay {i}", type_="switch"))
            else:
                devices.append(tasmota_device(dev, "POWER", dev, type_="light"))
        return devices

    def _build_device_list(self) -> list[dict[str, Any]]:
        devices: list[dict[str, Any]] = []
        devices.extend(self._tasmota_devices())
        for config_topic in sorted(self._ha_configs):
            dev = ha_device_from_config(config_topic, self._ha_configs[config_topic])
            if dev is not None:
                devices.append(dev)
        for name in sorted(self._z2m_entries):
            dev = z2m_device_from_entry(self._z2m_entries[name])
            if dev is not None:
                devices.append(dev)
        for key in sorted(self._shelly_relays):
            device_id, relay = key.split("/", 1)
            try:
                relay_n = int(relay)
            except ValueError:
                continue
            devices.append(shelly_gen1_device(
                device_id, relay_n, self._shelly_models.get(device_id, "")))
        for state_topic in sorted(self._shelly_gen2_state):
            match = re.match(r"^(.+)/status/(switch|light):(\d+)$", state_topic)
            if match:
                devices.append(shelly_gen2_device(
                    match.group(1), match.group(2), int(match.group(3))))
        # Generic: only topics not already covered by a known device.
        covered = set()
        for dev in devices:
            if dev.get("state_topic"):
                covered.add(dev["state_topic"])
            if dev.get("command_topic"):
                covered.add(dev["command_topic"])
            if dev.get("availability_topic"):
                covered.add(dev["availability_topic"])
        for topic in sorted(self._generic_topics):
            if topic in covered:
                continue
            dev = generic_device_from_topic(topic, self._generic_topics[topic])
            if dev is not None:
                devices.append(dev)
                covered.add(topic)
        # Deduplicate by id, first occurrence wins.
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for dev in devices:
            if dev["id"] not in seen:
                seen.add(dev["id"])
                unique.append(dev)
        return unique

    def _sync_discovered_state(self) -> None:
        """Push discovery-buffer state into _device_state after a scan."""
        with self._lock:
            for dev in self.devices:
                if dev.get("protocol") == "tasmota":
                    lwt = self._disc_lwt.get(dev.get("topic", ""))
                    if lwt is not None:
                        self._set_online(dev["id"], lwt == "Online")
                    power = self._disc_power.get(
                        f"{dev.get('topic')}/{dev.get('powerCmd')}")
                    if power is not None:
                        self._set_state(dev["id"], power == "ON", power)
                elif dev.get("protocol") == "zigbee2mqtt":
                    name = dev["state_topic"].split("/", 1)[1] if "/" in dev["state_topic"] else ""
                    if name in self._z2m_availability:
                        self._set_online(
                            dev["id"], self._z2m_availability[name] == "online")
                    if name in self._z2m_state:
                        state, raw = parse_state(
                            self._z2m_state[name], dev.get("state_key"))
                        self._set_state(dev["id"], state, raw)
                elif dev.get("protocol") == "shelly":
                    # Gen1 relay topics are stored as "<id>/<relay>".
                    key = dev["state_topic"].replace("shellies/", "").replace("/relay/", "/")
                    if key in self._shelly_relays:
                        payload = self._shelly_relays[key]
                        self._set_state(dev["id"], payload == "on", payload)
                    if dev["state_topic"] in self._shelly_gen2_state:
                        state, raw = parse_state(
                            self._shelly_gen2_state[dev["state_topic"]], "output")
                        self._set_state(dev["id"], state, raw)
                # Any protocol: state / availability seen during the
                # generic scan (HA state topics land here when the
                # device was not known yet at message time).
                if dev.get("state_topic") in self._generic_topics:
                    state, raw = parse_state(
                        self._generic_topics[dev["state_topic"]],
                        dev.get("state_key"),
                        dev.get("payload_on", "ON"),
                        dev.get("payload_off", "OFF"))
                    # Only overwrite when the buffer actually holds a
                    # state for this device (generic scan may also hold
                    # a command echo); parse_state returning a binary
                    # value or a non-empty raw is the signal.
                    if state is not None or raw:
                        self._set_state(dev["id"], state, raw)
                if dev.get("availability_topic") in self._generic_topics:
                    avail = self._generic_topics[dev["availability_topic"]]
                    if avail == dev.get("availability_on"):
                        self._set_online(dev["id"], True)
                    elif avail == dev.get("availability_off"):
                        self._set_online(dev["id"], False)

    # ── Control ─────────────────────────────────────────────

    def _publish(self, topic: str, payload: str) -> None:
        if not self.connected or not self.client:
            raise RuntimeError("MQTT is not connected")
        self.client.publish(topic, payload)

    def _control(self, idx: int, action: str) -> str:
        if idx < 0 or idx >= len(self.devices):
            raise ValueError(f"Invalid device index: {idx + 1}")
        d = self.devices[idx]
        state_entry = self._device_state.get(d["id"], {})
        topic, payload = control_message(
            d, action, current_state=state_entry.get("state"))
        self._publish(topic, payload)
        if action == "ON":
            self._device_state.setdefault(d["id"], {})["state"] = True
        elif action == "OFF":
            self._device_state.setdefault(d["id"], {})["state"] = False
        elif action == "TOGGLE":
            current = state_entry.get("state")
            if current is not None:
                self._device_state.setdefault(d["id"], {})["state"] = not current
        return f"{d['name']} -> {action}"

    def format_status(self) -> str:
        if not self.devices:
            return "No smart home devices registered"
        lines = [
            f"Smart Home — {len(self.devices)} device(s)",
            "=" * 45,
        ]
        for i, d in enumerate(self.devices, 1):
            st = self._device_state.get(d["id"], {})
            state = st.get("state")
            raw = st.get("raw", "")
            if state is True:
                state_txt = "ON "
            elif state is False:
                state_txt = "OFF"
            elif raw:
                state_txt = raw[:20]
            else:
                state_txt = "?"
            online = st.get("online")
            online_txt = "Online" if online is True else (
                "Offline" if online is False else "unknown")
            ro = "" if d.get("controllable") else " RO"
            lines.append(
                f"{i:2}. {d['shortName']:<12} [{d['type']:<7}] {state_txt:<20} "
                f"{online_txt:<8} {d['room']} ({d.get('protocol')}){ro}")
        return "\n".join(lines)

    def device_details(self, device_index: int) -> str:
        if device_index < 1 or device_index > len(self.devices):
            raise ValueError(f"Invalid device index: {device_index}")
        d = self.devices[device_index - 1]
        st = self._device_state.get(d["id"], {})
        fields = [
            f"Device {device_index}: {d['name']}",
            f"  protocol: {d.get('protocol')}",
            f"  type: {d.get('type')}  room: {d.get('room')}",
            f"  state_topic: {d.get('state_topic') or '-'}",
            f"  command_topic: {d.get('command_topic') or '- (read-only)'}",
            f"  payload_on/off: {d.get('payload_on')} / {d.get('payload_off')}",
            f"  state_key: {d.get('state_key') or '-'}",
            f"  availability_topic: {d.get('availability_topic') or '-'}",
            f"  controllable: {d.get('controllable')}",
            f"  state: {st.get('state')}  raw: {st.get('raw', '')[:80]}",
            f"  online: {st.get('online')}",
            f"  source: {d.get('source')}",
        ]
        return "\n".join(fields)

    # ── Public tool methods ─────────────────────────────────

    def device_control(self, device_index: int, action: str) -> str:
        return self._control(device_index - 1, action)

    def lights_all(self, action: str) -> str:
        count = 0
        for i, d in enumerate(self.devices):
            if d.get("type") == "light" and d.get("controllable"):
                self._control(i, action)
                count += 1
        if count == 0:
            return "No controllable lights found"
        return f"All {count} lights -> {action}"

    def room_control(self, room: str, action: str) -> str:
        in_room = [i for i, d in enumerate(self.devices)
                   if d.get("room", "").lower() == room.lower()]
        if not in_room:
            raise ValueError(f"Room '{room}' not found")
        count = 0
        for i in in_room:
            if self.devices[i].get("controllable"):
                self._control(i, action)
                count += 1
        if count == 0:
            return (f"Room '{room}' has {len(in_room)} device(s) "
                    "but none are controllable")
        return f"{count} devices in '{room}' -> {action}"


# ── Singleton / MCP tool handlers ────────────────────────────────


def _get_manager() -> SmartHomeManager:
    """Return the initialized singleton manager (creates it on first use)."""
    global _MANAGER
    cfg = _load_smarthome_config()
    signature = (
        cfg["broker"], cfg["port"], cfg["username"], cfg["password"],
        cfg["client_id"], cfg["use_tls"],
        json.dumps(cfg["devices"], sort_keys=True, default=str),
        cfg.get("generic_scan", True),
    )
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = SmartHomeManager()
        if _MANAGER._config_signature == signature:
            return _MANAGER
        if _MANAGER.client is not None:
            _MANAGER.shutdown()
        _MANAGER._config_signature = signature
        if cfg.get("broker"):
            _MANAGER.configure(
                broker=cfg["broker"], port=cfg["port"],
                user=cfg["username"], password=cfg["password"],
                client_id=cfg["client_id"], devices=cfg["devices"],
                use_tls=cfg.get("use_tls", False),
                generic_scan=cfg.get("generic_scan", True),
            )
            time.sleep(_CONNECT_WAIT_SEC)
            logger.info(
                f"Smart home initialized: {len(cfg['devices'])} configured device(s), "
                f"broker {cfg['broker']}:{cfg['port']}")
        else:
            logger.info("Smart home MQTT broker is not configured")
        return _MANAGER


def _manager_status(manager: SmartHomeManager, cfg: dict) -> str:
    if not cfg.get("broker"):
        return "No MQTT broker configured (SMART_HOME.MQTT.BROKER is empty)."
    if not manager.connected:
        return f"Not connected to the MQTT broker ({manager._broker}:{manager._port})"
    return ""


def shutdown_smarthome_manager() -> None:
    """Disconnect the singleton MQTT client during MCP/application shutdown."""
    global _MANAGER
    with _MANAGER_LOCK:
        manager = _MANAGER
        _MANAGER = None
    if manager is not None:
        manager.shutdown()


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
    action = _s(args.get("action")).upper()
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
    action = _s(args.get("action")).upper()
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
    room = _s(args.get("room")).strip()
    action = _s(args.get("action")).upper()
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
            "No devices found. Make sure devices are connected "
            "to the same MQTT broker (Tasmota, Home Assistant discovery, "
            "Zigbee2MQTT, Shelly, or a generic MQTT topic)."
        )
    return f"Found {len(devices)} devices:\n" + manager.format_status()


async def device_details(args: dict) -> str:
    cfg = _load_smarthome_config()
    manager = _get_manager()
    err = _manager_status(manager, cfg)
    if err:
        return err
    try:
        idx = int(args.get("deviceIndex") or 0)
    except (TypeError, ValueError):
        return "Error: deviceIndex must be an integer."
    try:
        return await asyncio.to_thread(manager.device_details, idx)
    except Exception as e:
        return f"Error: {e}"
