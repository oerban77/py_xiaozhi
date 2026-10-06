import 'dart:convert';

import 'package:camera/camera.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/camera_runtime.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // The facing is persisted in SharedPreferences, whose singleton is cached for
  // the whole test run. Reset it before each test so the default 'back' applies.
  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  void mockCameras(List<CameraDescription> cameras) {
    // availableCameras() funnels through the camera platform channel, so a mock
    // handler on the plugin's channel is enough to fake the device list. The
    // payload mirrors what the native plugins send: name/lensFacing/sensorOrientation.
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('plugins.flutter.io/camera'), (call) async {
      if (call.method == 'availableCameras') {
        return cameras
            .map((camera) => <String, Object>{
                  'name': camera.name,
                  'lensFacing': camera.lensDirection.name,
                  'sensorOrientation': camera.sensorOrientation,
                })
            .toList();
      }
      return null;
    });
  }

  const front = CameraDescription(
    name: 'front-camera',
    lensDirection: CameraLensDirection.front,
    sensorOrientation: 270,
  );
  const back = CameraDescription(
    name: 'back-camera',
    lensDirection: CameraLensDirection.back,
    sensorOrientation: 90,
  );

  group('normalizeFacing', () {
    test('maps English and Indonesian synonyms', () {
      expect(CameraRuntime.normalizeFacing('front'), 'front');
      expect(CameraRuntime.normalizeFacing('BACK'), 'back');
      expect(CameraRuntime.normalizeFacing('depan'), 'front');
      expect(CameraRuntime.normalizeFacing('kamera depan'), 'front');
      expect(CameraRuntime.normalizeFacing('selfie'), 'front');
      expect(CameraRuntime.normalizeFacing('front camera'), 'front');
      expect(CameraRuntime.normalizeFacing('belakang'), 'back');
      expect(CameraRuntime.normalizeFacing('kamera belakang'), 'back');
      expect(CameraRuntime.normalizeFacing('rear'), 'back');
      expect(CameraRuntime.normalizeFacing('main'), 'back');
    });

    test('returns an empty string for unknown input', () {
      expect(CameraRuntime.normalizeFacing(''), '');
      expect(CameraRuntime.normalizeFacing('side'), '');
      expect(CameraRuntime.normalizeFacing('maybe'), '');
    });
  });

  group('self.camera.switch', () {
    test('persists the requested facing and reports the selected camera', () async {
      mockCameras(const [front, back]);

      final result = await CameraRuntime.call(
        'self.camera.switch',
        <String, dynamic>{'facing': 'depan'},
      );
      final payload = jsonDecode(result) as Map<String, dynamic>;

      expect(payload['success'], true);
      expect(payload['facing'], 'front');
      expect(payload['selected'], 'front-camera');
      expect(payload['front'], ['front-camera']);
      expect(payload['back'], ['back-camera']);
      expect(await CameraRuntime.currentFacing(), 'front');
    });

    test('rejects an unknown facing', () async {
      mockCameras(const [front, back]);

      final result = await CameraRuntime.call(
        'self.camera.switch',
        <String, dynamic>{'facing': 'sideways'},
      );
      final payload = jsonDecode(result) as Map<String, dynamic>;

      expect(payload['success'], false);
      expect(payload['reason'], contains('Unknown camera facing'));
      expect(await CameraRuntime.currentFacing(), 'back');
    });

    test('reports when the requested camera is missing', () async {
      mockCameras(const [back]);

      final result = await CameraRuntime.call(
        'self.camera.switch',
        <String, dynamic>{'facing': 'front'},
      );
      final payload = jsonDecode(result) as Map<String, dynamic>;

      expect(payload['success'], false);
      expect(payload['reason'], contains('No front camera was found'));
    });
  });

  group('self.camera.get_facing', () {
    test('reports the persisted facing and the detected cameras', () async {
      mockCameras(const [front, back]);

      final result = await CameraRuntime.call(
        'self.camera.get_facing',
        <String, dynamic>{},
      );
      final payload = jsonDecode(result) as Map<String, dynamic>;

      expect(payload['facing'], 'back');
      expect(payload['front'], ['front-camera']);
      expect(payload['back'], ['back-camera']);
      expect(payload['current_camera'], 'back');
      expect(payload['available'], true);
    });
  });

  test('tool names match the Python MCP contract', () {
    expect(CameraRuntime.toolNames, contains('self.camera.switch'));
    expect(CameraRuntime.toolNames, contains('self.camera.get_facing'));
    expect(CameraRuntime.tools.length, CameraRuntime.toolNames.length);
  });

  test('rejects an unknown tool name', () async {
    expect(
      () => CameraRuntime.call('self.camera.nothing', <String, dynamic>{}),
      throwsArgumentError,
    );
  });
}
