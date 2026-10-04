import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/protocol_messages.dart';

void main() {
  test('hello uses the Xiaozhi websocket Opus profile', () {
    final message = jsonDecode(ProtocolMessages.hello()) as Map<String, dynamic>;

    expect(message['type'], 'hello');
    expect(message['transport'], 'websocket');
    expect(message['audio_params']['sample_rate'], 16000);
    expect(message['audio_params']['frame_duration'], 20);
  });

  test('manual listening messages retain the session id', () {
    expect(ProtocolMessages.listenStart('session-1')['session_id'], 'session-1');
    expect(ProtocolMessages.listenStop('session-1')['state'], 'stop');
    expect(ProtocolMessages.detectText('session-1', 'Halo')['text'], 'Halo');
  });
}