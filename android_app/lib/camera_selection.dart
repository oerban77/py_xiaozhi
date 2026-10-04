import 'package:camera/camera.dart';

CameraLensDirection cameraLensFor(String facing) =>
    facing == 'front' ? CameraLensDirection.front : CameraLensDirection.back;