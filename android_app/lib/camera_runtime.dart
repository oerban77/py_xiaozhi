import 'dart:convert';

import 'package:camera/camera.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Camera facing MCP tools, mirroring src/mcp/tools/camera/selection.py.
///
/// The Python client classifies /dev/video* nodes through V4L2; on Android the
/// camera plugin already reports [CameraDescription.lensDirection], so we only
/// need to map it onto the same front/back contract and persist the choice to
/// SharedPreferences, where take_photo and the settings page read it from.
class CameraRuntime {
  static const module = 'camera';
  static const preferencesKey = 'camera_facing';

  static const toolNames = <String>{
    'self.camera.switch',
    'self.camera.get_facing',
  };

  static const tools = <Map<String, Object?>>[
    {
      'name': 'self.camera.switch',
      'description': '[Camera Switch] Switch the active camera between the front-facing and the '
          'back-facing camera, so the next take_photo uses it.\n'
          'Use when the user mentions: switch camera, use the front camera, use the back '
          'camera, ganti kamera, kamera depan, kamera belakang, balik kamera.\n'
          'Parameter:\n'
          '- facing: which camera to use. Accepted values: \'front\' (depan / selfie) or '
          '\'back\' (belakang / rear).\n'
          'Returns a JSON payload with fields: success (bool), facing (front|back|unknown), '
          'selected (camera name), front (list of detected front cameras), back (list of '
          'detected back cameras), reason (when it fails).',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'facing': {'type': 'string'},
        },
        'required': ['facing'],
      },
    },
    {
      'name': 'self.camera.get_facing',
      'description': '[Camera Status] Report which camera (front or back) is currently active and '
          'which cameras were detected.\n'
          'Use when the user asks: which camera is active, what camera are you using, '
          'kamera mana yang aktif, kamera depan atau belakang.\n'
          'Returns a JSON payload with fields: facing (front|back|unknown), '
          'front (list), back (list), current_camera (key), available (bool).',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
  ];

  /// Map free-form user input onto 'front' / 'back', mirroring _normalize_facing().
  static String normalizeFacing(String facing) {
    final value = facing.trim().toLowerCase();
    switch (value) {
      case 'front':
      case 'depan':
      case 'depan kamera':
      case 'kamera depan':
      case 'selfie':
      case 'front-facing':
      case 'front camera':
        return 'front';
      case 'back':
      case 'belakang':
      case 'belakang kamera':
      case 'kamera belakang':
      case 'rear':
      case 'rear-facing':
      case 'back camera':
      case 'main':
        return 'back';
      default:
        return '';
    }
  }

  static Future<String> call(String name, Map<String, dynamic> arguments) async {
    switch (name) {
      case 'self.camera.switch':
        return switchCamera(arguments['facing'] is String
            ? arguments['facing'] as String
            : '${arguments['facing'] ?? ''}');
      case 'self.camera.get_facing':
        return getFacing();
    }
    throw ArgumentError('Unknown camera tool: $name');
  }

  /// Split the detected cameras into (front, back) name lists.
  static Future<({List<String> front, List<String> back})> classify() async {
    final cameras = await availableCameras();
    final front = <String>[];
    final back = <String>[];
    for (final camera in cameras) {
      if (camera.lensDirection == CameraLensDirection.front) {
        front.add(camera.name);
      } else if (camera.lensDirection == CameraLensDirection.back) {
        back.add(camera.name);
      }
    }
    return (front: front, back: back);
  }

  /// Current facing, read from the same SharedPreferences key take_photo uses.
  static Future<String> currentFacing() async {
    final preferences = await SharedPreferences.getInstance();
    return preferences.getString(preferencesKey) ?? 'back';
  }

  static Future<String> getFacing() async {
    final classified = await classify();
    final facing = await currentFacing();
    return jsonEncode({
      'facing': facing == 'front' || facing == 'back' ? facing : 'unknown',
      'front': classified.front,
      'back': classified.back,
      'current_camera': facing,
      'available': classified.front.isNotEmpty || classified.back.isNotEmpty,
    });
  }

  static Future<String> switchCamera(String facing) async {
    final normalized = normalizeFacing(facing);
    if (normalized.isEmpty) {
      return jsonEncode({
        'success': false,
        'reason': "Unknown camera facing '$facing'. Use 'front' or 'back' (depan / belakang).",
      });
    }

    final classified = await classify();
    final pool = normalized == 'front' ? classified.front : classified.back;
    if (pool.isEmpty) {
      return jsonEncode({
        'success': false,
        'facing': normalized,
        'front': classified.front,
        'back': classified.back,
        'reason': 'No $normalized camera was found. Detected cameras: '
            'front=${classified.front}, back=${classified.back}.',
      });
    }

    final selected = pool.first;
    final preferences = await SharedPreferences.getInstance();
    await preferences.setString(preferencesKey, normalized);
    return jsonEncode({
      'success': true,
      'facing': normalized,
      'selected': selected,
      'front': classified.front,
      'back': classified.back,
    });
  }
}
