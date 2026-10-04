import 'package:camera/camera.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/camera_selection.dart';

void main() {
  test('saved camera facing selects the requested lens', () {
    expect(cameraLensFor('front'), CameraLensDirection.front);
    expect(cameraLensFor('back'), CameraLensDirection.back);
    expect(cameraLensFor('invalid'), CameraLensDirection.back);
  });
}