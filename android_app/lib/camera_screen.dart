import 'dart:async';
import 'dart:convert';

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
///
/// Set [enableCapture] to false for a plain preview, e.g. the settings page,
/// where only the switch button should do anything and tapping the preview
/// must not take a photo.
class CameraScreen extends StatefulWidget {
  const CameraScreen({
    super.key,
    required this.lensDirection,
    this.onCameraFacingChanged,
    this.enableCapture = true,
  });

  final String lensDirection;

  /// Notified when the user flips the lens, so the caller keeps its own
  /// camera-facing state in sync with the persisted choice.
  final void Function(String facing)? onCameraFacingChanged;

  /// Whether tapping the preview takes a photo. Always leaves the switch
  /// button working, so the settings preview can still flip the lens.
  final bool enableCapture;

  @override
  State<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends State<CameraScreen> with WidgetsBindingObserver {
  CameraController? _cameraController;
  String? _error;
  bool _loading = true;
  bool _capturing = false;
  bool _initializing = false;
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
    // Guards against overlapping initialize() calls: the lifecycle observer
    // can fire "resumed" while a lens switch is still opening the new camera,
    // and a second initialize() would race the first for the camera service.
    // Gating on _loading instead would break the switch flow, which sets
    // _loading before calling this and would then bail out and never flip.
    if (_initializing) return;
    _initializing = true;
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
    } finally {
      _initializing = false;
    }
  }

  Future<void> _switchCamera() async {
    if (_initializing || _capturing) return;
    final next = _facing == 'front' ? 'back' : 'front';
    // Show the spinner right away so the preview does not look frozen while
    // the native camera service is being flipped.
    setState(() {
      _loading = true;
      _error = null;
    });
    // CameraRuntime.switchCamera reports whether the lens exists and persists
    // the choice to the same key take_photo reads.
    final decoded = jsonDecode(await CameraRuntime.switchCamera(next));
    if (decoded is! Map || decoded['success'] != true) {
      if (!mounted) return;
      final reason = decoded is Map
          ? '${decoded['reason'] ?? 'Kamera tidak dapat diganti.'}'
          : 'Kamera tidak dapat diganti.';
      // Keep the lens that is already running; the SnackBar explains why
      // nothing flipped instead of replacing a working preview with an error.
      setState(() => _loading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(reason)),
      );
      return;
    }
    widget.onCameraFacingChanged?.call(next);
    _facing = next;
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
            if (ready && widget.enableCapture) _buildCaptureHint(padding),
            // IgnorePointer keeps the invisible (opacity 0) flash from
            // swallowing taps, so the preview stays tappable between shots.
            IgnorePointer(
              ignoring: !_capturing,
              child: AnimatedOpacity(
                opacity: _capturing ? 1 : 0,
                duration: const Duration(milliseconds: 100),
                child: const ColoredBox(color: Colors.white),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPreview(CameraController camera) {
    // camera.value.previewSize is the sensor-native size, which is landscape on
    // phones (width > height). CameraPreview's own AspectRatio divides it as
    // width/height, so in portrait the box it wants is previewSize flipped to
    // portrait: height wide x height tall.
    final previewSize = camera.value.previewSize;
    if (previewSize == null ||
        previewSize.width <= 0 ||
        previewSize.height <= 0) {
      return const SizedBox.shrink();
    }

    return GestureDetector(
      onTap: widget.enableCapture ? _capture : null,
      behavior: HitTestBehavior.opaque,
      child: SizedBox.expand(
        child: FittedBox(
          fit: BoxFit.cover,
          // FittedBox leaves its child unconstrained, and CameraPreview's
          // internal AspectRatio cannot pick a size without a bounded
          // constraint, so the preview collapses and nothing is drawn. Give it
          // a real box first -- the sensor's portrait frame, in actual preview
          // pixels -- then let FittedBox scale it up to cover the screen the
          // way the WhatsApp camera does.
          child: SizedBox(
            width: previewSize.height,
            height: previewSize.height,
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