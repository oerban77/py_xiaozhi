import 'dart:async';
import 'dart:io';

import 'package:network_info_plus/network_info_plus.dart';

class SmartHomeScanner {
  static const _batchSize = 96;
  static const _connectTimeout = Duration(milliseconds: 250);

  static Future<List<String>> findMqttBrokers(int port) async {
    final networkInfo = NetworkInfo();
    final address = await networkInfo.getWifiIP();
    final subnetMask = await networkInfo.getWifiSubmask();
    if (address == null || subnetMask == null) {
      throw StateError('Jaringan Wi-Fi atau subnet tidak dapat dibaca perangkat.');
    }

    final ip = _parseIpv4(address);
    final mask = _parseIpv4(subnetMask);
    final network = ip & mask;
    final broadcast = network | (~mask & 0xFFFFFFFF);
    if (broadcast - network <= 1) {
      throw StateError('Subnet Wi-Fi tidak memiliki host yang dapat dipindai.');
    }

    final found = <String>[];
    for (var first = network + 1; first < broadcast; first += _batchSize) {
      final last = (first + _batchSize).clamp(first, broadcast);
      final batch = [
        for (var candidate = first; candidate < last; candidate++) _formatIpv4(candidate),
      ];
      final results = await Future.wait(batch.map((host) => _hasOpenPort(host, port)));
      found.addAll(results.whereType<String>());
    }
    return found;
  }

  static Future<String?> _hasOpenPort(String host, int port) async {
    try {
      final socket = await Socket.connect(host, port, timeout: _connectTimeout);
      socket.destroy();
      return host;
    } on SocketException {
      return null;
    } on TimeoutException {
      return null;
    }
  }

  static int _parseIpv4(String value) {
    final parts = value.split('.');
    if (parts.length != 4) throw FormatException('Alamat IPv4 tidak valid: $value');
    var result = 0;
    for (final part in parts) {
      final octet = int.tryParse(part);
      if (octet == null || octet < 0 || octet > 255) {
        throw FormatException('Alamat IPv4 tidak valid: $value');
      }
      result = (result << 8) | octet;
    }
    return result;
  }

  static String _formatIpv4(int value) =>
      '${(value >> 24) & 0xFF}.${(value >> 16) & 0xFF}.${(value >> 8) & 0xFF}.${value & 0xFF}';
}