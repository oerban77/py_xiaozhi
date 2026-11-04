import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/live2d_characters.dart';

void main() {
  test('default character is Haru', () {
    expect(Live2dCharacters.defaultModelUrl, contains('haru'));
    expect(
      Live2dCharacters.presetForUrl(Live2dCharacters.defaultModelUrl)?.name,
      'Haru',
    );
  });

  test('every preset is a unique https model3.json URL', () {
    final urls = <String>{};
    final names = <String>{};
    for (final preset in Live2dCharacters.presets) {
      expect(preset.url, startsWith('https://'));
      expect(preset.url, endsWith('.model3.json'));
      expect(urls.add(preset.url), isTrue);
      expect(names.add(preset.name), isTrue);
    }
    expect(Live2dCharacters.presets.length, greaterThanOrEqualTo(2));
  });

  test('unknown URL and name are not presets', () {
    expect(
      Live2dCharacters.presetForUrl('https://example.com/Other.model3.json'),
      isNull,
    );
    expect(Live2dCharacters.presetForName('Tidak Ada'), isNull);
  });
}
