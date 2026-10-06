import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_volume_controller/flutter_volume_controller.dart';
import 'package:py_xiaozhi_android/volume_runtime.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // The plugin exposes its method channel for tests; answering 'getVolume' with the
  // string the Android Kotlin side emits keeps the Dart parsing path exercised.
  double currentLevel = 0.5;
  bool currentMuted = false;
  late List<String> calls;

  setUp(() {
    currentLevel = 0.5;
    currentMuted = false;
    calls = [];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(FlutterVolumeController.methodChannel, (call) async {
      calls.add(call.method);
      switch (call.method) {
        case 'getVolume':
          return currentLevel.toString();
        case 'getMute':
          return currentMuted;
        case 'setVolume':
          final args = call.arguments as Map;
          currentLevel = (args['volume'] as num).toDouble();
          return null;
        case 'setMute':
          final args = call.arguments as Map;
          currentMuted = args['isMuted'] as bool;
          return null;
        case 'toggleMute':
          currentMuted = !currentMuted;
          return null;
      }
      return null;
    });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(FlutterVolumeController.methodChannel, null);
  });

  group('self.audio_speaker.set_volume', () {
    test('converts 0-100 to the plugin 0.0-1.0 range', () async {
      final result = await VolumeRuntime.call(
        'self.audio_speaker.set_volume',
        <String, dynamic>{'volume': 40},
      );
      expect(result, 'true');
      expect(calls, contains('setVolume'));
      expect(currentLevel, closeTo(0.4, 0.001));
    });

    test('clamps out-of-range values', () async {
      expect(
        await VolumeRuntime.call(
          'self.audio_speaker.set_volume',
          <String, dynamic>{'volume': 200},
        ),
        'false',
      );
      expect(
        await VolumeRuntime.call(
          'self.audio_speaker.set_volume',
          <String, dynamic>{'volume': -10},
        ),
        'false',
      );
      expect(calls, isNot(contains('setVolume')));
    });
  });

  group('self.audio_speaker.get_volume', () {
    test('converts the plugin level back to 0-100', () async {
      currentLevel = 0.25;
      expect(
        await VolumeRuntime.call('self.audio_speaker.get_volume', <String, dynamic>{}),
        '25',
      );
    });
  });

  group('self.audio_speaker.get_volume_status', () {
    test('reports volume, muted, and availability', () async {
      currentLevel = 0.6;
      currentMuted = false;
      final payload = jsonDecode(
        await VolumeRuntime.call('self.audio_speaker.get_volume_status', <String, dynamic>{}),
      ) as Map<String, dynamic>;
      expect(payload['volume'], 60);
      expect(payload['muted'], false);
      expect(payload['available'], true);
    });

    test('treats a zero level as muted', () async {
      currentLevel = 0.0;
      final payload = jsonDecode(
        await VolumeRuntime.call('self.audio_speaker.get_volume_status', <String, dynamic>{}),
      ) as Map<String, dynamic>;
      expect(payload['muted'], true);
    });
  });

  group('self.audio_speaker.set_muted', () {
    test('forwards the requested state', () async {
      final payload = jsonDecode(
        await VolumeRuntime.call('self.audio_speaker.set_muted', <String, dynamic>{'muted': true}),
      ) as Map<String, dynamic>;
      expect(payload['success'], true);
      expect(payload['muted'], true);
      expect(currentMuted, true);
    });

    test('accepts string booleans', () async {
      final payload = jsonDecode(
        await VolumeRuntime.call(
          'self.audio_speaker.set_muted',
          <String, dynamic>{'muted': 'false'},
        ),
      ) as Map<String, dynamic>;
      expect(payload['success'], true);
      expect(payload['muted'], false);
      expect(currentMuted, false);
    });
  });

  group('self.audio_speaker.toggle_mute', () {
    test('flips the current mute state', () async {
      currentMuted = false;
      var payload = jsonDecode(
        await VolumeRuntime.call('self.audio_speaker.toggle_mute', <String, dynamic>{}),
      ) as Map<String, dynamic>;
      expect(payload['success'], true);
      expect(payload['muted'], true);
      expect(currentMuted, true);

      payload = jsonDecode(
        await VolumeRuntime.call('self.audio_speaker.toggle_mute', <String, dynamic>{}),
      ) as Map<String, dynamic>;
      expect(payload['muted'], false);
      expect(currentMuted, false);
    });
  });

  group('self.audio_speaker.get_muted', () {
    test('reports the mute state and availability', () async {
      currentMuted = true;
      final payload = jsonDecode(
        await VolumeRuntime.call('self.audio_speaker.get_muted', <String, dynamic>{}),
      ) as Map<String, dynamic>;
      expect(payload['muted'], true);
      expect(payload['available'], true);
    });
  });

  test('tool names match the Python MCP contract', () {
    expect(VolumeRuntime.toolNames, contains('self.audio_speaker.set_volume'));
    expect(VolumeRuntime.toolNames, contains('self.audio_speaker.get_volume'));
    expect(VolumeRuntime.toolNames, contains('self.audio_speaker.get_volume_status'));
    expect(VolumeRuntime.toolNames, contains('self.audio_speaker.set_muted'));
    expect(VolumeRuntime.toolNames, contains('self.audio_speaker.toggle_mute'));
    expect(VolumeRuntime.toolNames, contains('self.audio_speaker.get_muted'));
    expect(VolumeRuntime.tools.length, VolumeRuntime.toolNames.length);
  });

  test('rejects an unknown tool name', () async {
    expect(
      () => VolumeRuntime.call('self.audio_speaker.nothing', <String, dynamic>{}),
      throwsArgumentError,
    );
  });
}
