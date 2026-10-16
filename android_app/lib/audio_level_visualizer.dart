import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';

/// Centered oscilloscope waveform that visualizes live audio level (mic or TTS).
///
/// Mirrors the ChatGPT / Gemini style: a single continuous line that oscillates
/// in place (no scrolling), reacting to the audio RMS level. The background is
/// transparent so it blends with the chat composer.
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
  /// Smoothed audio level in [0, 1].
  double _level = 0.0;
  StreamSubscription<double>? _subscription;
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _subscription = widget.audioLevelStream.listen(_onLevel);
    // Ticks drive the animation so the waveform keeps oscillating.
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 800),
    )..repeat();
  }

  @override
  void dispose() {
    _subscription?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onLevel(double rawLevel) {
    if (!mounted) return;
    setState(() {
      // RMS of 16-bit PCM is typically 0.001–0.1 (0.1%–10% of full scale),
      // so the raw level looks flat on the oscilloscope. Apply a gain factor
      // and clamp to [0, 1] so the waveform actually moves.
      final double level = rawLevel.clamp(0.0, 1.0);
      final double amplified = (level * _kLevelGain).clamp(0.0, 1.0);
      _level = _level * 0.6 + amplified * 0.4;
    });
  }

  /// Gain applied to the raw RMS level before smoothing. 16-bit PCM RMS is
  /// typically 0.001–0.1, so a gain of ~10 makes the visualizer responsive
  /// without clipping at normal speaking volume.
  static const double _kLevelGain = 10.0;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      height: widget.maxHeight,
      child: CustomPaint(
        painter: _WaveformPainter(
          level: _level,
          animation: _controller,
        ),
      ),
    );
  }
}

/// Paints a centered oscilloscope waveform that oscillates in place.
class _WaveformPainter extends CustomPainter {
  _WaveformPainter({
    required this.level,
    required Animation<double> animation,
  }) : animation = animation, super(repaint: animation);

  final double level;
  final Animation<double> animation;

  /// Muted blue-grey matching the ChatGPT / Gemini composer waveform.
  static const _lineColor = Color(0xFF9FB0D0);

  @override
  void paint(Canvas canvas, Size size) {
    if (size.width <= 0 || size.height <= 0) return;

    final paint = Paint()
      ..color = _lineColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    final path = Path();
    final int columns = max(2, size.width.floor());
    final double dx = size.width / (columns - 1);
    final double phase = animation.value * 2 * pi;

    // Amplitude scales with audio level: at silence the line is nearly flat,
    // at full level it fills most of the available height.
    final double amplitude = level * size.height * 0.42;

    for (int i = 0; i < columns; i++) {
      final double x = i * dx;
      // Combine a few harmonics for a natural oscilloscope texture.
      final double t = i / columns;
      final double y = _wave(t, phase, amplitude, size.height);

      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }

    canvas.drawPath(path, paint);
  }

  /// Compute Y position for column at fraction [t] in [0,1].
  double _wave(double t, double phase, double amplitude, double height) {
    final double center = height / 2;
    // Fundamental + 2nd harmonic + subtle noise for organic feel.
    final double fundamental = sin(t * 2 * pi * 3 + phase);
    final double harmonic = sin(t * 2 * pi * 7 + phase * 1.7) * 0.3;
    final double noise = (_jitter(t, phase) - 0.5) * 0.2;
    return center + (fundamental + harmonic + noise) * amplitude;
  }

  /// Deterministic pseudo-random in [0,1) that animates with [phase].
  double _jitter(double t, double phase) {
    final int seed = (phase * 1e5).floor();
    final int h = ((t * 1000).floor() * 73856093) ^ (seed * 19349663);
    return (h & 0xFFFFFF) / 0xFFFFFF;
  }

  @override
  bool shouldRepaint(_WaveformPainter oldDelegate) =>
      oldDelegate.level != level;
}