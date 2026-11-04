/// Live2D character presets for the home-screen avatar, kept separate from
/// [Live2DAvatar] so the list can be unit-tested without a WebView.
///
/// Every preset is a `.model3.json` (Cubism 3) model served from a public
/// CDN with its textures/motions next to it, so the viewer resolves the
/// model's relative file references the same way it does for the default
/// Haru greeter. Haru (greeter) is the default: it is the model that has
/// already been proven on-device. The other four are the official Live2D
/// Cubism sample characters (Hiyori, Mao, Natori, Ren), whose model files
/// were verified to exist on the CDN (Version 3, standard LipSync and
/// EyeBlink parameters) before being listed here. A custom URL typed in
/// Settings is used verbatim and is not part of this list.
class Live2dCharacter {
  const Live2dCharacter({required this.name, required this.url});

  /// Display name shown in Settings.
  final String name;

  /// Full URL of the character's `.model3.json`.
  final String url;
}

class Live2dCharacters {
  /// Default character: Haru greeter (already proven on-device).
  static const String defaultModelUrl =
      'https://cdn.jsdelivr.net/gh/guansss/pixi-live2d-display/test/assets/haru/haru_greeter_t03.model3.json';

  /// Characters offered in Settings, in display order.
  static const List<Live2dCharacter> presets = <Live2dCharacter>[
    Live2dCharacter(name: 'Haru', url: defaultModelUrl),
    Live2dCharacter(
      name: 'Hiyori',
      url:
          'https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@master/Samples/Resources/Hiyori/Hiyori.model3.json',
    ),
    Live2dCharacter(
      name: 'Mao',
      url:
          'https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@master/Samples/Resources/Mao/Mao.model3.json',
    ),
    Live2dCharacter(
      name: 'Natori',
      url:
          'https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@master/Samples/Resources/Natori/Natori.model3.json',
    ),
    Live2dCharacter(
      name: 'Ren',
      url:
          'https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@master/Samples/Resources/Ren/Ren.model3.json',
    ),
  ];

  /// The preset whose URL matches [url], or null for a custom URL.
  static Live2dCharacter? presetForUrl(String url) {
    for (final preset in presets) {
      if (preset.url == url) return preset;
    }
    return null;
  }

  /// The preset with the given display [name], or null.
  static Live2dCharacter? presetForName(String name) {
    for (final preset in presets) {
      if (preset.name == name) return preset;
    }
    return null;
  }
}
