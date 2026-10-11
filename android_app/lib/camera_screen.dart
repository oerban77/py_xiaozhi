import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'app_theme.dart';
import 'camera_runtime.dart';
import 'camera_selection.dart';

/// Fullscreen camera view, mirroring the WhatsApp camera: tap anywhere on the
/// preview to take a photo, or press the switch button to flip between the
/// front and back lens. Returns the JPEG bytes of the captured photo, or null
/// when the user backs out without taking one.
class CameraScreen extends StatefulWidget {
  const CameraScreen({
    super.key,
    required this.lensDirection,
    this.onCameraFacingChanged,
  });

  final String lensDirection;

  /// Notified when the user flips the lens, so the caller keeps its own
  /// camera-facing state in sync with the persisted choice.
  final void Function(String facing)? onCameraFacingChanged;

  @override
  State<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends State<CameraScreen> with WidgetsBindingObserver {
  CameraController? _cameraController;
  String? _error;
  bool _loading = true;
  bool _capturing = false;
  late String _facing;

  @override
  void initState() {
    super.initState();
    _facing = widget.lensDirection == 'front' ? 'front' : 'back';
    WidgetsBinding.instance.addObserver(this);
    unawaited(SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky));
    unawaited(_initializeCamera());
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final camera = _cameraController;
    if (camera == null) return;
    if (state == AppLifecycleState.inactive) {
      _cameraController = null;
      unawaited(camera.dispose());
      if (mounted) setState(() => _loading = true);
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
      final wanted = cameraLensFor(_facing);
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

      // Releasing the previous controller first keeps the lens free for the new
      // one, otherwise the second initialize() races the native camera service.
      final previous = _cameraController;
      if (previous != null) {
        _cameraController = null;
        await previous.dispose();
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

  Future<void> _switchCamera() async {
    if (_loading || _capturing) return;
    final next = _facing == 'front' ? 'back' : 'front';
    // CameraRuntime.switchCamera reports whether the lens exists and persists
    // the choice to the same key take_photo reads.
    final decoded = jsonDecode(await CameraRuntime.switchCamera(next));
    if (decoded is! Map || decoded['success'] != true) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(decoded is Map
              ? '${decoded['reason'] ?? 'Kamera tidak dapat diganti.'}'
              : 'Kamera tidak dapat diganti.'),
        ),
      );
      return;
    }
    widget.onCameraFacingChanged?.call(next);
    setState(() => _facing = next);
    await _initializeCamera();
  }

  Future<void> _capture() async {
    final camera = _cameraController;
    if (camera == null || !camera.value.isInitialized || _capturing) return;
    setState(() {
      _capturing = true;
      _error = null;
    });
    try {
      final image = await camera.takePicture();
      final bytes = await image.readAsBytes();
      if (!mounted) return;
      // Brief shutter flash, then hand the photo back to the chat.
      await Future<void>.delayed(const Duration(milliseconds: 150));
      if (!mounted) return;
      await SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
      if (!mounted) return;
      Navigator.of(context).pop<Uint8List>(bytes);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error is CameraException
            ? 'Gagal mengambil foto: ${error.description ?? error.code}'
            : error.toString().replaceFirst('Bad state: ', '');
        _capturing = false;
      });
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    final camera = _cameraController;
    if (camera != null) unawaited(camera.dispose());
    unawaited(SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge));
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final camera = _cameraController;
    final padding = MediaQuery.of(context).padding;
    final ready = !_loading && _error == null;
    return PopScope(
      canPop: !_capturing,
      child: Scaffold(
        backgroundColor: Colors.black,
        extendBodyBehindAppBar: true,
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          elevation: 0,
          foregroundColor: Colors.white,
          leading: IconButton(
            tooltip: 'Kembali',
            onPressed: _capturing ? null : () => Navigator.of(context).maybePop(),
            icon: const Icon(Icons.close_rounded),
          ),
          title: Text(_facing == 'front' ? 'Kamera depan' : 'Kamera belakang'),
        ),
        body: Stack(
          fit: StackFit.expand,
          children: [
            if (_error != null)
              _buildError()
            else if (_loading || camera == null || !camera.value.isInitialized)
              const Center(child: CircularProgressIndicator(color: Colors.white))
            else
              _buildPreview(camera),
            if (ready) _buildSwitchButton(padding),
            if (ready) _buildCaptureHint(padding),
            AnimatedOpacity(
              opacity: _capturing ? 1 : 0,
              duration: const Duration(milliseconds: 100),
              child: const ColoredBox(color: Colors.white),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPreview(CameraController camera) {
    final aspect = camera.value.aspectRatio;
    // FittedBox + a box matching the sensor aspect ratio fills the whole screen
    // the way the WhatsApp camera does, cropping the overflow instead of
    // letterboxing the preview.
    if (!aspect.isFinite || aspect <= 0) {
      return const ColoredBox(color: Colors.black);
    }
    return GestureDetector(
      onTap: _capture,
      behavior: HitTestBehavior.opaque,
      child: SizedBox.expand(
        child: FittedBox(
          fit: BoxFit.cover,
          child: SizedBox(
            width: 1000,
            height: 1000 / aspect,
            child: CameraPreview(camera),
          ),
        ),
      ),
    );
  }

  Widget _buildSwitchButton(EdgeInsets padding) {
    return Positioned(
      top: padding.top + kToolbarHeight + 12,
      right: 12,
      child: Container(
        decoration: const BoxDecoration(
          color: Color(0x66000000),
          shape: BoxShape.circle,
        ),
        child: IconButton(
          tooltip: 'Ganti kamera',
          onPressed: _switchCamera,
          color: Colors.white,
          icon: const Icon(Icons.cameraswitch_rounded, size: 26),
        ),
      ),
    );
  }

  Widget _buildCaptureHint(EdgeInsets padding) {
    return Positioned(
      bottom: padding.bottom + 28,
      left: 0,
      right: 0,
      child: Center(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          decoration: BoxDecoration(
            color: const Color(0x66000000),
            borderRadius: BorderRadius.circular(20),
          ),
          child: const Text(
            'Ketuk layar untuk mengambil foto',
            style: TextStyle(color: Colors.white, fontSize: 13),
          ),
        ),
      ),
    );
  }

  Widget _buildError() {
    return Center(
      child: Padding(
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
      ),
    );
  }
}