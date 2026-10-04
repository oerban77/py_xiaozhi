import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'camera_selection.dart';

class CameraScreen extends StatefulWidget {
  const CameraScreen({super.key, required this.lensDirection});

  final String lensDirection;

  @override
  State<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends State<CameraScreen> with WidgetsBindingObserver {
  CameraController? _cameraController;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    unawaited(_initializeCamera());
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final camera = _cameraController;
    if (camera == null) return;
    if (state == AppLifecycleState.inactive) {
      _cameraController = null;
      unawaited(camera.dispose());
    } else if (state == AppLifecycleState.resumed) {
      unawaited(_initializeCamera());
    }
  }

  Future<void> _initializeCamera() async {
    if (_loading && _cameraController != null) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final cameras = await availableCameras();
      final wanted = cameraLensFor(widget.lensDirection);
      CameraDescription? selected;
      for (final camera in cameras) {
        if (camera.lensDirection == wanted) {
          selected = camera;
          break;
        }
      }
      if (selected == null) {
        throw StateError(wanted == CameraLensDirection.front
            ? 'Kamera depan tidak tersedia di perangkat ini.'
            : 'Kamera belakang tidak tersedia di perangkat ini.');
      }

      final controller = CameraController(
        selected,
        ResolutionPreset.medium,
        enableAudio: false,
      );
      await controller.initialize();
      if (!mounted) {
        await controller.dispose();
        return;
      }
      setState(() {
        _cameraController = controller;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error is CameraException
            ? 'Kamera tidak dapat dibuka: ${error.description ?? error.code}'
            : error.toString().replaceFirst('Bad state: ', '');
        _loading = false;
      });
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    final camera = _cameraController;
    if (camera != null) unawaited(camera.dispose());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final camera = _cameraController;
    return Scaffold(
      backgroundColor: const Color(0xFF101715),
      appBar: AppBar(
        backgroundColor: const Color(0xFF101715),
        foregroundColor: Colors.white,
        title: Text(widget.lensDirection == 'front' ? 'Kamera depan' : 'Kamera belakang'),
      ),
      body: Center(
        child: _error != null
            ? Padding(
                padding: const EdgeInsets.all(28),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.no_photography_outlined, color: Colors.white70, size: 42),
                    const SizedBox(height: 16),
                    Text(
                      _error!,
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.white, height: 1.45),
                    ),
                    const SizedBox(height: 18),
                    FilledButton.icon(
                      onPressed: _initializeCamera,
                      icon: const Icon(Icons.refresh_rounded),
                      label: const Text('Coba lagi'),
                      style: FilledButton.styleFrom(backgroundColor: AppColors.green),
                    ),
                  ],
                ),
              )
            : _loading || camera == null || !camera.value.isInitialized
                ? const CircularProgressIndicator(color: Colors.white)
                : ClipRRect(
                    borderRadius: BorderRadius.circular(18),
                    child: CameraPreview(camera),
                  ),
      ),
    );
  }
}