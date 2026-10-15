import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';

/// Oscilloscope-style waveform that visualizes live audio level (mic or TTS).
///
/// Mirrors the desktop GUI's animated emoji reactions: while the mic is active
/// or the assistant speaks, a scrolling waveform reacts to the audio RMS level.
/// The trace is a continuous line (not a bar chart), matching the oscilloscope
/// look the reference image uses for the same purpose.
class AudioLevelVisualizer extends StatefulWidget {
  const AudioLevelVisualizer({
    super.key,
    required this.audioLevelStream,
    required this.deviceState,
    required this.emotion,
    this.barCount = 24,
    this.maxHeight = 36,
    this.minSamples = 160,
  });

  final Stream<double> audioLevelStream;
  final int deviceState;
  final String emotion;
  final int barCount;
  final double maxHeight;
  final int minSamples;

  @override
  State<AudioLevelVisualizer> createState() => _AudioLevelVisualizerState();
}

class _AudioLevelVisualizerState extends State<AudioLevelVisualizer>
    with SingleTickerProviderStateMixin {
  final List<double> _samples = [];
  StreamSubscription<double>? _subscription;
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    // Pre-fill with silence so the line starts flat instead of empty.
    _samples.addAll(List.filled(widget.minSamples, 0.0));
    _subscription = widget.audioLevelStream.listen(_onLevel);
    // Ticks drive the high-frequency jitter so the trace keeps moving even
    // between audio frames (otherwise a silent stretch would freeze it).
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat();
  }

  @override
  void dispose() {
    _subscription?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onLevel(double level) {
    if (!mounted) return;
    setState(() {
      _samples.add(level.clamp(0.0, 1.0));
      if (_samples.length > widget.minSamples * 2) {
        _samples.removeRange(0, _samples.length - widget.minSamples);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      height: widget.maxHeight,
      color: Colors.black,
      child: CustomPaint(
        painter: _WaveformPainter(
          samples: List.unmodifiable(_samples),
          maxHeight: widget.maxHeight,
          animation: _controller,
        ),
      ),
    );
  }
}

class _WaveformPainter extends CustomPainter {
  _WaveformPainter({
    required this.samples,
    required this.maxHeight,
    required Animation<double> animation,
  }) : animation = animation, super(repaint: animation);

  final List<double> samples;
  final double maxHeight;
  final Animation<double> animation;

  static const _lineColor = Color(0xFF9FB0D0);

  @override
  void paint(Canvas canvas, Size size) {
    if (samples.isEmpty || size.width <= 0 || size.height <= 0) return;

    final paint = Paint()
      ..color = _lineColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.0
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    final path = Path();
    final n = samples.length;
    // One point per pixel column gives the dense, fuzzy trace seen in the
    // reference oscilloscope image (far more points than raw RMS samples).
    final int columns = max(2, size.width.floor());
    final double dx = size.width / columns;
    final double phase = animation.value;

    for (int i = 0; i <= columns; i++) {
      final x = i * dx;
      // Map the column across the visible sample window with linear
      // interpolation so the trace scrolls smoothly as new levels arrive.
      final double t = i / columns;
      final double samplePos = t * (n - 1);
      final int idx0 = samplePos.floor();
      final int idx1 = min(idx0 + 1, n - 1);
      final double frac = samplePos - idx0;
      final double level = samples[idx0] * (1 - frac) + samples[idx1] * frac;

      // Center vertically and scale by the RMS level.
      final double baseY = size.height / 2 + (level - 0.5) * size.height * 0.92;

      // High-frequency jitter, scaled by level, gives the fuzzy texture.
      final double noiseAmp = (0.05 + level * 0.10) * size.height;
      final double jitter = _jitter(i, phase) - 0.5;
      final double y = baseY + jitter * 2 * noiseAmp;

      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }

    canvas.drawPath(path, paint);
  }

  /// Deterministic pseudo-random in [0,1) that animates with [phase] so the
  /// jitter pattern keeps shifting between frames.
  double _jitter(int i, double phase) {
    final int seed = (phase * 1e6).floor();
    final int h = (i * 73856093) ^ (seed * 19349663) ^ (seed << 11);
    return (h & 0xFFFFFF) / 0xFFFFFF;
  }

  @override
  bool shouldRepaint(_WaveformPainter oldDelegate) =>
      oldDelegate.samples != samples;
}