/// Canonical avatar expressions and their Indonesian aliases, for the
/// MCP `avatar_expression` tool. The avatar shows an expression from
/// the server's `llm` message `emotion` field automatically, but the
/// LLM has no way to set one on demand — when the user asks to "try
/// the laughing expression", the LLM could only say it was "not
/// available on this device", because there was no tool to call. This
/// module gives the LLM that tool: it normalizes the requested name
/// (English or Indonesian) to one of the 21 bundled emotion names, and
/// the controller sets [XiaozhiController.emotion] so the on-screen
/// avatar (3D or Live2D) shows it.
class AvatarExpression {
  AvatarExpression._();

  /// Canonical expression names (the 21 bundled emotions, identical to
  /// [EmotionService.bundled]).
  static const List<String> names = <String>[
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

  /// Indonesian (and common English) aliases for the canonical names.
  /// Keys are lowercase, trimmed, with spaces/underscores removed, so
  /// "tertawa", "Tertawa", and "tertawa_keras" all match.
  static const Map<String, String> _aliases = <String, String>{
    // Laughing / happy.
    'laughing': 'laughing',
    'tertawa': 'laughing',
    'tertawakeras': 'laughing',
    'ketawa': 'laughing',
    'ngakak': 'laughing',
    'laugh': 'laughing',
    'funny': 'funny',
    'lucu': 'funny',
    'happy': 'happy',
    'senang': 'happy',
    'gembira': 'happy',
    'bahagia': 'happy',
    // Sad / crying.
    'sad': 'sad',
    'sedih': 'sad',
    'crying': 'crying',
    'menangis': 'crying',
    'nangis': 'crying',
    // Angry.
    'angry': 'angry',
    'marah': 'angry',
    'kesal': 'angry',
    // Surprised / shocked.
    'surprised': 'surprised',
    'terkejut': 'surprised',
    'kaget': 'surprised',
    'shocked': 'shocked',
    'terkejutsekali': 'shocked',
    // Thinking / confused.
    'thinking': 'thinking',
    'berpikir': 'thinking',
    'mikir': 'thinking',
    'confused': 'confused',
    'bingung': 'confused',
    // Love / kiss.
    'loving': 'loving',
    'sayang': 'loving',
    'cinta': 'loving',
    'kissy': 'kissy',
    'cium': 'kissy',
    // Cool / confident / relaxed.
    'cool': 'cool',
    'keren': 'cool',
    'confident': 'confident',
    'percayaDiri': 'confident',
    'percayadiri': 'confident',
    'relaxed': 'relaxed',
    'santai': 'relaxed',
    'rileks': 'relaxed',
    // Sleepy / silly / embarrassed / delicious.
    'sleepy': 'sleepy',
    'ngantuk': 'sleepy',
    'silly': 'silly',
    'konyol': 'silly',
    'embarrassed': 'embarrassed',
    'malu': 'embarrassed',
    'delicious': 'delicious',
    'enak': 'delicious',
    'lezat': 'delicious',
    // Neutral / winking.
    'neutral': 'neutral',
    'netral': 'neutral',
    'biasa': 'neutral',
    'winking': 'winking',
    'kedip': 'winking',
    'mengedip': 'winking',
  };

  /// Normalize [raw] to a canonical expression name, or null when it
  /// is not a known expression or alias.
  static String? normalize(String raw) {
    final key = raw
        .trim()
        .toLowerCase()
        .replaceAll(RegExp(r'[\s_]'), '');
    if (key.isEmpty) return null;
    if (names.contains(key)) return key;
    return _aliases[key];
  }
}
