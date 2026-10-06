import 'dart:convert';

class ProtocolMessages {
  static String hello() => jsonEncode({
        'type': 'hello',
        'version': 1,
        'features': {'mcp': true},
        'transport': 'websocket',
        'audio_params': {
          'format': 'opus',
          'sample_rate': 16000,
          'channels': 1,
          'frame_duration': 20,
        },
      });

  static Map<String, Object?> listenStart(String sessionId, [String mode = 'manual']) => {
        'session_id': sessionId,
        'type': 'listen',
        'state': 'start',
        'mode': mode,
      };

  static Map<String, Object?> listenStop(String sessionId) => {
        'session_id': sessionId,
        'type': 'listen',
        'state': 'stop',
      };

  static Map<String, Object?> detectText(String sessionId, String text) => {
        'session_id': sessionId,
        'type': 'listen',
        'state': 'detect',
        'text': text,
      };

  /// [reason] mirrors src/protocols/protocol.py send_abort_speaking, which adds
  /// reason "wake_word_detected" so the server knows the interrupt came from the
  /// wake word plugin rather than the user tapping stop.
  static Map<String, Object?> abort(String sessionId, [String? reason]) => {
        'session_id': sessionId,
        'type': 'abort',
        if (reason != null) 'reason': reason,
      };
}