import 'package:flutter/material.dart';

import 'emotion_service.dart';

/// Shows the assistant's current emotion, mirroring the desktop GUI's
/// EmotionDisplay.qml + the emotion area in windows/MainWindow.qml:
///
/// * a bundled animation (AnimatedImage in QML, Image.asset here — Flutter
///   plays GIFs natively) when the emotion has an asset,
/// * a large emoji glyph otherwise,
/// * a dimmed placeholder while idle.
class EmotionDisplay extends StatelessWidget {
  const EmotionDisplay({
    super.key,
    required this.emotion,
    this.size = 128.0,
  });

  /// Raw emotion name coming from the server (`llm` message's `emotion` field)
  /// or one of the controller's local states ("neutral" while idle).
  final String emotion;

  /// Animated image size, mirroring
  /// `Math.min(parent.width, parent.height) * 0.9` in EmotionDisplay.qml.
  final double size;

  @override
  Widget build(BuildContext context) {
    final resolved = EmotionService.resolve(emotion);
    final isAsset = resolved.startsWith('assets/');
    final dimension = size * 0.9;

    if (isAsset) {
      return SizedBox(
        width: size,
        height: size,
        child: Center(
          // Image.asset decodes and animates GIF frames automatically.
          child: Image.asset(
            resolved,
            width: dimension,
            height: dimension,
            fit: BoxFit.contain,
            gaplessPlayback: true,
            errorBuilder: (context, error, stackTrace) =>
                _emojiGlyph(resolved, dimension),
          ),
        ),
      );
    }

    // Emoji fallback (the "😊" path in EmotionDisplay.qml).
    return SizedBox(
      width: size,
      height: size,
      child: Center(child: _emojiGlyph(resolved, size * 0.6)),
    );
  }

  Text _emojiGlyph(String glyph, double fontSize) => Text(
        glyph,
        style: TextStyle(fontSize: fontSize),
        textAlign: TextAlign.center,
      );
}

/// Placeholder shown while the device is disconnected, mirroring the dimmed
/// "😊" at opacity 0.3 in EmotionDisplay.qml.
class EmotionPlaceholder extends StatelessWidget {
  const EmotionPlaceholder({super.key, this.size = 128.0});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: 0.3,
      child: Text(
        EmotionService.fallbackEmoji,
        style: TextStyle(fontSize: size * 0.6),
        textAlign: TextAlign.center,
      ),
    );
  }
}
