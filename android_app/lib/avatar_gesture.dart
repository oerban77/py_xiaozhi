/// Avatar gestures: named movements the LLM can trigger through the MCP
/// `avatar_gesture` tool (and the user can trigger from the UI).
///
/// The same canonical names work on both avatars: the 3D avatar plays
/// them in the three.js page (`playGesture`, see avatar3d.html) and the
/// Live2D avatar plays its own version (built-in motions + parameter
/// animation, see live2d_avatar.dart). Names are Indonesian-first with
/// English aliases, matching how the user speaks to the assistant.
class AvatarGesture {
  /// Canonical gesture names the avatars understand.
  static const List<String> names = [
    'wave',
    'nod',
    'shake',
    'bow',
    'happy',
    'sad',
    'surprised',
    'angry',
    'think',
    'look_left',
    'look_right',
    'look_up',
    'look_down',
    'dance',
  ];

  /// Indonesian names and English aliases -> canonical name.
  static const Map<String, String> aliases = {
    'lambaian': 'wave',
    'angguk': 'nod',
    'geleng': 'shake',
    'membungkuk': 'bow',
    'senang': 'happy',
    'sedih': 'sad',
    'terkejut': 'surprised',
    'marah': 'angry',
    'berpikir': 'think',
    'lihat_kiri': 'look_left',
    'lihat_kanan': 'look_right',
    'lihat_atas': 'look_up',
    'lihat_bawah': 'look_down',
    'menari': 'dance',
    'lookleft': 'look_left',
    'lookright': 'look_right',
    'lookup': 'look_up',
    'lookdown': 'look_down',
  };

  /// Normalize a user/LLM gesture name to its canonical form, or null
  /// when the name is not a known gesture. Case-insensitive; spaces
  /// and dashes become underscores.
  static String? normalize(String raw) {
    final key = raw.trim().toLowerCase().replaceAll(RegExp(r'[\s-]+'), '_');
    if (key.isEmpty) return null;
    if (names.contains(key)) return key;
    return aliases[key];
  }
}
