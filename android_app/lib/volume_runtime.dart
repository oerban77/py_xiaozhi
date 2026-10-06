import 'dart:convert';

import 'package:flutter_volume_controller/flutter_volume_controller.dart';

/// System volume MCP tools, mirroring src/mcp/tools/volume/register.py.
///
/// The Python client controls the speaker through a VolumeController; on Android we
/// use flutter_volume_controller, whose API is 0.0-1.0 while the MCP contract is
/// 0-100, so the helpers below convert between the two.
class VolumeRuntime {
  static const module = 'volume';

  static const toolNames = <String>{
    'self.audio_speaker.set_volume',
    'self.audio_speaker.get_volume',
    'self.audio_speaker.get_volume_status',
    'self.audio_speaker.set_muted',
    'self.audio_speaker.toggle_mute',
    'self.audio_speaker.get_muted',
  };

  static const tools = <Map<String, Object?>>[
    {
      'name': 'self.audio_speaker.set_volume',
      'description': 'Set the system speaker volume to an absolute value (0-100).\n'
          'Use when user mentions: volume, sound, louder, quieter, mute, unmute, adjust volume.\n'
          'Examples: \'set volume to 50\', \'turn volume up\', \'make it louder\', \'mute\', '
          '\'Set the volume to 50\', \'Turn up volume\', \'Turn down volume slightly\', \'Mute\'.\n'
          'Parameter:\n'
          '- volume: Integer (0-100) representing the target volume level. Set to 0 for mute.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'volume': {'type': 'integer', 'minimum': 0, 'maximum': 100},
        },
        'required': ['volume'],
      },
    },
    {
      'name': 'self.audio_speaker.get_volume',
      'description': 'Get the current system speaker volume level.\n'
          'Use when user asks about: current volume, volume level, how loud, what\'s the volume.\n'
          'Examples: \'what is the current volume?\', \'how loud is it?\', \'check volume level\', '
          '\'What is the current volume?\', \'check the volume\', \'what is the volume level\'.\n'
          'Returns: Integer (0-100) representing the current volume level.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'self.audio_speaker.get_volume_status',
      'description': 'Get detailed speaker volume status including whether audio output is muted '
          'and whether the volume controller is available. Returns a JSON payload with fields: '
          'volume (0-100), muted (bool), available (bool), reason/error (optional).',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'self.audio_speaker.set_muted',
      'description': 'Mute or unmute the system speaker output.\n'
          'Use when user mentions: mute, unmute, silence the speaker, turn off the sound.\n'
          'Examples: \'mute\', \'unmute\', \'mute the speaker\', \'silence the audio\', \'Mute\'.\n'
          'Parameter:\n'
          '- muted: Boolean. true to mute the output, false to unmute it.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'muted': {'type': 'boolean'},
        },
        'required': ['muted'],
      },
    },
    {
      'name': 'self.audio_speaker.toggle_mute',
      'description': 'Toggle the system speaker between muted and unmuted.\n'
          'Use when user mentions: toggle mute, mute/unmute, bisukan, bunyikan.\n'
          'Returns a JSON payload with fields: success (bool), muted (bool).',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'self.audio_speaker.get_muted',
      'description': 'Check whether the system speaker output is currently muted.\n'
          'Use when user asks: is it muted, is the volume muted, apakah dibisukan.\n'
          'Returns a JSON payload with fields: muted (bool), available (bool).',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
  ];

  /// Set once a hardware call succeeds, so a later failure reports a real state
  /// instead of the default.
  static bool _available = false;

  static Future<String> call(String name, Map<String, dynamic> arguments) async {
    switch (name) {
      case 'self.audio_speaker.set_volume':
        return _setVolume(arguments);
      case 'self.audio_speaker.get_volume':
        return _getVolume();
      case 'self.audio_speaker.get_volume_status':
        return _getVolumeStatus();
      case 'self.audio_speaker.set_muted':
        return _setMuted(arguments);
      case 'self.audio_speaker.toggle_mute':
        return _toggleMute();
      case 'self.audio_speaker.get_muted':
        return _getMuted();
    }
    throw ArgumentError('Unknown volume tool: $name');
  }

  static int _optionalInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    return int.tryParse('$value') ?? -1;
  }

  static bool _optionalBool(Object? value) {
    if (value is bool) return value;
    if (value is num) return value != 0;
    if (value is String) {
      final text = value.trim().toLowerCase();
      return text == 'true' || text == '1' || text == 'yes' || text == 'on';
    }
    return false;
  }

  /// Convert the 0.0-1.0 plugin range to the 0-100 MCP contract.
  static int _toPercent(double? level) => (level == null ? 0 : (level * 100).round()).clamp(0, 100);

  /// Convert the 0-100 MCP contract to the 0.0-1.0 plugin range.
  static double _toLevel(int percent) => (percent.clamp(0, 100) / 100).toDouble();

  static Future<String> _setVolume(Map<String, dynamic> arguments) async {
    final volume = _optionalInt(arguments['volume']);
    if (volume < 0 || volume > 100) {
      return 'false';
    }
    try {
      await FlutterVolumeController.setVolume(_toLevel(volume));
      _available = true;
      return 'true';
    } catch (_) {
      return 'false';
    }
  }

  static Future<String> _getVolume() async {
    try {
      final level = await FlutterVolumeController.getVolume();
      _available = level != null;
      return '${_toPercent(level)}';
    } catch (_) {
      return '50';
    }
  }

  static Future<String> _getVolumeStatus() async {
    try {
      final level = await FlutterVolumeController.getVolume();
      final muted = await FlutterVolumeController.getMute();
      _available = level != null;
      return jsonEncode({
        'volume': _toPercent(level),
        'muted': muted == true || level == 0,
        'available': level != null,
      });
    } catch (error) {
      return jsonEncode({
        'volume': 50,
        'muted': false,
        'available': false,
        'error': '$error',
      });
    }
  }

  static Future<String> _setMuted(Map<String, dynamic> arguments) async {
    final muted = _optionalBool(arguments['muted']);
    try {
      await FlutterVolumeController.setMute(muted);
      _available = true;
      return jsonEncode({'success': true, 'muted': muted});
    } catch (error) {
      return jsonEncode({'success': false, 'reason': '$error'});
    }
  }

  static Future<String> _toggleMute() async {
    try {
      final current = await FlutterVolumeController.getMute();
      final next = !(current ?? false);
      await FlutterVolumeController.setMute(next);
      _available = true;
      return jsonEncode({'success': true, 'muted': next});
    } catch (error) {
      return jsonEncode({'success': false, 'reason': '$error'});
    }
  }

  static Future<String> _getMuted() async {
    try {
      final muted = await FlutterVolumeController.getMute();
      _available = muted != null;
      return jsonEncode({'muted': muted ?? false, 'available': muted != null});
    } catch (error) {
      return jsonEncode({'muted': false, 'available': false, 'error': '$error'});
    }
  }
}
