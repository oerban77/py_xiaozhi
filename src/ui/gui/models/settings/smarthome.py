"""Smart home MQTT broker settings and local broker discovery."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import itertools
import json
import socket
import threading

from PySide6.QtCore import Slot

from src.logging import get_logger

logger = get_logger()


class SettingsSmartHomeMixin:
    """SMART_HOME.MQTT settings and asynchronous LAN discovery."""

    def _get_smartHomeBroker(self) -> str:
        return str(self._get_value("SMART_HOME.MQTT.BROKER", "") or "")

    def _set_smartHomeBroker(self, value: str) -> None:
        self._set_value("SMART_HOME.MQTT.BROKER", (value or "").strip())

    def _get_smartHomePort(self) -> int:
        try:
            return int(self._get_value("SMART_HOME.MQTT.PORT", 1883) or 1883)
        except (TypeError, ValueError):
            return 1883

    def _set_smartHomePort(self, value: int) -> None:
        try:
            port = int(value)
        except (TypeError, ValueError):
            port = 1883
        self._set_value("SMART_HOME.MQTT.PORT", min(65535, max(1, port)))

    def _get_smartHomeUsername(self) -> str:
        return str(self._get_value("SMART_HOME.MQTT.USERNAME", "") or "")

    def _set_smartHomeUsername(self, value: str) -> None:
        self._set_value("SMART_HOME.MQTT.USERNAME", value or "")

    def _get_smartHomePassword(self) -> str:
        return str(self._get_value("SMART_HOME.MQTT.PASSWORD", "") or "")

    def _set_smartHomePassword(self, value: str) -> None:
        self._set_value("SMART_HOME.MQTT.PASSWORD", value or "")

    @Slot()
    def scanMqttBroker(self) -> None:
        """Find hosts accepting TCP connections on the configured port."""
        if self._mqtt_scan_running:
            return
        self._mqtt_scan_running = True
        self._mqtt_scan_cancel = threading.Event()
        port = self._get_smartHomePort()
        self.statusMessage.emit(f"Scanning local network on port {port}...")
        self._mqtt_scan_thread = threading.Thread(
            target=self._scan_mqtt_brokers,
            args=(port, self._mqtt_scan_cancel),
            name="settings:scan_mqtt_brokers",
            daemon=True,
        )
        self._mqtt_scan_thread.start()

    def _scan_mqtt_brokers(self, port: int, cancel: threading.Event) -> None:
        try:
            hosts = _find_open_hosts(port, cancel)
            result = json.dumps(hosts)
        except Exception as e:
            logger.warning(f"MQTT port scan failed: {e}", exc_info=True)
            result = "[]"
        if cancel.is_set():
            return
        self.mqttBrokerScanFinished.emit(result)

    def stopMqttBrokerScan(self) -> None:
        """Cancel the network scan and wait briefly for its worker to exit."""
        cancel = getattr(self, "_mqtt_scan_cancel", None)
        thread = getattr(self, "_mqtt_scan_thread", None)
        if cancel is not None:
            cancel.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._mqtt_scan_running = False
        self._mqtt_scan_thread = None

    @Slot(str)
    def _apply_mqtt_broker_scan(self, result: str) -> None:
        try:
            hosts = json.loads(result)
        except (TypeError, ValueError):
            hosts = []
        self._mqtt_scan_running = False
        self._mqtt_scan_thread = None
        if not hosts:
            self.statusMessage.emit(
                f"No open host found on port {self._get_smartHomePort()}"
            )
            return

        self._set_smartHomeBroker(hosts[0])
        endpoints = ", ".join(f"{host}:{self._get_smartHomePort()}" for host in hosts)
        self.statusMessage.emit(f"Open host(s): {endpoints}")


def _port_is_open(host: str, port: int, cancel: threading.Event) -> bool:
    """Return whether a TCP connection can be made to the given host and port."""
    if cancel.is_set():
        return False
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return not cancel.is_set()
    except OSError:
        return False


def _find_open_hosts(port: int, cancel: threading.Event) -> list[str]:
    """Scan all hosts in the detected local IPv4 subnets for one open port."""
    networks: set[ipaddress.IPv4Network] = set()
    candidates: set[str] = {"127.0.0.1"}
    try:
        import psutil

        for addresses in psutil.net_if_addrs().values():
            for address in addresses:
                if address.family != socket.AF_INET or not address.address or not address.netmask:
                    continue
                ip = ipaddress.IPv4Address(address.address)
                if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                    continue
                prefix = ipaddress.IPv4Network(f"0.0.0.0/{address.netmask}").prefixlen
                networks.add(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
                candidates.add(str(ip))
    except Exception as e:
        logger.debug(f"Could not enumerate local network interfaces: {e}")

    found: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        host_sources = [iter(sorted(candidates))]
        host_sources.extend(
            network.hosts()
            for network in sorted(
                networks,
                key=lambda item: (int(item.network_address), item.prefixlen),
            )
        )
        seen: set[str] = set()
        hosts = itertools.chain.from_iterable(host_sources)
        while not cancel.is_set():
            batch = []
            for host in hosts:
                host = str(host)
                if host not in seen:
                    seen.add(host)
                    batch.append(host)
                if len(batch) == 128:
                    break
            if not batch:
                break
            checks = {
                pool.submit(_port_is_open, host, port, cancel): host
                for host in batch
            }
            for future in concurrent.futures.as_completed(checks):
                if cancel.is_set():
                    for pending in checks:
                        pending.cancel()
                    return []
                if future.result():
                    found.append(checks[future])
    return sorted(set(found), key=lambda item: tuple(map(int, item.split("."))))