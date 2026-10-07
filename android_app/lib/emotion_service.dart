/// Emotion resource resolver for the Android client.
///
/// Mirrors src/ui/gui/services/emotion_service.py: turns an emotion name coming
/// from the server (the `llm` message's `emotion` field) into something the UI
/// can render. The desktop GUI resolves names to file:// URLs and falls back to
/// `neutral`, then to the "😊" character (see EmotionDisplay.qml). Here we
/// resolve to an asset path instead, keeping the same fallback chain.
class EmotionService {
  EmotionService._();

  /// Extensions accepted by the Python client's _find_emotion_file.
  static const List<String> extensions = <String>[
    '.gif',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
  ];

  /// Emotion names bundled under android_app/assets/emojis (21 GIFs, identical to
  /// assets/emojis in the repository root).
  static const List<String> bundled = <String>[
    'angry',
    'confident',
    'confused',
    'cool',
    'crying',
    'delicious',
    'embarrassed',
    'funny',
    'happy',
    'kissy',
    'laughing',
    'loving',
    'neutral',
    'relaxed',
    'sad',
    'shocked',
    'silly',
    'sleepy',
    'surprised',
    'thinking',
    'winking',
  ];

  static final Map<String, String> _cache = <String, String>{};

  /// Emoji used when an emotion has no bundled asset at all, mirroring the
  /// "😊" final fallback in get_emotion_url.
  static const String fallbackEmoji = '😊';

  static String normalize(String? name) {
    if (name == null) return '';

    final cleaned = name.trim();
    if (cleaned.isEmpty) return '';

    final candidates = <String>{
      cleaned.toLowerCase(),
      cleaned.toLowerCase().replaceAll(RegExp(r'\.(gif|png|jpg|jpeg|webp)$'), ''),
      cleaned.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), ''),
    }..removeWhere((value) => value.isEmpty);

    for (final candidate in candidates) {
      if (bundled.contains(candidate)) {
        return candidate;
      }
    }

    return cleaned.toLowerCase();
  }

  /// True when [name] has a bundled animation. Used by the display widget to
  /// decide between an animated image and a plain emoji glyph.
  static bool hasAnimation(String? name) {
    final normalized = normalize(name);
    return normalized.isNotEmpty && bundled.contains(normalized);
  }

  /// Resolves [name] to an asset path (for example
  /// `assets/emojis/happy.gif`), falling back to `neutral` and then to
  /// [fallbackEmoji], exactly like EmotionService.get_emotion_url.
  static String resolve(String? name) {
    final key = normalize(name);
    if (key.isEmpty) {
      return fallbackEmoji;
    }

    final cached = _cache[key];
    if (cached != null) {
      return cached;
    }

    String resolved;
    if (hasAnimation(key)) {
      resolved = 'assets/emojis/$key${extensions.first}';
    } else {
      final fallbackKey = 'neutral';
      if (hasAnimation(fallbackKey)) {
        resolved = 'assets/emojis/$fallbackKey${extensions.first}';
      } else {
        resolved = fallbackEmoji;
      }
    }
    _cache[key] = resolved;
    return resolved;
  }
}
